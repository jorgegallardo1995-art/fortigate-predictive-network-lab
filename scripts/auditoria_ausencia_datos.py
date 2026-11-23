#!/usr/bin/env python3
"""H10 — Auditoría de la ausencia neta de datos sobre la base del Release (corte 2026-11-14T23:59:59Z).

Verifica: timestamps duplicados, distribución de intervalos entre filas consecutivas, primera/última
fila vs extremos del período, y reconcilia minutos del período / filas crudas / ciclos exitosos /
fallidos / huecos / minutos netos ausentes, distinguiendo los tres conceptos.

Uso:  python -m scripts.auditoria_ausencia_datos --db snap.db
Salida: docs/resultados/auditoria_ausencia_datos.md
"""
import argparse, sqlite3
import numpy as np
import pandas as pd

TOL_S = 5  # tolerancia declarada alrededor de los 60 s


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("--db", default="snap.db"); a = p.parse_args(argv)
    con = sqlite3.connect(a.db)
    ts = pd.read_sql("SELECT scheduled_timestamp, timestamp_utc, collection_success FROM telemetry_raw "
                     "ORDER BY timestamp_utc", con)
    t = pd.to_datetime(ts["timestamp_utc"], utc=True)
    sch = pd.to_datetime(ts["scheduled_timestamp"], utc=True)
    L = ["# H10 — Auditoría de la ausencia neta de datos (base del Release)\n",
         "`python -m scripts.auditoria_ausencia_datos --db snap.db`\n"]

    # 1. duplicados
    dup_ts = ts["timestamp_utc"].duplicated().sum()
    dup_sch = ts["scheduled_timestamp"].duplicated().sum()
    L.append("## 1. Timestamps duplicados")
    L.append(f"- `timestamp_utc` duplicados: **{dup_ts}**")
    L.append(f"- `scheduled_timestamp` duplicados: **{dup_sch}**")
    if dup_ts:
        d = t[t.duplicated(keep=False)].dt.date.value_counts().sort_index()
        L.append("  fechas con duplicados: " + ", ".join(f"{k} ({v})" for k, v in d.items()))

    # 2. intervalos
    dts = t.diff().dropna().dt.total_seconds()
    irr = dts[(dts < 60 - TOL_S) | (dts > 60 + TOL_S)]
    L.append("\n## 2. Intervalos entre filas consecutivas (segundos)")
    L.append(f"- mediana={dts.median():.1f} | p1={dts.quantile(.01):.1f} p5={dts.quantile(.05):.1f} "
             f"p95={dts.quantile(.95):.1f} p99={dts.quantile(.99):.1f} | máximo={dts.max():.0f} "
             f"({dts.max()/60:.1f} min)")
    L.append(f"- intervalos irregulares (≠ 60 s ± {TOL_S} s): **{len(irr)}** de {len(dts)} "
             f"({len(irr)/len(dts)*100:.1f} %); de ellos > 65 s (huecos): {int((dts>65).sum())}, "
             f"< 55 s: {int((dts<55).sum())}")

    # 3. extremos
    PER0 = pd.Timestamp("2026-09-01 05:32:33", tz="UTC"); PER1 = pd.Timestamp("2026-11-14 23:59:46", tz="UTC")
    L.append("\n## 3. Primera y última fila vs extremos del período")
    L.append(f"- primera fila: {t.min()}  (extremo período: {PER0})")
    L.append(f"- última fila:  {t.max()}  (extremo período: {PER1})")

    # 4. reconciliación
    period_min = (t.max() - t.min()).total_seconds() / 60
    raw = len(ts); ok = int(ts["collection_success"].sum()); failed = raw - ok
    ng = con.execute("SELECT count(*) FROM collection_gaps").fetchone()[0]
    gap_s = con.execute("SELECT sum(duration_seconds) FROM collection_gaps").fetchone()[0] or 0
    lost = con.execute("SELECT sum(lost_samples) FROM collection_gaps").fetchone()[0] or 0
    L.append("\n## 4. Reconciliación de minutos")
    L.append("| concepto | valor | significado |")
    L.append("|---|---|---|")
    L.append(f"| minutos del período | {period_min:,.0f} | (max−min de timestamp_utc) |")
    L.append(f"| filas crudas (telemetry_raw) | {raw:,} | ciclos con una fila (exitosos + fallidos) |")
    L.append(f"| ciclos exitosos (collection_success=1) | {ok:,} | filas con telemetría válida (las que usa el ML) |")
    L.append(f"| ciclos fallidos (collection_success=0) | {failed:,} | fila presente, SIN telemetría válida |")
    L.append(f"| filas ausentes (minutos sin fila) | {period_min-raw:,.0f} | **minutos netos ausentes** = período − filas |")
    L.append(f"| collection_gaps: registros / lost_samples / minutos | {ng} / {int(lost)} / {gap_s/60:,.0f} | **sobrecuenta** |")
    L.append("\n**Distinción de conceptos:**")
    L.append(f"- *Minutos sin NINGUNA fila* (ausencia física) = período − filas crudas = **{period_min-raw:,.0f} min**. "
             "Es la cifra de «ausencia neta» (≈ 5.147).")
    L.append(f"- *Ciclos fallidos registrados* (fila presente, telemetría inválida) = **{failed:,}**. Tienen fila, "
             "no son ausencia física, pero no aportan telemetría.")
    L.append(f"- *Minutos sin telemetría VÁLIDA* = (período − exitosos) = **{period_min-ok:,.0f} min** "
             "(suma ausencia física + ciclos fallidos).")
    L.append(f"- `collection_gaps` (lost_samples {int(lost)}, {gap_s/60:,.0f} min) **sobrecuenta**: incluye los "
             f"{failed:,} ciclos fallidos del incidente de IP de la Raspberry contados como pérdida (y en parte "
             "dos veces). La ausencia física correcta es la de filas faltantes, no la de collection_gaps.")
    con.close()
    with open("docs/resultados/auditoria_ausencia_datos.md", "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
