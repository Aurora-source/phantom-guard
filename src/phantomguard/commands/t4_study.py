#!/usr/bin/env python3
"""T4 (slow drift / overwrite) separability study: windowed drift z vs EWMA of per-step residuals.

    python -m phantomguard.commands.t4_study --workers 12 --output-dir runs/t4-study

Validation part only, dev seed 11. For every context (time-block and LOSO) the clean validation streams
give, per moving in-ROI track, the maximum of each statistic; the T4 streams (A2-A4: overwrite in place,
reported velocity kept from the real track) give, per attack instance, the maximum over its forged frames.

Statistics (all causal, per track, using the baseline's fitted drift model; no attack data):
* ``drift_z``     the deployed v2 DRIFT statistic (best of 8/16/32-cycle windows, moving regime);
* ``ewma_<lam>``  vector EWMA of the per-step residual Δp - B v dt in the line-of-sight frame (B and
                  per-axis scales from the 8-cycle moving model divided by sqrt(8)), normalised by its
                  stationary std: |EWMA| / (s sqrt(lam/(2-lam))). lam in {1/4, 1/8, 1/16, 1/32}.

For each statistic the "zero clean exceedance" threshold is the largest clean track maximum (pooled over
contexts); the report gives the fraction of T4 instances above it, and above the clean 99th percentile
of track maxima. The thresholds are clean-only; attacks only measure what they would buy.
"""

from __future__ import annotations

import os

for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import argparse
import csv
import json
import math
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from phantomguard.config import add_path_arguments, config_from_args, load_baseline, paths, raw_path

LAMBDAS = (0.25, 0.125, 0.0625, 0.03125)


def track_stats(cfg, baseline, frames):
    """Run the detector; return {frame_index: {stat: value}} for moving in-ROI tracked objects, and track ids."""
    from phantomguard.detect.pipeline import Detector

    det = Detector(cfg, baseline, None, None, capture_z=True)
    dm = baseline["drift_model"]["value"]["models"]["8:moving"]
    br, bt = dm["B_radial"], dm["B_tangential"]
    sr, st = dm["scale_radial"] / math.sqrt(8), dm["scale_tangential"] / math.sqrt(8)
    thr = cfg["motion"]["moving_threshold_mps"]
    roi = cfg["roi"]["max_range"]
    state: dict[int, list] = {}
    out, track_of = {}, {}
    for r in det.run(frames):
        by_id = {t.track_id: t for t in det.tracks.active.values()}
        for v in r.objects:
            tr = by_id.get(v.track_id)
            if tr is None or len(tr.points) < 2 or not v.in_roi:
                continue
            a, b = tr.points[-2], tr.points[-1]
            if b.cycle_index - a.cycle_index != 1 or a.rng > roi:
                state.pop(v.track_id, None)
                continue
            dt = b.t_s - a.t_s
            r0 = a.rng or 1.0
            ux, uy = a.x / r0, a.y / r0
            dx, dy = b.x - a.x, b.y - a.y
            ivx, ivy = a.vx * dt, a.vy * dt
            er = (dx * ux + dy * uy) - br * (ivx * ux + ivy * uy)
            et = (-dx * uy + dy * ux) - bt * (-ivx * uy + ivy * ux)
            e = np.array([er / sr, et / st])
            ew = state.setdefault(v.track_id, [np.zeros(2) for _ in LAMBDAS])
            moving = math.hypot(a.vx, a.vy) >= thr and math.hypot(b.vx, b.vy) >= thr
            stats = {"drift_z": v.scores.get("z:DRIFT", float("nan"))}
            for i, lam in enumerate(LAMBDAS):
                ew[i] = (1 - lam) * ew[i] + lam * e
                stats[f"ewma_{lam:g}"] = float(np.hypot(*ew[i]) / math.sqrt(lam / (2 - lam))) if moving else float("nan")
            out[v.frame_index] = stats
            track_of[v.frame_index] = v.track_id
    return out, track_of


def clean_job(payload):
    cfg, context, seg = payload
    from phantomguard.io.replay import ReplaySource

    baseline = load_baseline(context["baseline_path"])
    stats, track_of = track_stats(cfg, baseline, ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)))
    per_track = defaultdict(dict)
    for fi, s in stats.items():
        t = per_track[track_of[fi]]
        for k, x in s.items():
            if x == x:
                t[k] = max(t.get(k, -1.0), x)
    return [{"context": context["tag"], "file": seg.file, **t} for t in per_track.values() if t]


def attack_job(payload):
    cfg, context, seg, job = payload
    from phantomguard.commands.run_attack_eval import attacked_stream
    from phantomguard.eval.metrics import parse_labels

    baseline = load_baseline(context["baseline_path"])
    with tempfile.TemporaryDirectory() as tmp:
        lp = Path(tmp) / "labels.csv"
        src, _, _ = attacked_stream(cfg, context, seg, job, baseline, lp)
        stats, _ = track_stats(cfg, baseline, list(src))
        with lp.open(newline="", encoding="utf-8") as f:
            labels = [l for l in parse_labels(csv.DictReader(f)) if l.is_attack]
    per = defaultdict(dict)
    for l in labels:
        s = stats.get(l.frame_index)
        if s is None:
            per[l.attack_id]
            continue
        for k, x in s.items():
            if x == x:
                per[l.attack_id][k] = max(per[l.attack_id].get(k, -1.0), x)
    return [{"context": context["tag"], "file": seg.file, "level": job["level"], "attack_id": aid, **s}
            for aid, s in per.items()]


def main(argv=None) -> int:
    from phantomguard.commands.run_attack_eval import attack_jobs, split_contexts

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    add_path_arguments(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    out = Path(args.output_dir) if args.output_dir else paths(cfg).output / "t4-study"
    out.mkdir(parents=True, exist_ok=True)
    contexts = split_contexts(cfg, "all", "val")
    clean_p = [(cfg, c, s) for c in contexts for s in c["test"]]
    att_p = [(cfg, c, s, j) for c in contexts for s in c["test"]
             for j in attack_jobs(cfg, c, s, ["T4"], ["A2", "A3", "A4"], [11], 1)]
    with ProcessPoolExecutor(args.workers) as pool:
        clean = [r for rows in pool.map(clean_job, clean_p) for r in rows]
        attacks = [r for rows in pool.map(attack_job, att_p) for r in rows]
    keys = ["drift_z"] + [f"ewma_{lam:g}" for lam in LAMBDAS]
    summary = {"clean_tracks": len(clean), "t4_instances": len(attacks), "stats": {}}
    for k in keys:
        cv = np.array([r[k] for r in clean if k in r])
        av = np.array([r.get(k, -1.0) for r in attacks])
        if not len(cv):
            continue
        t0, t99 = float(cv.max()), float(np.quantile(cv, 0.99))
        summary["stats"][k] = {"clean_tracks_scored": int(len(cv)), "clean_max": t0, "clean_p99_track": t99,
                               "t4_above_clean_max": float((av > t0).mean()) if len(av) else None,
                               "t4_above_clean_p99": float((av > t99).mean()) if len(av) else None,
                               "t4_unscored": int((av < 0).sum())}
    (out / "t4_study.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for name, rows in (("clean_tracks", clean), ("t4_instances", attacks)):
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with (out / f"t4_study_{name}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
