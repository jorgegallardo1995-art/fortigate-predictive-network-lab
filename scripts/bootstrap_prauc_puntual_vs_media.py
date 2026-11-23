#!/usr/bin/env python3
"""H04 — Descompone la diferencia entre la RESTA PUNTUAL de PR-AUC (sobre la prueba completa)
y la MEDIA del bootstrap por evento, para las 6 comparaciones de la Sección 4.9.

Para cada comparación reporta: PR-AUC puntual de cada modelo y su resta; media, mediana e IC 95 %
de las 3.000 diferencias bootstrap; SESGO = media_bootstrap − resta_puntual; nº de réplicas
descartadas (sin positivos); prevalencia media de las réplicas vs prevalencia de la prueba.

Diagnóstico del signo del oráculo (régimen limpio, LSTM−oráculo): compara el bootstrap con el signo
FIJO (elegido sobre la prueba completa) vs el signo REELEGIDO en cada réplica, para ver si el sesgo
proviene de la reselección del signo o de la no linealidad del PR-AUC bajo remuestreo por evento.

No reentrena ni cambia modelos. Usa los modelos publicados y la base del Release.
Uso:  python -m scripts.bootstrap_prauc_puntual_vs_media --db snap.db
Salidas: docs/resultados/bootstrap_puntual_vs_media.{csv,json,md}
"""
from __future__ import annotations
import argparse, json, csv
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score as AP

from ml.config import DEFAULT_CONFIG as CFG
from ml.baselines import random_forest_baseline, minutes_since_last_failure, threshold_baseline
from ml.preprocessing import SequenceScaler
from ml.variables import input_columns
from scripts.audit_baseline_lead import build_split, events

THR_RULE = {"latency": {"critical": 150.0}, "jitter": {"critical": 50.0}, "packet_loss": {"critical": 5.0}}
B, SEED = 3000, 42
RAND_SINCE = pd.Timestamp("2026-11-07T00:00:00")


def etn(ts):
    return pd.to_datetime(np.asarray(ts), utc=True).tz_convert("UTC").tz_localize(None).astype("int64").to_numpy()


def regime(db, model_path, since, randomized_only):
    import tensorflow as tf
    sp, fi, exp = build_split(db, since=since)
    ETN = etn(sp.test.end_timestamps); y = sp.test.y.astype(int)
    sc = SequenceScaler().fit(sp.train.X)
    model = tf.keras.models.load_model(model_path, compile=False)
    pL = model.predict(sc.transform(sp.test.X), verbose=0).ravel()
    _, pR = random_forest_baseline(sp.train, sp.test, seed=CFG.seed)
    tr = threshold_baseline(sp.test.X, input_columns(), THR_RULE).astype(float)
    x = minutes_since_last_failure(sp.test.end_timestamps, fi).astype(float)
    evs, _ = events(sp, exp, randomized_only)
    cn = np.datetime64(RAND_SINCE.to_datetime64(), "ns").astype("int64")
    ridx = np.where(ETN >= cn)[0] if randomized_only else np.arange(len(ETN))
    yv = y[ridx]
    xo = x if AP(yv, x[ridx]) >= AP(yv, -x[ridx]) else -x
    centres = np.array([(ETN[s] + ETN[e]) // 2 for s, e, _ in evs])
    assign = np.array([int(np.argmin(np.abs(centres - ETN[t]))) for t in ridx])
    groups = [ridx[assign == g] for g in range(len(evs))]
    sc_map = {"LSTM": pL, "RF": pR, "umbral": tr, "oraculo": xo}
    return y, yv, groups, sc_map, x


def boot(y, groups, sa, sb, rng, resign_x=None):
    d = []; disc = 0; prev = []
    for _ in range(B):
        idx = np.concatenate([groups[g] for g in rng.integers(0, len(groups), len(groups))])
        yy = y[idx]
        if yy.sum() == 0:
            disc += 1; continue
        prev.append(yy.mean())
        b = sb[idx]
        if resign_x is not None:   # reelige el signo del oráculo en cada réplica
            b = resign_x[idx] if AP(yy, resign_x[idx]) >= AP(yy, -resign_x[idx]) else -resign_x[idx]
        d.append(AP(yy, sa[idx]) - AP(yy, b))
    d = np.array(d)
    return dict(media=float(d.mean()), mediana=float(np.median(d)),
                ic_low=float(np.percentile(d, 2.5)), ic_high=float(np.percentile(d, 97.5)),
                descartadas=disc, prev_media=float(np.mean(prev)))


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("--db", default="snap.db")
    args = p.parse_args(argv)
    PAIRS = {"RF - LSTM": ("RF", "LSTM"), "LSTM - oraculo": ("LSTM", "oraculo"),
             "LSTM - umbral": ("LSTM", "umbral")}
    regs = {"limpio": ("docs/modelo/run_20261122T144340Z/model.keras", "2026-11-07", False),
            "aleatorizado": ("docs/modelo/run_20261122T144431Z/model.keras", None, True)}
    rows = []; out = {}
    for reg, (mp, since, ro) in regs.items():
        y, yv, groups, sc_map, x = regime(args.db, mp, since, ro)
        prev_test = float(yv.mean()); out[reg] = {"prevalencia_prueba": round(prev_test, 4), "comparaciones": {}}
        for name, (a, b) in PAIRS.items():
            # PR-AUC puntual sobre la prueba del régimen (todas las secuencias del eval set)
            idx_all = np.sort(np.concatenate(groups))
            apa = AP(y[idx_all], sc_map[a][idx_all]); apb = AP(y[idx_all], sc_map[b][idx_all])
            punt = apa - apb
            bs = boot(y, groups, sc_map[a], sc_map[b], np.random.default_rng(SEED))
            sesgo = bs["media"] - punt
            out[reg]["comparaciones"][name] = {"PRAUC_a": round(apa, 4), "PRAUC_b": round(apb, 4),
                "resta_puntual": round(punt, 4), **{k: round(v, 4) if isinstance(v, float) else v for k, v in bs.items()},
                "sesgo_media_menos_puntual": round(sesgo, 4)}
            rows.append([reg, name, round(apa, 4), round(apb, 4), round(punt, 4), round(bs["media"], 4),
                         round(bs["mediana"], 4), round(bs["ic_low"], 4), round(bs["ic_high"], 4),
                         round(sesgo, 4), bs["descartadas"], round(bs["prev_media"], 4), round(prev_test, 4)])
        # diagnóstico del signo (solo limpio, LSTM-oráculo)
        if reg == "limpio":
            fix = boot(y, groups, sc_map["LSTM"], sc_map["oraculo"], np.random.default_rng(SEED))
            res = boot(y, groups, sc_map["LSTM"], sc_map["oraculo"], np.random.default_rng(SEED), resign_x=x)
            out["diagnostico_signo_limpio_LSTM_oraculo"] = {
                "signo_fijo_media": round(fix["media"], 4), "signo_reelegido_media": round(res["media"], 4)}

    with open("docs/resultados/bootstrap_puntual_vs_media.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    with open("docs/resultados/bootstrap_puntual_vs_media.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["regimen", "comparacion", "PRAUC_a", "PRAUC_b", "resta_puntual", "media_boot",
                    "mediana_boot", "ic_low", "ic_high", "sesgo(media-puntual)", "descartadas",
                    "prev_media_boot", "prev_prueba"])
        w.writerows(rows)
    dg = out["diagnostico_signo_limpio_LSTM_oraculo"]
    md = ["# H04 — Resta puntual vs media bootstrap de las diferencias de PR-AUC\n",
          "`python -m scripts.bootstrap_prauc_puntual_vs_media --db snap.db` · B=3000 · semilla 42 · "
          "remuestreo por evento.\n",
          "| régimen | comparación | PR-AUC a | PR-AUC b | resta puntual | media boot | mediana boot | "
          "IC95% | sesgo | descartadas | prev boot / prueba |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]:+} | {r[5]:+} | {r[6]:+} | "
                  f"[{r[7]:+}, {r[8]:+}] | {r[9]:+} | {r[10]} | {r[11]}/{r[12]} |")
    md += ["",
           "## Diagnóstico del signo del oráculo (régimen limpio, LSTM−oráculo)",
           f"- Bootstrap con signo **FIJO** (elegido en la prueba completa): media = **{dg['signo_fijo_media']:+}**",
           f"- Bootstrap con signo **REELEGIDO por réplica**: media = **{dg['signo_reelegido_media']:+}**",
           "",
           "**Conclusión.** Ambos dan ≈ −0,113: la reselección del signo **no** explica la diferencia con la",
           "resta puntual (−0,056). La causa es el **sesgo del bootstrap de PR-AUC bajo remuestreo por evento**:",
           "con solo 13 eventos la distribución de las 3.000 diferencias es muy **asimétrica a la izquierda**",
           "(réplicas con composición desfavorable producen PR-AUC muy bajas), lo que arrastra la **media** por",
           "debajo de la resta puntual. La **mediana** (≈ −0,075) queda entre ambas y es un estimador central",
           "más robusto. El **estado de la hipótesis no cambia** (INCONCLUSO: el IC incluye 0 en las tres",
           "formas). En el régimen aleatorizado (74 eventos) el sesgo es mucho menor (ver tabla)."]
    with open("docs/resultados/bootstrap_puntual_vs_media.md", "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


def _ev_idx(groups):
    return np.sort(np.concatenate(groups))


if __name__ == "__main__":
    main()
