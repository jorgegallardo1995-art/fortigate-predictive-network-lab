#!/usr/bin/env python3
"""Monitor de suficiencia de datos para el ENTRENAMIENTO LIMPIO del régimen
aleatorizado (--since 2026-11-07).

Reconstruye el split temporal usando SOLO datos aleatorizados y decide si ya hay
positivos suficientes en val/test y cobertura de eventos por escenario. Cuando se
cumple el umbral, ENVÍA UN CORREO (una sola vez) avisando que se puede lanzar
`scripts/train_randomized_regime.sh 2026-11-07`.

Uso:
  check_clean_regime.py                 # imprime el estado (no envía nada)
  check_clean_regime.py --send          # modo launchd: envía correo SOLO si LISTO (idempotente)
  check_clean_regime.py --confirm-setup # envía UN correo de confirmación de que el monitor quedó activo

Credenciales SMTP: ~/.config/pnl_report.env (reutiliza las del reporte diario).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "scripts"))

SINCE = "2026-11-07"
# --- Umbral de suficiencia (transparente, se incluye en el correo) ---
GATE = {
    "val_pos_min": 150,          # positivos en validación (evaluación no degenerada)
    "test_pos_min": 150,         # positivos en prueba (~10 eventos independientes)
    "congestion_events_min": 30, # meta orientativa de la comisión: 30-40/escenario
    "latency_events_min": 25,
}

MONITOR_DIR = APP_DIR / "data" / "monitor"
SENTINEL = MONITOR_DIR / "clean_regime_ready.sent"
LOG = MONITOR_DIR / "clean_regime_check.log"
# Snapshot fresco que deja el entrenamiento 3x/día; si falta o está viejo, se refresca.
TRAIN_SNAP = Path.home() / "fortigate_rpi_archive" / "work" / "train_snapshot.db"
OWN_SNAP = MONITOR_DIR / "snap.db"
RPI_USER = os.environ.get("RPI_USER", "aaa")
RPI_HOSTS = os.environ.get("RPI_HOSTS", "10.254.0.10 100.123.191.35").split()
RPI_DIR = os.environ.get("RPI_DIR", "fortigate_predictive_collector")


def log(msg: str) -> None:
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(UTC).isoformat(timespec='seconds')} | {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _age_hours(p: Path) -> float:
    if not p.exists():
        return 1e9
    return (datetime.now().timestamp() - p.stat().st_mtime) / 3600.0


def resolve_snapshot() -> Path | None:
    """Reuse the training snapshot if fresh (<18h); else pull our own copy."""
    if TRAIN_SNAP.exists() and _age_hours(TRAIN_SNAP) < 18:
        log(f"usando snapshot de entrenamiento ({_age_hours(TRAIN_SNAP):.1f}h): {TRAIN_SNAP}")
        return TRAIN_SNAP
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    for host in RPI_HOSTS:
        ssh = f"{RPI_USER}@{host}"
        try:
            subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                 "-o", "StrictHostKeyChecking=accept-new", ssh,
                 f"flock -n /tmp/pnl_snapshot.lock -c \"sqlite3 '{RPI_DIR}/data/fortigate.db' "
                 f"'PRAGMA wal_checkpoint(PASSIVE);'\""],
                check=True, timeout=60, capture_output=True,
            )
            r = subprocess.run(
                ["rsync", "-a", "--timeout=180",
                 "-e", "ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new",
                 f"{ssh}:{RPI_DIR}/data/fortigate.db", str(OWN_SNAP)],
                timeout=240, capture_output=True,
            )
            if r.returncode == 0 and OWN_SNAP.exists():
                log(f"snapshot propio actualizado desde {host}")
                return OWN_SNAP
        except Exception as e:  # noqa: BLE001
            log(f"snapshot desde {host} falló: {e}")
            continue
    if OWN_SNAP.exists():
        log(f"usando snapshot propio previo ({_age_hours(OWN_SNAP):.1f}h)")
        return OWN_SNAP
    return None


def compute(db: str) -> dict:
    import pandas as pd
    from ml.config import DEFAULT_CONFIG as cfg, FAILURE_EVENT_TYPES
    from ml.data_loading import (
        load_dataset, load_experiments, load_network_events, load_speed_tests,
    )
    from ml.induced import build_induced_plan, drop_excluded_rows, predictive_failures
    from ml.labeling import apply_labels
    from ml.preprocessing import temporal_split
    from ml.segments import continuous_segments
    from ml.sequences import build_sequences
    from ml.variables import input_columns

    df = load_dataset(db, "REAL", since=SINCE)
    exp = load_experiments(db)
    ev = load_network_events(db, FAILURE_EVENT_TYPES)
    st = load_speed_tests(db, "REAL")
    plan = build_induced_plan(exp, st)
    df = drop_excluded_rows(df, plan.exclude_windows)
    failures = predictive_failures(exp, ev, [], plan)
    labeled = apply_labels(df, failures, cfg.horizon_minutes)
    segs = continuous_segments(labeled, cfg.gap_tolerance_seconds)
    seqset = build_sequences(segs, input_columns(), cfg.window_steps)

    out: dict = {"sequences": len(seqset), "positives": int(seqset.y.sum())}
    if len(seqset) >= 50:
        sp = temporal_split(seqset, cfg.train_frac, cfg.val_frac)
        out.update(
            train_pos=int(sp.train.y.sum()), val_pos=int(sp.val.y.sum()),
            test_pos=int(sp.test.y.sum()), train_n=len(sp.train),
            val_n=len(sp.val), test_n=len(sp.test),
            span_end=str(pd.Timestamp(seqset.end_timestamps.max())),
        )
    # eventos por escenario desde SINCE
    e = exp.copy()
    e["start_timestamp"] = pd.to_datetime(e["start_timestamp"], utc=True, errors="coerce")
    recent = e[e["start_timestamp"] >= pd.Timestamp(SINCE, tz="UTC")]
    vc = recent["scenario"].value_counts().to_dict()
    out["congestion_events"] = int(vc.get("CONGESTION", 0))
    out["latency_events"] = int(vc.get("LATENCY_DEGRADATION", 0))
    out["loss_events"] = int(vc.get("PACKET_LOSS", 0))
    return out


def is_ready(s: dict) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    for key, label in (("val_pos", "val_pos_min"), ("test_pos", "test_pos_min")):
        val = s.get(key, 0)
        need = GATE[label]
        mark = "✅" if val >= need else "⏳"
        if val < need:
            ok = False
        reasons.append(f"  {mark} {key} = {val} (meta ≥ {need})")
    for key, label in (("congestion_events", "congestion_events_min"),
                       ("latency_events", "latency_events_min")):
        val = s.get(key, 0)
        need = GATE[label]
        mark = "✅" if val >= need else "⏳"
        if val < need:
            ok = False
        reasons.append(f"  {mark} {key} = {val} (meta ≥ {need})")
    return ok, reasons


def status_text(s: dict, ready: bool, reasons: list[str]) -> str:
    head = "LISTO ✅" if ready else "ACUMULANDO ⏳"
    lines = [
        f"Monitor de datos para el ENTRENAMIENTO LIMPIO (régimen aleatorizado, desde {SINCE})",
        f"Estado: {head}",
        "",
        f"Secuencias (solo aleatorizado): {s.get('sequences','-')}  |  positivos: {s.get('positives','-')}",
        f"Split temporal -> train pos {s.get('train_pos','-')} / "
        f"val pos {s.get('val_pos','-')} / test pos {s.get('test_pos','-')}",
        f"Eventos desde {SINCE}: CONGESTION {s.get('congestion_events','-')} / "
        f"LATENCY {s.get('latency_events','-')} / LOSS {s.get('loss_events','-')}",
        f"Datos hasta: {s.get('span_end','-')}",
        "",
        "Criterio de suficiencia:",
        *reasons,
    ]
    if ready:
        lines += [
            "",
            "➡️  Ya se puede lanzar el entrenamiento limpio:",
            "    cd ~/fortigate_predictive_collector",
            f"    scripts/train_randomized_regime.sh {SINCE}",
            "",
            "Eso reentrena y hace walk-forward SOLO sobre datos aleatorizados, y recalcula",
            "la naive-oráculo de calendario para la comparación definitiva de la comisión.",
        ]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--send", action="store_true",
                   help="modo launchd: enviar correo SOLO si LISTO (una vez)")
    p.add_argument("--confirm-setup", action="store_true",
                   help="enviar un correo de confirmación de que el monitor quedó activo")
    p.add_argument("--secrets", default=str(Path.home() / ".config" / "pnl_report.env"))
    args = p.parse_args(argv)

    db = resolve_snapshot()
    if db is None:
        log("ERROR: no se pudo obtener snapshot de la BD; se omite este chequeo")
        return 1
    s = compute(str(db))
    ready, reasons = is_ready(s)
    body = status_text(s, ready, reasons)
    log(f"check -> ready={ready} val_pos={s.get('val_pos')} test_pos={s.get('test_pos')} "
        f"cong={s.get('congestion_events')} lat={s.get('latency_events')}")

    need_email = args.confirm_setup or (args.send and ready and not SENTINEL.exists())
    if not args.send and not args.confirm_setup:
        print("\n" + body)
        return 0

    if need_email:
        from thesis_progress_report import _load_env, send_email
        env = _load_env(Path(args.secrets))
        if args.confirm_setup:
            subject = "[Tesis] Monitor activo — aviso automático cuando los datos estén listos"
            intro = ("Este es un correo de confirmación: el monitor quedó instalado y revisará "
                     "los datos una vez al día. Te avisaré automáticamente por este medio cuando "
                     "el régimen aleatorizado tenga datos suficientes para el entrenamiento limpio.\n\n")
            send_email(subject, intro + body, env)
            log("correo de confirmación de instalación enviado")
        else:  # ready
            subject = "[Tesis] LISTO ✅ — datos suficientes para el entrenamiento limpio"
            send_email(subject, body, env)
            MONITOR_DIR.mkdir(parents=True, exist_ok=True)
            SENTINEL.write_text(datetime.now(UTC).isoformat())
            log("correo de LISTO enviado; sentinel creado (no se repetirá)")
    elif args.send and ready and SENTINEL.exists():
        log("LISTO pero ya se notificó antes (sentinel presente); no se reenvía")
    else:
        log("aún no listo; sin correo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
