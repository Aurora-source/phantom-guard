#!/usr/bin/env python3
"""Does a learned layer add exact forged-object recall at the same clean alert budget? (development study)

    python -m phantomguard.commands.learned_compare --part val --workers 12 --output-dir runs/learned-compare

Candidates, all scored on the SAME online windows (last N+1 points of an in-ROI track; dx, dy, vx, vy,
radial v, RCS, range; no slot, counter, time or identity), trained on the context's TRAIN portions only:

* ``ae``     the existing window autoencoder (reconstruction error), artifact from ``train``;
* ``pred``   a causal ridge predictor of the last step (dx, dy, vx, vy, RCS) from the previous N-1 steps;
             score = mean squared residual in robust (MAD) units. NumPy closed form, deterministic;
* ``resid``  no learning beyond three fitted scales: last-step position change vs reported velocity with a
             fitted per-axis gain B (dx ~ B_x vx dt) and the RCS step vs the window mean, in MAD units.

Routes (detect.fusion): ``independent`` (learned flags M of N of a track's own cycles: alerts alone) and
``joint`` (learned-only cycles count ``weight`` toward the physics M-of-N; monotone). For each candidate x
route the threshold is the most sensitive grid point (quantiles of the clean validation window scores,
static and moving separately) that adds no clean alert episode over the physics-only system on the same
clean validation streams. Attacks are then injected into those validation streams (dev seeds) and the
increment in exactly identified instances is reported, with leave-one-layer-out rows for the physics
layers. The final test part is never touched by this command. If no candidate adds recall at equal clean
budget, the report says so: the learned layer then has no operational role and no ML claim is made.
"""

from __future__ import annotations

import os

for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import argparse
import csv
import json
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

from phantomguard.config import add_path_arguments, config_from_args, effective_cfg, load_baseline, paths, raw_path

PHYSICS = ("protocol", "kinematic", "replay")
ALL = PHYSICS + ("learned",)
MODELS = ("ae", "pred", "resid")
ROUTES = {"independent": {"mode": "independent", "m": 4, "n": 6}, "joint": {"mode": "joint", "weight": 0.5}}
GRID_Q = (0.99, 0.995, 0.999, 0.9995, 0.9999, 1.0)
LAST = [0, 1, 2, 3, 5]          # dx, dy, vx, vy, rcs inside a step's 7 features


# ----------------------------------------------------------------------------------------- models
def _mad(r: np.ndarray, floor: float) -> np.ndarray:
    return np.maximum(1.4826 * np.median(np.abs(r - np.median(r, axis=0)), axis=0), floor)


def fit_pred(w: np.ndarray, n: int, lam: float = 1e-2) -> dict:
    x, y = w[:, :(n - 1) * 7], w[:, (n - 1) * 7:][:, LAST]
    mx, sx = x.mean(0), x.std(0)
    sx[sx < 1e-6] = 1.0
    z = np.column_stack([(x - mx) / sx, np.ones(len(x))])
    a = z.T @ z + lam * len(z) * np.eye(z.shape[1])
    coef = np.linalg.solve(a, z.T @ y)
    s = _mad(y - z @ coef, 0.05)
    return {"kind": "pred", "mx": mx, "sx": sx, "coef": coef, "scale": s, "n": n, "lam": lam, "train_windows": len(w)}


def fit_resid(w: np.ndarray, n: int, dt: float) -> dict:
    last = w[:, (n - 1) * 7:(n - 1) * 7 + 7]
    rcs_prev = w[:, 5:(n - 1) * 7:7].mean(1)
    bx = float((last[:, 0] * last[:, 2] * dt).sum() / max(((last[:, 2] * dt) ** 2).sum(), 1e-9))
    by = float((last[:, 1] * last[:, 3] * dt).sum() / max(((last[:, 3] * dt) ** 2).sum(), 1e-9))
    r = np.column_stack([last[:, 0] - bx * last[:, 2] * dt, last[:, 1] - by * last[:, 3] * dt, last[:, 5] - rcs_prev])
    return {"kind": "resid", "bx": bx, "by": by, "dt": dt, "scale": _mad(r, 0.05), "n": n, "train_windows": len(w)}


def score_model(m: dict, w: np.ndarray) -> np.ndarray:
    n = m["n"]
    if m["kind"] == "pred":
        z = np.column_stack([(w[:, :(n - 1) * 7] - m["mx"]) / m["sx"], np.ones(len(w))])
        r = (w[:, (n - 1) * 7:][:, LAST] - z @ m["coef"]) / m["scale"]
    else:
        last = w[:, (n - 1) * 7:(n - 1) * 7 + 7]
        rcs_prev = w[:, 5:(n - 1) * 7:7].mean(1)
        r = np.column_stack([last[:, 0] - m["bx"] * last[:, 2] * m["dt"], last[:, 1] - m["by"] * last[:, 3] * m["dt"],
                             last[:, 5] - rcs_prev]) / m["scale"]
    return (r ** 2).mean(1)


def fit_models(cfg: dict, baseline: dict, train) -> dict:
    from phantomguard.config import bval
    from phantomguard.detect.autoencoder import collection_config, windows_from_tracks
    from phantomguard.stats.baseline import collect

    n = cfg["learned"]["window_cycles"]
    st = collect(collection_config(cfg, baseline), train)
    w, _ = windows_from_tracks(list(st.tracks.values()), n, cfg["roi"]["max_range"], cfg["motion"]["moving_threshold_mps"])
    dt = float(bval(baseline, "cadence_median")) * cfg["units"]["tick_seconds"]
    return {"pred": fit_pred(w, n), "resid": fit_resid(w, n, dt)}


# ---------------------------------------------------------------------------------------- scoring
def detect_scored(cfg, baseline, tag, source, models):
    from phantomguard.detect.pipeline import Detector, load_artifacts
    from phantomguard.eval.metrics import lite

    ae, lib = load_artifacts(tag, strict=True, cfg=cfg, baseline=baseline)
    results = list(Detector(cfg, baseline, ae, lib, capture_windows=True).run(source))
    entries = [(fi, w) for r in results for fi, (w, _) in r.learned_windows.items()]
    cycles = [lite(r) for r in results]
    if entries:
        x = np.asarray([w for _, w in entries], dtype=float)
        by_fi = {v.frame_index: v for c in cycles for v in c.objects}
        for name, m in models.items():
            for (fi, _), s in zip(entries, score_model(m, x)):
                by_fi[fi].scores[name] = float(s)
    return cycles


def with_learned(cycles, key: str, thr: tuple[float, float]):
    out = []
    for c in cycles:
        objs = []
        for v in c.objects:
            reasons = [r for r in v.reasons if r != "LEARNED"]
            s = v.scores.get(key)
            if s is not None and np.isfinite(s) and s > thr[1 if v.window_moving else 0]:
                reasons.append("LEARNED")
            objs.append(replace(v, reasons=reasons, flagged=False, alert=False))
        out.append(replace(c, objects=objs, cycle_alert=False))
    return out


def variants(scoring: dict, thresholds: dict) -> dict:
    """name -> (layers, cfg, learned key, thresholds or None)."""
    from phantomguard.eval.metrics import with_fusion

    out = {"physics": (PHYSICS, scoring, None, None)}
    for drop in PHYSICS:
        out[f"physics-minus-{drop}"] = (tuple(l for l in PHYSICS if l != drop), scoring, None, None)
    for (model, route), thr in thresholds.items():
        if thr is not None:
            out[f"{model}+{route}"] = (ALL, with_fusion(scoring, {"learned_route": ROUTES[route]}), model, thr)
    return out


def run_variant(cycles, layers, vcfg, key, thr):
    from phantomguard.eval.metrics import apply_layers

    base = with_learned(cycles, key, thr) if key else [replace(c, objects=[replace(v, flagged=False, alert=False)
                                                                            for v in c.objects]) for c in cycles]
    return apply_layers(base, vcfg, layers, copy_records=False)


# ---------------------------------------------------------------------------------------- workers
def clean_worker(payload):
    cfg, context, segment, models = payload
    from phantomguard.io.replay import ReplaySource

    baseline = load_baseline(context["baseline_path"])
    return detect_scored(cfg, baseline, context["tag"], ReplaySource(raw_path(cfg, segment.file), (segment.lo, segment.hi)), models)


def attack_worker(payload):
    cfg, context, segment, job, models, thresholds = payload
    from phantomguard.commands.run_attack_eval import attacked_stream
    from phantomguard.eval.attack_adapter import support_reason
    from phantomguard.eval.metrics import attack_metrics, parse_labels

    if support_reason(job["attack_type"], job["level"], job["motion_case"]):
        return []
    baseline = load_baseline(context["baseline_path"])
    scoring = effective_cfg(cfg, baseline)
    with tempfile.TemporaryDirectory() as tmp:
        labels_path = Path(tmp) / "labels.csv"
        try:
            attacked, _, _ = attacked_stream(cfg, context, segment, job, baseline, labels_path)
            cycles = detect_scored(cfg, baseline, context["tag"], attacked, models)
        except (ValueError, RuntimeError) as exc:
            return [{**job, "variant": "error", "error": str(exc)}]
        with labels_path.open(newline="", encoding="utf-8") as f:
            labels = parse_labels(csv.DictReader(f))
    rows = []
    for name, (layers, vcfg, key, thr) in variants(scoring, thresholds).items():
        fused = run_variant(cycles, layers, vcfg, key, thr)
        _, inst = attack_metrics(fused, labels, vcfg, layers, copy_records=False, score_cache={"skip_scores": True})
        rows.append({**{k: job[k] for k in ("attack_type", "level", "motion_case", "replay_provenance", "replay_variant",
                                              "effective_seed")},
                     "context": context["tag"], "file": segment.file, "variant": name,
                     "identified": sorted(i["attack_id"] for i in inst if i["identified"]),
                     "detected": sorted(i["attack_id"] for i in inst if i["detected"]),
                     "instances": sorted(i["attack_id"] for i in inst)})
    return rows


# ------------------------------------------------------------------------------------------- main
def choose_thresholds(clean: list, scoring: dict, log) -> tuple[dict, list]:
    from phantomguard.eval.calibration import episodes_for

    ref, minutes = episodes_for([run_variant(c, PHYSICS, scoring, None, None) for c in clean], scoring, PHYSICS)
    chosen, audit = {}, []
    for model in MODELS:
        vals = {mv: np.asarray([v.scores[model] for c in clean for cy in c for v in cy.objects
                                if model in v.scores and bool(v.window_moving) == mv]) for mv in (False, True)}
        if not len(vals[False]) or not len(vals[True]):
            for route in ROUTES:
                chosen[(model, route)] = None
            continue
        grid = [(float(np.quantile(vals[False], q)), float(np.quantile(vals[True], q))) for q in GRID_Q]
        for route, spec in ROUTES.items():
            from phantomguard.eval.metrics import with_fusion

            vcfg = with_fusion(scoring, {"learned_route": spec})
            pick = None
            for q, thr in zip(GRID_Q, grid):
                ev, _ = episodes_for([run_variant(c, ALL, vcfg, model, thr) for c in clean], vcfg, ALL)
                audit.append({"model": model, "route": route, "quantile": q, "thr_static": thr[0], "thr_moving": thr[1],
                              "clean_episodes": ev, "physics_reference": ref, "minutes": round(minutes, 3)})
                if pick is None and ev <= ref:
                    pick = thr
            chosen[(model, route)] = pick
            log(f"    {model}+{route}: threshold {pick} (reference {ref} episodes in {minutes:.2f} min)")
    return chosen, audit


def main(argv=None) -> int:
    from phantomguard.commands.run_attack_eval import attack_jobs, split_contexts

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=("val",), default="val", help="development part only; test is never used")
    ap.add_argument("--split", choices=("all", "timeblock", "loso"), default="all")
    ap.add_argument("--levels", default="A2,A3,A4")
    ap.add_argument("--seeds", default="11")
    ap.add_argument("--workers", type=int, default=8)
    add_path_arguments(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    out = paths(cfg).output / "learned-compare" if args.output_dir is None else Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    levels = args.levels.split(",")
    seeds = [int(s) for s in args.seeds.split(",")]
    rows, audit, model_meta = [], [], {}
    with ProcessPoolExecutor(args.workers) as pool:
        for context in split_contexts(cfg, args.split, args.part):
            baseline = load_baseline(context["baseline_path"])
            scoring = effective_cfg(cfg, baseline)
            models = fit_models(cfg, baseline, context["train"])
            model_meta[context["tag"]] = {k: {kk: (vv.tolist() if isinstance(vv, np.ndarray) else vv) for kk, vv in m.items()
                                              if kk not in {"mx", "sx", "coef"}} for k, m in models.items()}
            print(f"[{context['tag']}] clean validation streams", flush=True)
            clean = list(pool.map(clean_worker, [(cfg, context, s, models) for s in context["test"]]))
            thresholds, aud = choose_thresholds(clean, scoring, print)
            audit += [{"context": context["tag"], **a} for a in aud]
            payloads = [(cfg, context, s, job, models, thresholds) for s in context["test"]
                        for job in attack_jobs(cfg, context, s, None, levels, seeds, 1)]
            print(f"[{context['tag']}] {len(payloads)} attacked streams", flush=True)
            for r in pool.map(attack_worker, payloads):
                rows += r
    (out / "learned_compare_rows.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")
    with (out / "learned_compare_thresholds.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(audit[0]))
        w.writeheader()
        w.writerows(audit)
    summary = summarize(rows)
    (out / "learned_compare_summary.json").write_text(json.dumps({"summary": summary, "models": model_meta,
                                                                    "routes": ROUTES, "grid_quantiles": GRID_Q},
                                                                   indent=2, default=str) + "\n", encoding="utf-8")
    for line in summary["overall"]:
        print(line)
    return 0


def summarize(rows: list[dict]) -> dict:
    """Per variant: instances identified/detected, and the increment over physics on the same instances."""
    by_stream = defaultdict(dict)
    for r in rows:
        if r.get("variant") == "error":
            continue
        key = (r["context"], r["file"], r["attack_type"], r["level"], r["motion_case"], r["replay_provenance"],
               r["replay_variant"], r["effective_seed"])
        by_stream[key][r["variant"]] = r
    tot = defaultdict(lambda: defaultdict(int))
    cell = defaultdict(lambda: defaultdict(int))
    for key, vs in by_stream.items():
        phys = vs.get("physics")
        if not phys:
            continue
        p_id, p_det = set(phys["identified"]), set(phys["detected"])
        for name, r in vs.items():
            ident, det = set(r["identified"]), set(r["detected"])
            for agg in (tot[name], cell[(name, key[2], key[3])]):
                agg["instances"] += len(r["instances"])
                agg["identified"] += len(ident)
                agg["detected"] += len(det)
                agg["gained_identified"] += len(ident - p_id)
                agg["lost_identified"] += len(p_id - ident)
                agg["gained_detected"] += len(det - p_det)
    overall = [f"{name:28s} instances {t['instances']:5d} identified {t['identified']:5d} detected {t['detected']:5d} "
               f"+id {t['gained_identified']:4d} -id {t['lost_identified']:4d} +det {t['gained_detected']:4d}"
               for name, t in sorted(tot.items())]
    return {"overall": overall, "totals": {k: dict(v) for k, v in tot.items()},
            "by_type_level": {f"{k[0]}|{k[1]}|{k[2]}": dict(v) for k, v in sorted(cell.items())}}


if __name__ == "__main__":
    raise SystemExit(main())
