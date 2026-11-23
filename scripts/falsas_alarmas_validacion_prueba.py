#!/usr/bin/env python3
"""Tarea 5 — Reproduce la cifra «2 falsas alarmas/día en validación -> 36 por día en prueba».

Modelo publicado run_20261122T144340Z (régimen limpio). FA/día = falsos positivos / días
transcurridos (convención por tiempo real del conjunto). Reporta FA/día en validación y en prueba
para: (a) el umbral de operación 0,60; (b) el menor umbral que da <= 2 FA/día EN VALIDACIÓN, y su
FA/día en prueba (transferencia del punto de operación).

Uso:  python -m scripts.falsas_alarmas_validacion_prueba --db snap.db
Salida: docs/resultados/falsas_alarmas_validacion_prueba.md
"""
import argparse
import numpy as np
import pandas as pd
from sklearn.metrics import recall_score
from ml.preprocessing import SequenceScaler
from scripts.audit_baseline_lead import build_split


def days(ts):
    t = pd.to_datetime(np.asarray(ts), utc=True)
    return max((t.max() - t.min()).total_seconds() / 86400, 1e-9)


def fa_day(proba, y, thr, d):
    fp = int(((proba >= thr) & (y == 0)).sum())
    return fp, fp / d, recall_score(y, (proba >= thr).astype(int), zero_division=0)


def main(argv=None):
    import tensorflow as tf
    p = argparse.ArgumentParser(); p.add_argument("--db", default="snap.db"); a = p.parse_args(argv)
    sp, _, _ = build_split(a.db, since="2026-11-07")
    sc = SequenceScaler().fit(sp.train.X)
    m = tf.keras.models.load_model("docs/modelo/run_20261122T144340Z/model.keras", compile=False)
    pva = m.predict(sc.transform(sp.val.X), verbose=0).ravel(); yva = sp.val.y.astype(int)
    pte = m.predict(sc.transform(sp.test.X), verbose=0).ravel(); yte = sp.test.y.astype(int)
    dv, dt = days(sp.val.end_timestamps), days(sp.test.end_timestamps)

    L = ["# Tarea 5 — Falsas alarmas: validación -> prueba (run_20261122T144340Z)\n",
         "`python -m scripts.falsas_alarmas_validacion_prueba --db snap.db`\n",
         f"Días de validación = {dv:.3f} | días de prueba = {dt:.3f}\n"]
    # (a) umbral 0,60
    fpv, fav, rv = fa_day(pva, yva, 0.60, dv); fpt, fat, rt = fa_day(pte, yte, 0.60, dt)
    L.append("## (a) Umbral de operación 0,60")
    L.append(f"- validación: FP={fpv}, FA/día={fav:.1f}, recall={rv:.3f}")
    L.append(f"- prueba:     FP={fpt}, FA/día={fat:.1f}, recall={rt:.3f}")
    # (b) umbral que da <=2 FA/día en validación
    target = 2.0; thr_sel = None
    for thr in np.round(np.arange(0.30, 0.991, 0.01), 2):
        if fa_day(pva, yva, thr, dv)[1] <= target:
            thr_sel = thr; break
    L.append(f"\n## (b) Umbral elegido en VALIDACIÓN para <= {target:.0f} FA/día")
    if thr_sel is None:
        L.append("- NO VERIFICADO: ningún umbral en [0,30; 0,99] baja de 2 FA/día en validación.")
    else:
        fpv2, fav2, rv2 = fa_day(pva, yva, thr_sel, dv); fpt2, fat2, rt2 = fa_day(pte, yte, thr_sel, dt)
        L.append(f"- umbral = **{thr_sel:.2f}**")
        L.append(f"- validación: FP={fpv2}, FA/día=**{fav2:.1f}**, recall={rv2:.3f}")
        L.append(f"- prueba:     FP={fpt2}, FA/día=**{fat2:.1f}**, recall={rt2:.3f}")
        L.append(f"\n**Resultado:** un umbral calibrado a ≤2 FA/día en validación produce **{fat2:.0f} FA/día en "
                 "prueba** → el punto de operación NO transfiere (el umbral se fija solo en validación, nunca en prueba).")
    with open("docs/resultados/falsas_alarmas_validacion_prueba.md", "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
