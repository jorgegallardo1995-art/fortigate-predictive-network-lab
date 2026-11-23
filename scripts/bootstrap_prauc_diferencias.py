#!/usr/bin/env python3
"""Event-level bootstrap of PR-AUC DIFFERENCES for Section 4.9 of the thesis.

Método
------
Para cada régimen se comparan, en el conjunto de PRUEBA, las PR-AUC de:
  - LSTM  (modelo secuencial)
  - Random Forest (línea base no secuencial)
  - regla de umbral reactiva (latencia>=150 OR jitter>=50 OR pérdida>=5, umbrales 'critical')
  - calendario ORÁCULO (puntaje = minutos desde la última falla, con el MEJOR signo sobre el test;
    es una cota superior optimista del calendario, se declara como tal).
Se reportan tres diferencias: LSTM - oráculo, LSTM - umbral, RF - LSTM.

Definición de "evento" y asignación de secuencias
-------------------------------------------------
Un evento = una racha CONTIGUA de ventanas positivas (pre-falla) en el test (misma definición que
ml/evaluate.lead_times). Se excluye la racha de borde cuyo T_f (instante de falla del experimento)
cae después del fin del test (misma regla que el test limpio de 13 eventos). Cada secuencia del
conjunto de evaluación se asigna al evento cuyo CENTRO temporal es el más cercano; el bootstrap
remuestrea EVENTOS completos (no secuencias), B=3000, semilla 42, y recalcula las diferencias de
PR-AUC sobre las secuencias de los eventos remuestreados. IC = percentiles 2,5 y 97,5.

Regímenes
---------
  - Régimen limpio (--since 2026-11-07): 832 secuencias de prueba, 13 eventos, umbral 0,60.
    Modelo docs/modelo/run_20261122T144340Z.
  - Histórico aleatorizado: subconjunto del test full-history con end >= 2026-11-07 (5.709 de 8.395
    secuencias), 74 eventos, umbral 0,70. Modelo docs/modelo/run_20261122T144431Z.

Uso:  python -m scripts.bootstrap_prauc_diferencias --db snap.db
Salidas: docs/resultados/bootstrap_prauc.{json,csv}, bootstrap_prauc_tabla.md, y los hashes
SHA-256 de los vectores de predicción usados.
"""
from __future__ import annotations
import argparse, json, csv, hashlib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from ml.config import DEFAULT_CONFIG as CFG
from ml.baselines import random_forest_baseline, minutes_since_last_failure, threshold_baseline
from ml.preprocessing import SequenceScaler
from ml.variables import input_columns
from scripts.audit_baseline_lead import build_split, events  # reutiliza helpers commiteados

THR_RULE = {"latency": {"critical": 150.0}, "jitter": {"critical": 50.0}, "packet_loss": {"critical": 5.0}}
B = 3000
SEED = 42
RAND_SINCE = pd.Timestamp("2026-11-07T00:00:00")

THESIS = {  # valores reportados en la tesis (para comparar, NO para forzar)
    "limpio": {"RF - LSTM": (0.194, 0.095, 0.284), "LSTM - oraculo": (-0.113, -0.375, 0.065),
               "LSTM - umbral": (0.040, -0.031, 0.153)},
    "aleatorizado": {"LSTM - oraculo": (0.220, 0.130, 0.302), "LSTM - umbral": (0.287, 0.222, 0.356),
                     "RF - LSTM": (0.053, -0.002, 0.105)},
}


def etn_ns(ts):
    return pd.to_datetime(np.asarray(ts), utc=True).tz_convert("UTC").tz_localize(None).astype("int64").to_numpy()


def sha256(arr):
    return hashlib.sha256(np.ascontiguousarray(arr, dtype=np.float64).tobytes()).hexdigest()


def run_regime(db, model_path, since, randomized_only, label):
    import tensorflow as tf
    sp, fi, exp = build_split(db, since=since)
    ETN = etn_ns(sp.test.end_timestamps)
    sc = SequenceScaler().fit(sp.train.X)
    model = tf.keras.models.load_model(model_path, compile=False)
    pL = model.predict(sc.transform(sp.test.X), verbose=0).ravel()
    _, pR = random_forest_baseline(sp.train, sp.test, seed=CFG.seed)
    tr = threshold_baseline(sp.test.X, input_columns(), THR_RULE).astype(float)
    x = minutes_since_last_failure(sp.test.end_timestamps, fi).astype(float)

    evs, _ = events(sp, exp, randomized_only)
    cn = np.datetime64(RAND_SINCE.to_datetime64(), "ns").astype("int64")
    ridx = np.where(ETN >= cn)[0] if randomized_only else np.arange(len(ETN))
    y = sp.test.y.astype(int)
    yv = y[ridx]
    xo = (x if average_precision_score(yv, x[ridx]) >= average_precision_score(yv, -x[ridx]) else -x)

    centres = np.array([(ETN[s] + ETN[e]) // 2 for s, e, _ in evs])
    assign = np.array([int(np.argmin(np.abs(centres - ETN[t]))) for t in ridx])
    groups = [ridx[assign == g] for g in range(len(evs))]

    scores = {"LSTM": pL, "RF": pR, "umbral": tr, "oraculo": xo}
    pairs = {"LSTM - oraculo": ("LSTM", "oraculo"), "LSTM - umbral": ("LSTM", "umbral"),
             "RF - LSTM": ("RF", "LSTM")}
    rng = np.random.default_rng(SEED)
    out = {}
    for name, (a, b) in pairs.items():
        d = []
        for _ in range(B):
            pick = rng.integers(0, len(groups), len(groups))
            gi = np.concatenate([groups[g] for g in pick]); yy = y[gi]
            if yy.sum() == 0:
                continue
            d.append(average_precision_score(yy, scores[a][gi]) - average_precision_score(yy, scores[b][gi]))
        d = np.array(d)
        out[name] = {"media": round(float(d.mean()), 3),
                     "ic_low": round(float(np.percentile(d, 2.5)), 3),
                     "ic_high": round(float(np.percentile(d, 97.5)), 3),
                     "p_mayor_0": round(float(np.mean(d > 0)), 3),
                     "estado": "CONFIRMA" if np.percentile(d, 2.5) > 0 else
                               ("RECHAZA" if np.percentile(d, 97.5) < 0 else "INCONCLUSO")}
    meta = {"label": label, "eventos": len(evs), "n_test": int(len(yv)),
            "prevalencia": round(float(yv.mean()), 3),
            "hashes_sha256": {k: sha256(v[ridx]) for k, v in scores.items()}}
    return out, meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="snap.db")
    args = p.parse_args(argv)
    res = {}
    res["limpio"], m1 = run_regime(args.db, "docs/modelo/run_20261122T144340Z/model.keras",
                                   "2026-11-07", False, "Régimen limpio (13 eventos)")
    res["aleatorizado"], m2 = run_regime(args.db, "docs/modelo/run_20261122T144431Z/model.keras",
                                         None, True, "Histórico aleatorizado (74 eventos)")
    meta = {"limpio": m1, "aleatorizado": m2, "B": B, "seed": SEED}

    with open("docs/resultados/bootstrap_prauc.json", "w") as f:
        json.dump({"resultados": res, "meta": meta, "tesis": THESIS}, f, indent=2, ensure_ascii=False)
    with open("docs/resultados/bootstrap_prauc.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["regimen", "comparacion", "media", "ic_low", "ic_high", "p>0", "estado",
                    "tesis_media", "tesis_low", "tesis_high", "|dif_media|"])
        for reg in ("limpio", "aleatorizado"):
            for name, r in res[reg].items():
                t = THESIS[reg].get(name, (None, None, None))
                dif = round(abs(r["media"] - t[0]), 3) if t[0] is not None else ""
                w.writerow([reg, name, r["media"], r["ic_low"], r["ic_high"], r["p_mayor_0"],
                            r["estado"], t[0], t[1], t[2], dif])
    # tabla markdown
    lines = ["# Bootstrap de diferencias de PR-AUC (Sección 4.9) — reproducido vs tesis\n",
             f"B={B}, semilla={SEED}, remuestreo por evento. Reproducible: "
             "`python -m scripts.bootstrap_prauc_diferencias --db snap.db`.\n"]
    for reg, meta_r in (("limpio", m1), ("aleatorizado", m2)):
        lines.append(f"\n## {meta_r['label']} — {meta_r['eventos']} eventos, "
                     f"n_test={meta_r['n_test']}, prevalencia={meta_r['prevalencia']}\n")
        lines.append("| comparación | reproducido (IC95%) | tesis (IC95%) | |Δmedia| | estado |")
        lines.append("|---|---|---|---|---|")
        for name, r in res[reg].items():
            t = THESIS[reg].get(name)
            dif = abs(r["media"] - t[0])
            flag = "" if dif <= 0.005 else " ⚠️>0,005"
            lines.append(f"| {name} | {r['media']:+.3f} [{r['ic_low']:+.3f}, {r['ic_high']:+.3f}] | "
                         f"{t[0]:+.3f} [{t[1]:+.3f}, {t[2]:+.3f}] | {dif:.3f}{flag} | {r['estado']} |")
    with open("docs/resultados/bootstrap_prauc_tabla.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(lines))
    print("\nHashes SHA-256 de los vectores de predicción:")
    for reg, mm in (("limpio", m1), ("aleatorizado", m2)):
        print(f"  [{reg}]")
        for k, h in mm["hashes_sha256"].items():
            print(f"    {k:9} {h}")


if __name__ == "__main__":
    main()
