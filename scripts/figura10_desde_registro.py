#!/usr/bin/env python3
"""Regenera una figura del registro de experimentos (Figura 10 de la tesis) a partir de
docs/resultados/registro_experimentos_20261108.csv.

La Figura 10 se respalda en los 506 eventos inducidos CON instante de falla (T_f) registrado
(de 591 experimentos exportados el 2026-11-08). Este script filtra a esos 506 y grafica los
eventos por día y escenario a lo largo del periodo experimental.

NOTA: el aspecto visual exacto de la Figura 10 impresa en la tesis no se verifica aquí (el
documento no está en el repositorio); lo que se publica y se verifica es el REGISTRO de 506
eventos que la respalda y un script reproducible que genera una figura desde él.

Uso:  python -m scripts.figura10_desde_registro
Salida: docs/graficos/figura10_registro_eventos.png
"""
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REG = "docs/resultados/registro_experimentos_20261108.csv"
OUT = "docs/graficos/figura10_registro_eventos.png"


def main():
    d = pd.read_csv(REG)
    ev = d[d["failure_instant_Tf_utc"].notna()].copy()
    assert len(ev) == 506, f"Se esperaban 506 eventos con T_f, hay {len(ev)}"
    ev["dia"] = pd.to_datetime(ev["failure_instant_Tf_utc"], utc=True).dt.date
    piv = ev.pivot_table(index="dia", columns="scenario", values="experiment_id",
                         aggfunc="count", fill_value=0).sort_index()
    ax = piv.plot(kind="bar", stacked=True, figsize=(11, 4.2), width=0.9,
                  color={"CONGESTION": "#2b6cb0", "WAN_FAILURE": "#b03a2e",
                         "LATENCY_DEGRADATION": "#1b7a43"})
    ax.set_xlabel("día (UTC)"); ax.set_ylabel("eventos con T_f")
    ax.set_title(f"Registro de experimentos — {len(ev)} eventos inducidos con T_f (corte 2026-11-08)")
    ax.legend(title="escenario", fontsize=8)
    plt.tight_layout(); plt.savefig(OUT, dpi=160); plt.close()
    print(f"Figura generada: {OUT}")
    print(f"Total eventos con T_f: {len(ev)}  | por escenario: {ev['scenario'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
