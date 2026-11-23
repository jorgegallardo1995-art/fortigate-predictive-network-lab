#!/usr/bin/env python3
"""Reproducible committee audit: (1) time-since-last-failure naive baseline and
(2) lead-time confidence intervals, both with event-level bootstrap.

Event = a contiguous run of positive (pre-failure) windows in the test set. The
boundary run whose failure instant T_f falls after the test cutoff is excluded
(same rule as the 13-event clean test), so the randomised test has 74 events.

Lead is measured with the CORRECTED definition: the first alert (proba >= threshold)
within the ramp window [T_f - 6 min, T_f]; T_f is the experiment's failure instant.
Non-detected events: contribute to the detection rate (as a miss) but are EXCLUDED
from the lead mean/median (they have no lead). The paired RF-LSTM lead difference
uses only events detected by BOTH models.

Thresholds are the operating points selected by maximising F1 on VALIDATION ONLY
(ml/evaluate.select_threshold), never on test. The naive baseline learns its sign on
TRAIN via logistic regression (ml/baselines.time_since_failure_baseline), never on test.

Usage:
    python -m scripts.audit_baseline_lead --db snap.db
"""
from __future__ import annotations
import argparse
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from ml.config import DEFAULT_CONFIG as CFG, FAILURE_EVENT_TYPES
from ml.data_loading import (load_dataset, load_experiments, load_network_events,
                             load_speed_tests)
from ml.induced import build_induced_plan, drop_excluded_rows, predictive_failures
from ml.labeling import apply_labels
from ml.segments import continuous_segments
from ml.variables import input_columns
from ml.sequences import build_sequences
from ml.preprocessing import temporal_split, SequenceScaler
from ml.baselines import random_forest_baseline, time_since_failure_baseline
from ml.evaluate import select_threshold

UNTIL = "2026-11-14T23:59:59Z"
RAND_SINCE = pd.Timestamp("2026-11-07T00:00:00")
RAMP_MIN = 6                      # congestion ramp starts 6 min before T_f
MIN = 60_000_000_000             # ns per minute
B = 3000
SEED = 42


def build_split(db, since=None):
    df = load_dataset(db, "REAL", since=since, until=UNTIL)
    exp = load_experiments(db); ev = load_network_events(db, FAILURE_EVENT_TYPES)
    st = load_speed_tests(db, "REAL")
    plan = build_induced_plan(exp, st); df = drop_excluded_rows(df, plan.exclude_windows)
    fails = predictive_failures(exp, ev, [], plan)
    ucut = pd.Timestamp(UNTIL)
    fails = [f for f in fails if pd.Timestamp(f.failure_timestamp).tz_convert("UTC") <= ucut]
    labeled = apply_labels(df, fails, CFG.horizon_minutes)
    segs = continuous_segments(labeled, CFG.gap_tolerance_seconds)
    ss = build_sequences(segs, input_columns(), CFG.window_steps)
    sp = temporal_split(ss, CFG.train_frac, CFG.val_frac)
    fi = [f.failure_timestamp for f in fails]
    return sp, fi, exp


def etn_ns(ts):
    return pd.to_datetime(np.asarray(ts), utc=True).tz_convert("UTC").tz_localize(None).astype("int64").to_numpy()


def events(sp, exp, randomized_only):
    """Contiguous positive runs in the test; return list of (start_idx,end_idx,T_f_ns),
    excluding the boundary run (T_f after test end) and, if randomized_only, runs ending
    before 7-nov. Also returns ETN(ns) of the test."""
    y = sp.test.y.astype(int); ETN = etn_ns(sp.test.end_timestamps); tend = ETN.max()
    ex = exp.copy(); ex["ft"] = pd.to_datetime(ex["failure_timestamp"], utc=True, errors="coerce")
    ftns = np.sort(ex.dropna(subset=["ft"])["ft"].dt.tz_localize(None).astype("int64").to_numpy())
    cn = np.datetime64(RAND_SINCE.to_datetime64(), "ns").astype("int64")
    out = []; k = 0; n = len(y)
    while k < n:
        if y[k] == 1:
            j = k
            while j + 1 < n and y[j + 1] == 1:
                j += 1
            if (not randomized_only) or ETN[j] >= cn:
                tf = ftns[np.argmin(np.abs(ftns - ETN[j]))]
                if tf <= tend:                       # exclude boundary (T_f past test end)
                    out.append((k, j, int(tf)))
            k = j + 1
        else:
            k += 1
    return out, ETN


def event_groups(evs, ETN, restrict_idx=None):
    """Assign each (optionally restricted) test index to its nearest event centre."""
    idx = np.arange(len(ETN)) if restrict_idx is None else restrict_idx
    centres = np.array([(ETN[s] + ETN[e]) // 2 for s, e, _ in evs])
    assign = np.array([int(np.argmin(np.abs(centres - ETN[t]))) for t in idx])
    return [idx[assign == g] for g in range(len(evs))]


def boot_diff_ap(groups, y, pa, pb, rng):
    d = []
    for _ in range(B):
        pick = rng.integers(0, len(groups), len(groups))
        gi = np.concatenate([groups[g] for g in pick]); yy = y[gi]
        if yy.sum() == 0:
            continue
        d.append(average_precision_score(yy, pa[gi]) - average_precision_score(yy, pb[gi]))
    d = np.array(d); lo, hi = np.percentile(d, 2.5), np.percentile(d, 97.5)
    state = "CONFIRMA" if lo > 0 else ("RECHAZA" if hi < 0 else "INCONCLUSO")
    return d.mean(), lo, hi, float(np.mean(d > 0)), state


def corrected_lead(proba, thr, s, e, tf, ETN):
    """First alert within [T_f-6min, T_f]; returns (detected, lead_min or None)."""
    lo = tf - RAMP_MIN * MIN
    for i in range(s, e + 1):
        if proba[i] >= thr and lo <= ETN[i] <= tf:
            return True, (tf - ETN[i]) / MIN
    return False, None


def boot_rate(flags, rng):
    f = np.array(flags, float); n = len(f)
    bs = [f[rng.integers(0, n, n)].mean() for _ in range(B)]
    return f.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5)


def boot_stat(vals, rng, stat):
    v = np.array(vals, float); n = len(v)
    bs = [stat(v[rng.integers(0, n, n)]) for _ in range(B)]
    return stat(v), np.percentile(bs, 2.5), np.percentile(bs, 97.5)


def analyse(db, model_path, since, label, randomized_only):
    import tensorflow as tf
    sp, fi, exp = build_split(db, since=since)
    y = sp.test.y.astype(int)
    sc = SequenceScaler().fit(sp.train.X)
    model = tf.keras.models.load_model(model_path, compile=False)
    pL = model.predict(sc.transform(sp.test.X), verbose=0).ravel()
    pLva = model.predict(sc.transform(sp.val.X), verbose=0).ravel()
    _, pR = random_forest_baseline(sp.train, sp.test, seed=CFG.seed)
    _, pRva = random_forest_baseline(sp.train, sp.val, seed=CFG.seed)
    _, pN = time_since_failure_baseline(sp.train, sp.test, fi, seed=CFG.seed)  # sign on TRAIN
    evs, ETN = events(sp, exp, randomized_only)
    rng = np.random.default_rng(SEED)

    # restrict test indices to the randomized subset for the full-history case
    cn = np.datetime64(RAND_SINCE.to_datetime64(), "ns").astype("int64")
    ridx = np.where(ETN >= cn)[0] if randomized_only else None
    yeval = y[ridx] if ridx is not None else y
    sl = (lambda a: a[ridx]) if ridx is not None else (lambda a: a)
    prev = yeval.mean()

    print(f"\n===== {label} | eventos={len(evs)} | n_test={len(yeval)} prevalencia={prev:.3f} =====")
    print("### Tarea 1 — naive 'tiempo desde la última falla' (signo en TRAIN) ###")
    print(f"  LSTM  PR-AUC={average_precision_score(yeval,sl(pL)):.3f}  ROC-AUC={roc_auc_score(yeval,sl(pL)):.3f}")
    print(f"  naive PR-AUC={average_precision_score(yeval,sl(pN)):.3f}  ROC-AUC={roc_auc_score(yeval,sl(pN)):.3f}")
    groups = event_groups(evs, ETN, restrict_idx=ridx)
    m, lo, hi, p, st = boot_diff_ap(groups, y, pL, pN, np.random.default_rng(SEED))
    print(f"  LSTM - naive: {m:+.3f} IC95%[{lo:+.3f},{hi:+.3f}] P(>0)={p:.2f} -> {st}")

    print("### Tarea 2 — lead corregido [T_f-6,T_f], IC por evento ###")
    tL = select_threshold(sp.val.y.astype(int), pLva, CFG.candidate_thresholds)
    tR = select_threshold(sp.val.y.astype(int), pRva, CFG.candidate_thresholds)
    detL = []; detR = []; leadL = []; leadR = []; paired = []
    for s, e, tf in evs:
        dL, lL = corrected_lead(pL, tL, s, e, tf, ETN)
        dR, lR = corrected_lead(pR, tR, s, e, tf, ETN)
        detL.append(dL); detR.append(dR)
        if dL: leadL.append(lL)
        if dR: leadR.append(lR)
        if dL and dR: paired.append(lR - lL)
    for nm, det, lead in (("LSTM", detL, leadL), ("RF", detR, leadR)):
        r, rlo, rhi = boot_rate(det, np.random.default_rng(SEED))
        if lead:
            mm, mlo, mhi = boot_stat(lead, np.random.default_rng(SEED), np.mean)
            md, dlo, dhi = boot_stat(lead, np.random.default_rng(SEED), np.median)
            print(f"  {nm}: detección={sum(det)}/{len(det)} ({r:.2f} IC[{rlo:.2f},{rhi:.2f}]) | "
                  f"lead medio={mm:.2f} IC[{mlo:.2f},{mhi:.2f}] mediana={md:.1f} IC[{dlo:.1f},{dhi:.1f}] (n_det={len(lead)})")
        else:
            print(f"  {nm}: detección={sum(det)}/{len(det)} | sin eventos detectados")
    if len(paired) > 1:
        dm, dl, dh = boot_stat(paired, np.random.default_rng(SEED), np.mean)
        print(f"  RF - LSTM lead medio (pareado, n={len(paired)} ambos detectados): {dm:+.2f} IC95%[{dl:+.2f},{dh:+.2f}]")
    else:
        print(f"  RF - LSTM pareado: n={len(paired)} insuficiente")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="snap.db")
    args = p.parse_args(argv)
    analyse(args.db, "docs/modelo/run_20261122T144340Z/model.keras",
            since="2026-11-07", label="RÉGIMEN SIN SESGO (13 eventos)", randomized_only=False)
    analyse(args.db, "docs/modelo/run_20261122T144431Z/model.keras",
            since=None, label="PRUEBA ALEATORIZADA DEL HISTÓRICO (74 eventos)", randomized_only=True)


if __name__ == "__main__":
    main()
