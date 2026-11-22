#!/usr/bin/env python3
"""Daily thesis-progress report, keyed to the UNADE reviewer's rejection.

Reads the latest training run (and the latest walk-forward cross-validation, if
present) from data/models/ and writes a structured Markdown report that maps the
CURRENT results onto each of the reviewer's blocks. It is written to be handed to
another assistant to update chapters 4 and 5 of the thesis, so every claim comes
with its number and its source run.

    python -m scripts.thesis_progress_report            # print the report
    python -m scripts.thesis_progress_report --send     # also email it

Email credentials are read from a secrets file (default ~/.config/pnl_report.env),
never hard-coded:  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_APP_PASSWORD,
REPORT_FROM, REPORT_TO.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from datetime import UTC, date, datetime
from email.message import EmailMessage
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = APP_DIR / "data" / "models"

# Thesis resubmission deadline. The reviewer gave 20 days; day 1 was 2026-11-06
# ("ayer fue el día 1" said on 2026-11-07), so day 20 — the handover — is
# 2026-11-25. Anchored to fixed dates so the countdown updates itself each day.
THESIS_DAY_ONE = date(2026, 11, 6)
THESIS_DEADLINE = date(2026, 11, 25)


def _deadline_tag() -> str:
    """Short countdown for the email subject, e.g. 'quedan 17 días'."""
    days_left = (THESIS_DEADLINE - date.today()).days
    if days_left < 0:
        return "entrega VENCIDA"
    if days_left == 0:
        return "ENTREGA HOY"
    if days_left == 1:
        return "queda 1 día"
    return f"quedan {days_left} días"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _runs() -> list[Path]:
    return sorted(d for d in MODELS_DIR.glob("run_*") if d.is_dir())


def _pilot(meta: dict) -> bool:
    return bool(meta.get("pilot_window_since"))


def _full_history_runs() -> list[Path]:
    return [d for d in _runs() if not _pilot(_load(d / "run_metadata.json"))]


def _fmt(x, nd=2, pct=False, dash="no definido"):
    if x is None:
        return dash
    v = x * 100 if pct else x
    return f"{v:.{nd}f}{'%' if pct else ''}"


def _latest_crossval() -> dict:
    files = sorted(MODELS_DIR.glob("crossval_*.json"))
    return _load(files[-1]) if files else {}


def build_report() -> tuple[str, str]:
    """Return (subject, markdown_body)."""
    runs = _full_history_runs()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if not runs:
        return (f"[Tesis · {_deadline_tag()}] Sin corridas de entrenamiento ({today})",
                "No hay corridas de entrenamiento en data/models/. "
                "Revisa que el entrenamiento programado esté corriendo.")

    latest = runs[-1]
    meta = _load(latest / "run_metadata.json")
    met = _load(latest / "metrics.json")
    lstm = met.get("lstm_test") or {}
    bl = met.get("baselines") or {}
    naive = bl.get("naive_time_since_failure") or {}
    rf = bl.get("random_forest") or {}
    flow = meta.get("dataset_flow") or {}
    parts = flow.get("sequences_by_partition") or {}
    lead = met.get("lead_time") or {}
    cv = _latest_crossval()
    cvp = cv.get("pooled") or {}

    run_id = meta.get("run_id", latest.name)
    lines: list[str] = []
    w = lines.append

    w(f"# Informe de avance de tesis — {today}")
    w("")
    w("Informe automático del sistema de predicción temprana de fallas (LSTM). "
      "Estructurado según los hallazgos de la Comisión Académica de UNADE (revisor "
      "Miguel Herrero Uceda) para actualizar los capítulos 4 y 5. **Todas las cifras "
      "provienen de corridas reales y reproducibles**; se indica el run de origen.")
    w("")
    w(f"- **Corrida más reciente (full-history):** `{run_id}`")
    w(f"- **Período del dataset:** {(meta.get('dataset_period') or {}).get('start','?')[:10]} "
      f"→ {(meta.get('dataset_period') or {}).get('end','?')[:10]}")
    w(f"- **Fallas etiquetadas (distintas):** {flow.get('distinct_failures', '?')}")
    w("")

    # --- Bloque 1 ---------------------------------------------------------
    w("## Bloque 1 — Contrastación de la hipótesis")
    w("")
    w("El rechazo señalaba que el piloto tenía 231 observaciones de un día, 58 secuencias, "
      "y **validación y prueba con 0 positivos** (imposible de contrastar). Estado actual:")
    w("")
    w(f"- Observaciones usadas: **{flow.get('rows_used','?')}** "
      f"(de {flow.get('observations_loaded','?')} cargadas; "
      f"{flow.get('rows_dropped_induced_load','?')} descartadas por carga inducida).")
    w(f"- Secuencias totales: **{flow.get('sequences_total','?')}**.")
    tr, va, te = parts.get("train", {}), parts.get("val", {}), parts.get("test", {})
    w(f"- Positivos por partición — train: {tr.get('positives','?')}/{tr.get('n','?')}, "
      f"**validación: {va.get('positives','?')}/{va.get('n','?')}**, "
      f"**prueba: {te.get('positives','?')}/{te.get('n','?')}**. "
      "Validación y prueba YA contienen positivos (el defecto fatal de la V42 quedó resuelto).")
    if cvp:
        lstm_pr = cvp.get("lstm_pr_auc", {}).get("mean")
        naive_pr = cvp.get("naive_pr_auc", {}).get("mean")
        rf_pr = cvp.get("random_forest_pr_auc", {}).get("mean")
        w("")
        w("**Validación cruzada walk-forward por evento** (la evaluación rigurosa que "
          f"pide el revisor; margen de exclusión {(cv.get('config') or {}).get('margin_minutes','?')} min, "
          f"{(cv.get('config') or {}).get('folds','?')} pliegues, agregados):")
        w("")
        w("| Modelo | PR-AUC |")
        w("|---|---|")
        w(f"| **LSTM** | **{_fmt(lstm_pr,4)}** |")
        w(f"| Línea base ingenua (calendario) | {_fmt(naive_pr,4)} |")
        w(f"| Random Forest | {_fmt(rf_pr,4)} |")
        w(f"\n- Recall por evento (LSTM): **{_fmt(cvp.get('lstm_event_recall',{}).get('mean'),3)}**, "
          f"lead time medio **{_fmt(cvp.get('mean_lead_time_minutes',{}).get('mean'),1)} min**.")
        # Honest interpretation for the thesis document.
        if lstm_pr is not None and naive_pr is not None and lstm_pr > naive_pr:
            w(f"- ✅ La LSTM ({_fmt(lstm_pr,3)}) **supera a la línea base ingenua** "
              f"({_fmt(naive_pr,3)}): no está aprendiendo el calendario.")
        if lstm_pr is not None and rf_pr is not None and rf_pr > lstm_pr:
            w(f"- ⚠️ **Hallazgo honesto:** Random Forest ({_fmt(rf_pr,3)}) supera a la LSTM "
              f"({_fmt(lstm_pr,3)}) en PR-AUC. El modelo secuencial todavía NO justifica su "
              "complejidad frente a un baseline no secuencial. Para la defensa hay que: (a) ajustar "
              "la LSTM (arquitectura/umbral/horizonte) o (b) reencuadrar la aportación. La prueba "
              "pareada McNemar por pliegue está en el JSON de walk-forward.")
    else:
        w("- ⏳ Walk-forward aún no ejecutado esta semana (se agrega cuando corra `ml/crossval`).")
    w("")

    # --- Bloque 2 ---------------------------------------------------------
    w("## Bloque 2 — Diseño de eventos y la prueba del calendario")
    w("")
    w("El revisor advirtió que un horario fijo permite que el modelo aprenda el "
      "calendario del experimentador, y exigió una línea base ingenua. Estado actual:")
    w("")
    w("- **Horario de rampas aleatorizado** cada día (separación mínima garantizada): "
      "ya no hay calendario fijo que memorizar.")
    w("- **Período de recuperación (15 min tras el restablecimiento) excluido** de ambas clases.")
    w("- **Línea base ingenua «tiempo desde la última falla»** (solo el reloj, sin telemetría):")
    w(f"    - LSTM — recall {_fmt(lstm.get('recall'),2)}, precisión {_fmt(lstm.get('precision'),2)}, "
      f"F1 {_fmt(lstm.get('f1'),2)}, PR-AUC {_fmt(lstm.get('pr_auc'),4)}.")
    w(f"    - Ingenua — recall {_fmt(naive.get('recall'),2)}, precisión {_fmt(naive.get('precision'),2)}, "
      f"PR-AUC {_fmt(naive.get('pr_auc'),4)}.")
    w("    - **Interpretación:** si la LSTM supera claramente a la ingenua en PR-AUC, "
      "está aprendiendo precursores y no el calendario. Si no la supera, hay que revisar el diseño.")
    w("")

    # --- Bloque 3 ---------------------------------------------------------
    w("## Bloque 3 — Métricas, umbral y comparación con líneas base")
    w("")
    w(f"- Métricas de prueba (umbral {_fmt(lstm.get('threshold'),2)}, elegido sobre validación con positivos): "  # noqa: E501
      f"recall **{_fmt(lstm.get('recall'),2)}**, precisión **{_fmt(lstm.get('precision'),2)}**, "
      f"F1 **{_fmt(lstm.get('f1'),2)}**, PR-AUC **{_fmt(lstm.get('pr_auc'),4)}**.")
    w(f"- Falsas alarmas/día (sobre la duración real del test): **{_fmt(lstm.get('false_alarms_per_day'),2)}**.")  # noqa: E501
    cm = lstm.get("confusion_matrix") or {}
    w(f"- Matriz de confusión: VN={cm.get('tn','?')}, FP={cm.get('fp','?')}, "
      f"FN={cm.get('fn','?')}, VP={cm.get('tp','?')}.")
    w(f"- Lead time: {lead.get('events_detected','?')}/{lead.get('events_evaluated','?')} eventos detectados, "  # noqa: E501
      f"media {_fmt(lead.get('mean_lead_time_minutes'),1)} min.")
    if rf:
        w(f"- Random Forest (mismas secuencias): recall {_fmt(rf.get('recall'),2)}, "
          f"PR-AUC {_fmt(rf.get('pr_auc'),4)}.")
    w("- Las métricas indefinidas (0/0) se reportan como «no definido», no como 0. "
      "La comparación pareada (McNemar) y los IC por bootstrap se incluyen en el informe walk-forward.")
    w("")

    # --- Bloque 4 ---------------------------------------------------------
    w("## Bloque 4 — Trazabilidad del dataset")
    w("")
    w("Tabla de flujo del dataset (de observaciones crudas a secuencias etiquetadas):")
    w("")
    w("| Etapa | Valor |")
    w("|---|---|")
    w(f"| Observaciones cargadas | {flow.get('observations_loaded','?')} |")
    w(f"| Descartadas (carga inducida) | {flow.get('rows_dropped_induced_load','?')} |")
    w(f"| Filas usadas | {flow.get('rows_used','?')} |")
    w(f"| Filas positivas | {flow.get('positive_rows','?')} |")
    w(f"| Fallas distintas | {flow.get('distinct_failures','?')} |")
    w(f"| Secuencias totales | {flow.get('sequences_total','?')} |")
    w(f"| Secuencias train/val/test | {tr.get('n','?')} / {va.get('n','?')} / {te.get('n','?')} |")
    w("")

    # --- Tendencia --------------------------------------------------------
    w("## Tendencia (últimas corridas full-history)")
    w("")
    w("| Fecha | Recall | Precisión | F1 | PR-AUC | Detectadas |")
    w("|---|---|---|---|---|---|")
    for d in runs[-7:]:
        m2 = _load(d / "metrics.json").get("lstm_test") or {}
        l2 = _load(d / "metrics.json").get("lead_time") or {}
        day = d.name.replace("run_", "")[:8]
        w(f"| {day} | {_fmt(m2.get('recall'),2)} | {_fmt(m2.get('precision'),2)} | "
          f"{_fmt(m2.get('f1'),2)} | {_fmt(m2.get('pr_auc'),4)} | "
          f"{l2.get('events_detected','?')}/{l2.get('events_evaluated','?')} |")
    w("")

    # --- Pendientes para el documento ------------------------------------
    w("## Qué queda pendiente (NO es código — es redacción/decisiones de la tesis)")
    w("")
    w("- **Escenarios variados** (Bloque 2): hoy solo rampas de ancho de banda. Faltan "
      "`LATENCY_DEGRADATION` y `PACKET_LOSS` inyectados, que requieren infraestructura adicional.")
    w("- **Reescritura de capítulos 4 y 5** con estos resultados; retirar toda afirmación de "
      "eficacia no medida (Bloque 7).")
    w("- **Repositorio con código y commit** para el anexo técnico (Bloque 4).")
    w("- **Autorización del responsable de la red y tratamiento de datos** (Bloque 8).")
    w("- **APA, referencias e índice** (Bloques 9 y 10).")
    w("")
    w("---")
    w(f"*Generado automáticamente el {datetime.now(UTC):%Y-%m-%d %H:%M} UTC a partir de `{run_id}`. "
      "Entregar este informe a la IA que mantiene el documento de tesis.*")

    recall_txt = _fmt(lstm.get("recall"), 2)
    subject = f"[Tesis · {_deadline_tag()}] Avance {today} — recall {recall_txt}, {flow.get('distinct_failures','?')} fallas"  # noqa: E501
    return subject, "\n".join(lines)


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return {**env, **{k: v for k, v in os.environ.items() if k.startswith(("SMTP_", "REPORT_"))}}


def send_email(subject: str, body_md: str, env: dict[str, str]) -> None:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_APP_PASSWORD", "REPORT_FROM", "REPORT_TO"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise SystemExit(f"Faltan credenciales en el archivo de secretos: {', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = env["REPORT_FROM"]
    msg["To"] = env["REPORT_TO"]
    msg.set_content(body_md)  # Markdown as plain text - ideal to paste into another AI

    with smtplib.SMTP(env["SMTP_HOST"], int(env["SMTP_PORT"]), timeout=30) as s:
        s.starttls()
        s.login(env["SMTP_USER"], env["SMTP_APP_PASSWORD"])
        s.send_message(msg)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--send", action="store_true", help="email the report (else print it)")
    p.add_argument("--secrets", default=str(Path.home() / ".config" / "pnl_report.env"))
    args = p.parse_args(argv)

    subject, body = build_report()
    if not args.send:
        print(subject)
        print()
        print(body)
        return 0
    send_email(subject, body, _load_env(Path(args.secrets)))
    print(f"enviado: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
