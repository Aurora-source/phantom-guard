#!/usr/bin/env python3
"""Calibrate the false-alarm operating point on the VALIDATION split (never on test).

The soft kinematic thresholds and the AE thresholds are quantiles of clean data. Picking the
quantile is a calibration choice: we take the smallest candidate quantile whose clean validation
alert rate (all layers, after M-of-N fusion) is below the target. The thresholds themselves still
come from train (kinematic) and validation (AE) clean data. The full candidate table is recorded in
the baseline JSON. Run after learn_baseline.py and train.py; --loso calibrates every fold on its
own validation segments.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from phantomguard.config import BASELINE_PATH, REPO_ROOT, load_baseline, load_config, raw_path, save_baseline
from phantomguard.detect.autoencoder import windows_from_tracks
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.eval.metrics import lite, score
from phantomguard.eval.splits import loso_folds, time_block_segments
from phantomguard.io.replay import ReplaySource
from phantomguard.stats.baseline import collect, derive_thresholds, exceedance, track_features

CANDIDATES = [0.999, 0.9995, 0.9999, 0.99995, 1.0]
SOFT_KEYS = ["accel_hard", "rr_resid_hard", "pos_speed_hard", "rcs_std_hard", "rcs_by_range", "colocation_min",
             "speed_soft"]
TARGET_PER_MIN = 1.0


def calibrate(cfg, train, val, baseline_path: Path, tag: str) -> None:
    base = load_baseline(baseline_path)
    ae, lib = load_artifacts(tag)
    st_tr = collect(cfg, train)
    tf_tr = track_features(st_tr, cfg)
    st_va = collect(cfg, val)
    tf_va = track_features(st_va, cfg)
    n, roi, thr = cfg["learned"]["window_cycles"], cfg["roi"]["max_range"], cfg["motion"]["moving_threshold_mps"]
    xva, mva = windows_from_tracks(list(st_va.tracks.values()), n, roi, thr)
    e_va = ae.errors(xva)
    table, chosen = [], None
    for q in CANDIDATES:
        cand = derive_thresholds(st_tr, tf_tr, cfg, kq=q)
        b = dict(base)
        for k in SOFT_KEYS:
            b[k] = cand[k]
        b["ae_threshold_static"] = dict(base["ae_threshold_static"], value=float(np.quantile(e_va[~mva], q)))
        b["ae_threshold_moving"] = dict(base["ae_threshold_moving"], value=float(np.quantile(e_va[mva], q)))
        events = minutes = flagged = objs = 0
        for seg in val:  # score each segment on its own: separate streams, separate time spans
            det = Detector(cfg, b, ae, lib)
            m = score([lite(r) for r in det.run(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)))], cfg,
                      ("protocol", "kinematic", "replay", "learned"))
            events += m.alert_events
            minutes += m.minutes
            flagged += m.flagged
            objs += m.obj_cycles
        apm = events / minutes
        table.append({"quantile": q, "val_alerts_per_minute": round(apm, 3), "val_fp_flagged": flagged / objs,
                      "val_alert_events": events, "val_minutes": round(minutes, 3)})
        print(f"[{tag}] q={q}: val {apm:.2f} alerts/min ({events} in {minutes:.2f} min), flagged {100 * flagged / objs:.3f}%")
        if chosen is None and apm < TARGET_PER_MIN:
            chosen = (q, b)
    if chosen is None:  # nothing meets the target: keep the loosest candidate and say so
        q = CANDIDATES[-1]
        chosen = (q, b)
        note = f"no candidate met < {TARGET_PER_MIN} alerts/min on validation; loosest candidate kept"
    else:
        note = f"smallest candidate quantile with validation alerts/min < {TARGET_PER_MIN}"
    q, b = chosen
    exc = exceedance(st_va, tf_va, b)
    for k, ek in (("accel_hard", "accel_hard"), ("rr_resid_hard", "rr_resid_hard"), ("pos_speed_hard", "pos_speed_hard"),
                  ("rcs_std_hard", "rcs_std_hard"), ("rcs_by_range", "rcs_by_range"), ("colocation_min", "colocation")):
        b[k]["val_exceedance"] = exc[ek]
    for k in ("ae_threshold_static", "ae_threshold_moving"):
        b[k]["rule"] = b[k]["rule"].replace(f"q{cfg['learned']['threshold_quantile']}", f"q{q}")
    b["soft_quantile"] = {"value": q, "rule": note + "; candidates " + str(CANDIDATES), "split": "val",
                          "table": table}
    save_baseline(b, baseline_path)
    print(f"[{tag}] chosen soft quantile {q} ({note})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loso", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    tb = time_block_segments(cfg)
    calibrate(cfg, tb["train"], tb["val"], BASELINE_PATH, "timeblock")
    if args.loso:
        pdir = REPO_ROOT / cfg["data"]["processed_dir"]
        for held, fold in loso_folds(cfg).items():
            stem = Path(held).stem
            calibrate(cfg, fold["train"], fold["val"], pdir / f"baseline_loso_{stem}.json", f"loso_{stem}")


if __name__ == "__main__":
    main()
