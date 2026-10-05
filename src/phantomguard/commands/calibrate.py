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

from phantomguard.config import add_path_arguments, config_from_args, paths

import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from phantomguard.config import BASELINE_PATH, REPO_ROOT, effective_cfg, load_baseline, load_config, raw_path, save_baseline
from phantomguard.detect.autoencoder import (calibrated_thresholds, load_iforest, score_iforest,
                                             collection_config, windows_from_tracks)
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
    base.pop("fusion_mn", None)  # choose both stages on this split's clean validation
    ae, lib = load_artifacts(tag, strict=True, cfg=cfg, baseline=base)
    iforest = load_iforest(tag, strict=True, cfg=cfg, baseline=base)
    collect_cfg = collection_config(cfg, base)
    st_tr = collect(collect_cfg, train)
    tf_tr = track_features(st_tr, cfg)
    st_va = collect(collect_cfg, val)
    tf_va = track_features(st_va, cfg)
    n, roi, thr = cfg["learned"]["window_cycles"], cfg["roi"]["max_range"], cfg["motion"]["moving_threshold_mps"]
    xva, mva = windows_from_tracks(list(st_va.tracks.values()), n, roi, thr)
    e_va = ae.errors(xva)
    s_va = score_iforest(iforest, xva)
    table, chosen = [], None
    for q in CANDIDATES:
        cand = derive_thresholds(st_tr, tf_tr, cfg, kq=q)
        b = dict(base)
        for k in SOFT_KEYS:
            b[k] = cand[k]
        ae_thresholds = calibrated_thresholds(e_va, mva, q, "AE reconstruction error", n)
        if_thresholds = calibrated_thresholds(s_va, mva, q, "isolation-forest anomaly", n)
        for label in ("static", "moving"):
            b[f"ae_threshold_{label}"] = dict(base[f"ae_threshold_{label}"], **ae_thresholds[label])
            b[f"iforest_threshold_{label}"] = dict(base[f"iforest_threshold_{label}"], **if_thresholds[label])
        events = minutes = flagged = objs = 0
        for seg in val:  # score each segment on its own: separate streams, separate time spans
            det = Detector(cfg, b, ae, lib)
            m = score([lite(r) for r in det.run(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)))], cfg,
                      ("protocol", "kinematic", "replay", "learned"))
            events += m.alert_events
            minutes += m.minutes
            flagged += m.flagged
            objs += m.obj_cycles
        if minutes <= 0 or objs == 0:
            raise ValueError(f"[{tag}] validation has no elapsed time or object-cycles; calibration unavailable")
        apm = events / minutes
        table.append({"quantile": q, "val_alerts_per_minute": round(apm, 3), "val_fp_flagged": flagged / objs,
                      "val_alert_events": events, "val_minutes": round(minutes, 3),
                      "ae_threshold_static": ae_thresholds["static"]["value"],
                      "ae_threshold_moving": ae_thresholds["moving"]["value"],
                      "iforest_threshold_static": if_thresholds["static"]["value"],
                      "iforest_threshold_moving": if_thresholds["moving"]["value"]})
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
    b["soft_quantile"] = {"value": q, "rule": note + "; candidates " + str(CANDIDATES), "split": "val",
                          "table": table, "selection_model": "ae",
                          "iforest_rule": "same selected validation quantile as AE; not used to select operating point"}
    b["fusion_mn"] = choose_fusion(cfg, b, ae, lib, val, tag)
    save_baseline(b, baseline_path)
    print(f"[{tag}] chosen soft quantile {q} ({note}); fusion M/N {b['fusion_mn']['value']}")


def choose_fusion(cfg, b, ae, lib, val, tag: str) -> dict:
    """Stage 2: persistence M-of-N from CLEAN validation only (no attack labels: hard rule 2).

    Rule: fewest clean validation alert events; ties go to the smallest N (fastest time-to-detect).
    The detector's reason codes do not depend on M/N, so it runs once and fusion is re-applied offline.
    """
    runs = []
    for seg in val:
        det = Detector(cfg, b, ae, lib)
        runs.append([lite(r) for r in det.run(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)))])
    table = []
    for m_, n_ in cfg["fusion"]["mn_candidates"]:
        c2 = {**cfg, "fusion": {**cfg["fusion"], "m": m_, "n": n_}}
        events = minutes = 0.0
        for cycles in runs:
            sm = score(cycles, c2, ("protocol", "kinematic", "replay", "learned"))
            events += sm.alert_events
            minutes += sm.minutes
        table.append({"m": m_, "n": n_, "val_alert_events": int(events), "val_minutes": round(minutes, 3),
                      "val_alerts_per_minute": round(events / minutes, 3)})
        print(f"[{tag}] fusion {m_}/{n_}: {int(events)} clean val alert events in {minutes:.2f} min")
    best = min(table, key=lambda r: (r["val_alert_events"], r["n"]))
    return {"value": [best["m"], best["n"]], "split": "val", "table": table,
            "rule": "fewest CLEAN validation alert events over mn_candidates; ties -> smallest N (fastest detection); "
                    "no attack labels used"}


# ------------------------------------------------------------------ profile v2: out-of-recording episodes

V2_LAYERS = ("protocol", "kinematic", "replay")   # the learned layer has its own calibration (train.py / calibrate)


def _file_portions(segments):
    """One contiguous [lo, hi) portion per recording (train and validation segments of a file are adjacent)."""
    by_file: dict = {}
    for s in segments:
        lo, hi = by_file.get(s.file, (s.lo, s.hi))
        by_file[s.file] = (min(lo, s.lo), max(hi, s.hi))
    return by_file


def cv_run(job):
    """Fit profile v2 on every recording except ``held``, then run the detector on ``held`` (clean).

    Returns pruned LiteCycles carrying stored exceedances ``scores['z:CODE']``; thresholds are applied
    later by eval.calibration so one detector pass serves the whole grid.
    """
    from phantomguard.detect.autoencoder import collection_config
    from phantomguard.detect.replay_fp import build_library
    from phantomguard.eval.calibration import TUNABLE, prune_quiet_tracks
    from phantomguard.eval.splits import Segment
    from phantomguard.stats.baseline import derive_thresholds
    from phantomguard.stats.v2 import apply_v2

    cfg, portions, held = job
    inner = [Segment(f, lo, hi) for f, (lo, hi) in portions.items() if f != held]
    st_cfg = collection_config(cfg, {"reassign_jump": cfg["tracks"]["reassign_jump_default"]})
    st = collect(st_cfg, inner)
    tf = track_features(st, cfg)
    b = derive_thresholds(st, tf, cfg)
    apply_v2(cfg, b, st)
    b["rule_thresholds"] = {"value": {code: None for code in TUNABLE}}   # record exceedances, flag nothing yet
    lib = build_library(cfg, list(st.tracks.values()))
    lo, hi = portions[held]
    det = Detector(cfg, b, None, lib, layers=V2_LAYERS, capture_z=True)
    cycles = [lite(r) for r in det.run(ReplaySource(raw_path(cfg, held), (lo, hi)))]
    floor = {code: spec["grid"][0] - 1e-9 for code, spec in TUNABLE.items()}
    return held, prune_quiet_tracks(cycles, floor), len(cycles)


def calibrate_v2(cfg, train, val, baseline_path: Path, tag: str, workers: int, m: int = 4, n: int = 6) -> None:
    from phantomguard.eval.calibration import TUNABLE, choose_rule_thresholds, episodes_for, poisson_ci, rethreshold

    base = load_baseline(baseline_path)
    if (base.get("detector_contract") or {}).get("value", {}).get("profile") != "v2":
        raise ValueError(f"{baseline_path}: not a profile v2 baseline; run baseline --profile v2 first")
    portions = _file_portions([*train, *val])
    jobs = [(cfg, portions, held) for held in portions]
    if workers == 1:
        runs = list(map(cv_run, jobs))
    else:
        with ProcessPoolExecutor(workers) as pool:
            runs = list(pool.map(cv_run, jobs))
    pruned = [r[1] for r in runs]
    fus_cfg = effective_cfg({**cfg, "fusion": {**cfg["fusion"], "m": m, "n": n}}, base)
    codes = list(TUNABLE)
    print(f"[{tag}] out-of-recording CV over {[r[0][:12] for r in runs]} ({sum(r[2] for r in runs)} cycles)")
    thresholds, choices, ref = choose_rule_thresholds(pruned, fus_cfg, V2_LAYERS, codes, allowance=0)
    active = {c for c, t in thresholds.items() if np.isfinite(t)}
    final_events, minutes = episodes_for([rethreshold(r, thresholds, active) for r in pruned], fus_cfg, V2_LAYERS)
    per_file = []
    for (held, _, _), cycles in zip(runs, pruned):
        ev, mins = episodes_for([rethreshold(cycles, thresholds, active)], fus_cfg, V2_LAYERS)
        per_file.append({"held_out": held, "events": ev, "minutes": round(mins, 3)})
    lo, hi = poisson_ci(final_events, minutes)
    from phantomguard.eval.localize import alert_episodes
    from phantomguard.eval.metrics import apply_layers
    detail = []
    for (held, _, _), cycles in zip(runs, pruned):
        fused = apply_layers(rethreshold(cycles, thresholds, active), fus_cfg, V2_LAYERS)
        detail += [{"held_out": held, "cycle": ep["start_cycle"], "kind": ep["kind"], "reasons": dict(ep["reasons"])}
                   for ep in alert_episodes(fused)]
    base["rule_thresholds"] = {
        "value": {c: (None if not np.isfinite(t) else float(t)) for c, t in thresholds.items()},
        "rule": "per rule: least sensitive grid value at which the rule alone adds <= 0 alert episodes on clean recordings "
                "scored by a model fitted without them (inner leave-one-recording-out CV over train+validation recordings); "
                "null = no grid value met the allowance (rule inactive)",
        "split": "cv_out_of_recording", "calibrated": True, "fusion_mn": [m, n],
        "cv": {"episodes": final_events, "minutes": round(minutes, 3), "per_minute": final_events / minutes if minutes else None,
               "poisson_ci95_per_minute": [lo, hi], "reference_events_without_tunable_rules": ref["reference_events"],
               "per_recording": per_file, "episodes_detail": detail,
               "caveat": "recordings are consecutive segments of one session; episodes are clustered; no IID or "
                         "distribution-free guarantee"},
        "table": [{"code": c.code, "unit": TUNABLE[c.code]["unit"], "threshold": None if not np.isfinite(c.threshold) else c.threshold,
                   "episodes_at_choice": c.episodes_at_choice, "allowance": c.allowance, "curve": c.curve} for c in choices]}
    base["fusion_mn"] = {"value": [m, n], "split": "cv_out_of_recording", "table": [],
                         "rule": "profile v2 fixes M/N at the published 4-of-6; M/N variants are reported as ablations"}
    save_baseline(base, baseline_path)
    print(f"[{tag}] chosen thresholds {base['rule_thresholds']['value']}; CV episodes {final_events} in {minutes:.2f} min "
          f"(95% Poisson {lo:.2f}-{hi:.2f}/min; reference {ref['reference_events']})")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--loso", action="store_true")
    ap.add_argument("--profile", choices=("legacy", "v2"), default="legacy")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 4))
    add_path_arguments(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    tb = time_block_segments(cfg)
    if args.profile == "v2":
        calibrate_v2(cfg, tb["train"], tb["val"], paths(cfg).baseline, "timeblock", args.workers)
        if args.loso:
            pdir = paths(cfg).processed
            for held, fold in loso_folds(cfg).items():
                stem = Path(held).stem
                calibrate_v2(cfg, fold["train"], fold["val"], pdir / f"baseline_loso_{stem}.json", f"loso_{stem}", args.workers)
        return
    calibrate(cfg, tb["train"], tb["val"], paths(cfg).baseline, "timeblock")
    if args.loso:
        pdir = paths(cfg).processed
        for held, fold in loso_folds(cfg).items():
            stem = Path(held).stem
            calibrate(cfg, fold["train"], fold["val"], pdir / f"baseline_loso_{stem}.json", f"loso_{stem}")


if __name__ == "__main__":
    main()
