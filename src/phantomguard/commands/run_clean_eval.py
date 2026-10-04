#!/usr/bin/env python3
"""Clean-data evaluation of the detector (no attacks injected).

Reports false positives per object-cycle and persistent alerts per minute on held-out CLEAN data:
(a) time-block split: validation and test segments of each file, with configs/baseline.json and
    models trained on the train split;
(b) leave-one-scenario-out: each whole file, with the fold baseline and models trained on the
    other three files.
Also: per-layer ablation, static vs moving, reason-code counts, latency, and AE vs isolation forest
exceedance on the same held-out windows. All numbers in docs/results/clean_eval.md come from here.

Detection rates, time-to-detect and AUROC need attacked data and are not produced.
"""

from __future__ import annotations

from phantomguard.config import add_path_arguments, config_from_args, paths

import json
import argparse
import pickle
import sys
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _thread_setting in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_thread_setting, "1")

import numpy as np
import pandas as pd
from scipy import stats as sps

from phantomguard.config import BASELINE_PATH, REPO_ROOT, effective_cfg, load_baseline, load_config, raw_path
from phantomguard.detect.autoencoder import collection_config, load_iforest, score_iforest, windows_from_tracks
from phantomguard.detect.common import LAYERS
from phantomguard.detect.pipeline import MODELS_DIR, Detector, load_artifacts
from phantomguard.eval.metrics import PREVIOUS_FUSION, latency_stats, lite, score, with_fusion
from phantomguard.eval.splits import Segment, loso_folds, time_block_segments
from phantomguard.io.replay import ReplaySource
from phantomguard.stats.baseline import collect

RESULTS = REPO_ROOT / "docs" / "results"

SUBSETS = {
    "protocol": ("protocol",),
    "protocol+kinematic": ("protocol", "kinematic"),
    "protocol+kinematic+replay": ("protocol", "kinematic", "replay"),
    "all": LAYERS,
    "all-minus-protocol": ("kinematic", "replay", "learned"),
    "all-minus-kinematic": ("protocol", "replay", "learned"),
    "all-minus-replay": ("protocol", "kinematic", "learned"),
    "all-minus-learned": ("protocol", "kinematic", "replay"),
}


def run_segment(job):
    split, part, seg, baseline_path, tag, cfg = job
    b = load_baseline(baseline_path)
    ae, lib = load_artifacts(tag, strict=True, cfg=cfg, baseline=b)
    det = Detector(cfg, b, ae, lib)
    cycles = [lite(r) for r in det.run(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)))]
    return split, part, seg, cycles, effective_cfg(cfg, b)["fusion"]


def fmt_rate(x):
    return f"{100 * x:.3f}%"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--output-dir", type=Path, default=None)
    add_path_arguments(parser)
    args = parser.parse_args(argv)
    cfg = config_from_args(args)
    workers = args.workers if args.workers is not None else cfg["eval"]["workers"]
    if workers < 1:
        parser.error("workers must be positive")
    global_results = paths(cfg).output if args.output_dir else paths(cfg).output / "clean-eval"
    if not (paths(cfg).models / "ae_timeblock.npz").exists():
        sys.exit("models missing: run scripts/learn_baseline.py --loso and scripts/train.py --loso first")
    tb = time_block_segments(cfg)
    jobs = [("timeblock", part, s, paths(cfg).baseline, "timeblock") for part in ("val", "test") for s in tb[part]]
    pdir = paths(cfg).processed
    for held, fold in loso_folds(cfg).items():
        stem = Path(held).stem
        jobs.append(("loso", "test", fold["test"][0], pdir / f"baseline_loso_{stem}.json", f"loso_{stem}"))
    # Carry the selected data configuration to workers rather than reloading defaults.
    jobs = [(*job, cfg) for job in jobs]
    if workers == 1:
        results = list(map(run_segment, jobs))
    else:
        with ProcessPoolExecutor(workers) as ex:
            results = list(ex.map(run_segment, jobs))

    rows, abl, reason_rows = [], [], []
    all_lat = []
    op_cmp: dict = {}  # (split, part) -> [events_calibrated, events_previous, minutes]
    for split, part, seg, cycles, fusion in results:
        all_lat.extend(cycles)
        cal = score(cycles, {**cfg, "fusion": fusion}, LAYERS)
        prev = score(cycles, with_fusion(cfg, PREVIOUS_FUSION), LAYERS)
        acc = op_cmp.setdefault((split, part), [0, 0, 0.0])
        acc[0] += cal.alert_events
        acc[1] += prev.alert_events
        acc[2] += cal.minutes
        for name, layers in SUBSETS.items():
            m = score(cycles, {**cfg, "fusion": fusion}, layers)  # M/N calibrated for this split
            r = {"split": split, "part": part, "file": seg.file, "layers": name, **m.row()}
            abl.append(r)
            if name == "all":
                rows.append(r)
                for code, n in sorted((m.reasons + m.cycle_reasons).items()):
                    reason_rows.append({"split": split, "part": part, "file": seg.file, "reason": code, "count": n,
                                        "per_object_cycle": n / m.obj_cycles})
    df = pd.DataFrame(rows)
    dab = pd.DataFrame(abl)
    drs = pd.DataFrame(reason_rows)
    global_results.mkdir(parents=True, exist_ok=True)
    df.to_csv(global_results / "clean_eval.csv", index=False)
    dab.to_csv(global_results / "clean_eval_ablation.csv", index=False)
    drs.to_csv(global_results / "clean_eval_reasons.csv", index=False)
    lat = latency_stats(all_lat)

    # Pooled ablation over the time-block TEST segments and over LOSO
    def pooled(d):
        g = d.groupby("layers", sort=False)
        out = []
        for name, x in g:
            oc = x["object_cycles"].sum()
            out.append({"layers": name, "object_cycles": int(oc), "minutes": x["minutes"].sum(),
                        "fp_flagged": (x["fp_rate_flagged"] * x["object_cycles"]).sum() / oc,
                        "fp_alerting": (x["fp_rate_alerting"] * x["object_cycles"]).sum() / oc,
                        "alert_events": int(x["alert_events"].sum()),
                        "alerts_per_minute": x["alert_events"].sum() / x["minutes"].sum()})
        return pd.DataFrame(out)

    p_test = pooled(dab[(dab.split == "timeblock") & (dab.part == "test")])
    p_val = pooled(dab[(dab.split == "timeblock") & (dab.part == "val")])
    p_loso = pooled(dab[dab.split == "loso"])

    # AE vs isolation forest on held-out TEST windows (time-block), at their val-calibrated thresholds
    b = load_baseline(cfg=cfg)
    n, roi, thr = cfg["learned"]["window_cycles"], cfg["roi"]["max_range"], cfg["motion"]["moving_threshold_mps"]
    xte, mte = windows_from_tracks(list(collect(collection_config(cfg, b), tb["test"]).tracks.values()), n, roi, thr)
    ae, _ = load_artifacts("timeblock", strict=True, cfg=cfg, baseline=b)
    iso = load_iforest("timeblock", cfg=cfg, baseline=b, required=True)
    e_ae = ae.errors(xte)
    e_if = score_iforest(iso, xte)
    cmp_rows = []
    for cls, mask in (("static", ~mte), ("moving", mte)):
        cmp_rows.append({"windows": cls, "n": int(mask.sum()),
                         "ae_exceedance": float((e_ae[mask] > b[f"ae_threshold_{cls}"]["value"]).mean()),
                         "iforest_exceedance": float((e_if[mask] > b[f"iforest_threshold_{cls}"]["value"]).mean()),
                         "spearman_ae_vs_iforest": float(sps.spearmanr(e_ae[mask], e_if[mask])[0])})
    dcmp = pd.DataFrame(cmp_rows)
    dcmp.to_csv(global_results / "clean_eval_ae_vs_iforest.csv", index=False)

    opdf = pd.DataFrame([{"data": f"{sp} {pt}", "minutes": round(mn, 2), "calibrated_events": ce,
                          "calibrated_per_min": ce / mn, "previous_events": pe, "previous_per_min": pe / mn}
                         for (sp, pt), (ce, pe, mn) in op_cmp.items()])
    opdf.to_csv(global_results / "clean_eval_operating_point.csv", index=False)
    write_report(cfg, df, p_val, p_test, p_loso, drs, lat, dcmp, opdf, global_results, workers)
    print((global_results / "clean_eval.md").read_text(encoding="utf-8"))


def table(d: pd.DataFrame, cols: list[str], fmts: dict) -> str:
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = ""
    for _, r in d.iterrows():
        body += "| " + " | ".join(fmts.get(c, str)(r[c]) for c in cols) + " |\n"
    return head + body


def write_report(cfg, df, p_val, p_test, p_loso, drs, lat, dcmp, opdf, output=RESULTS, workers=None):
    pct = lambda x: f"{100 * x:.3f}%"
    f2 = lambda x: f"{x:.2f}"
    m, n = effective_cfg(cfg, load_baseline(cfg=cfg))["fusion"]["m"], effective_cfg(cfg, load_baseline(cfg=cfg))["fusion"]["n"]
    lines = ["# Clean-data evaluation (no attacks)", "",
             "Generated by `scripts/run_clean_eval.py`. Every number below is produced by that script.", "",
             "**Scope.** False positives on held-out *clean* data only (no fabricated frames). Detection rates, "
             "time-to-detect and AUROC against the synthetic frame generator are in `summary.md`.", "",
             f"- Definitions: *flagged* = an object-cycle with at least one reason code from the enabled layers. *Alerting* = after fusion "
             f"(hard reason, or {m} of the last {n} cycles of the track flagged; time-block M/N from `fusion_mn` in baseline.json, "
             "LOSO per fold; learned-only object-cycles do not count on their own). *Alert events* = number of times a track (or "
             "the cycle stream) enters the alert state. Minutes come from header timestamps × `tick_seconds` "
             f"({cfg['units']['tick_seconds']}, unverified assumption).",
             "- Thresholds: `configs/baseline.json` (time-block) and `data/processed/baseline_loso_*.json` (LOSO), learned "
             "from train (AE thresholds: validation). The test split was not used for any threshold.",
             f"- The ROI (range <= {cfg['roi']['max_range']}) applies to the kinematic, replay and learned layers; protocol checks apply to every frame.",
             "", "## Pooled results, all layers", ""]
    cols = ["layers", "object_cycles", "minutes", "fp_flagged", "fp_alerting", "alert_events", "alerts_per_minute"]
    fm = {"fp_flagged": pct, "fp_alerting": pct, "alerts_per_minute": f2, "minutes": f2,
          "object_cycles": lambda x: str(int(x)), "alert_events": lambda x: str(int(x))}
    for title, p in (("Time-block VALIDATION (thresholds calibrated here for the learned layer)", p_val),
                     ("Time-block TEST (held out)", p_test), ("Leave-one-scenario-out (each whole file held out)", p_loso)):
        lines += [f"### {title}", "", table(p, cols, fm)]
    lines += ["## Operating point trade-off (clean alert events, all layers)", "",
              f"Same detector output, fusion re-applied offline. *calibrated* = the M/N chosen by calibrate.py with the "
              f"autoencoder corroborating only; *previous* = {PREVIOUS_FUSION['m']} of {PREVIOUS_FUSION['n']} with the "
              "autoencoder allowed to alert alone. The detection side of this trade-off is in summary.md.", "",
              table(opdf, ["data", "minutes", "calibrated_events", "calibrated_per_min", "previous_events", "previous_per_min"],
                    {"calibrated_per_min": f2, "previous_per_min": f2, "minutes": f2}), ""]
    lines += ["## Per file, all layers", ""]
    c2 = ["split", "part", "file", "cycles", "minutes", "fp_rate_flagged", "fp_rate_flagged_roi_static",
          "fp_rate_flagged_roi_moving", "fp_rate_alerting", "alert_events", "alerts_per_minute"]
    fm2 = {"fp_rate_flagged": pct, "fp_rate_flagged_roi_static": pct, "fp_rate_flagged_roi_moving": pct,
           "fp_rate_alerting": pct, "alerts_per_minute": f2, "minutes": f2}
    lines += [table(df, c2, fm2)]
    lines += ["## Reason codes on clean held-out data (all layers)", ""]
    pv = drs.pivot_table(index="reason", columns=["split", "part"], values="count", aggfunc="sum", fill_value=0)
    pv.columns = [f"{a}/{b}" for a, b in pv.columns]
    lines += [table(pv.reset_index(), ["reason"] + list(pv.columns), {}), ""]
    calibration = load_baseline(cfg=cfg)
    quantile = calibration["soft_quantile"]["value"]
    threshold_rows = pd.DataFrame([{"class": cls,
                                  "ae_threshold": calibration[f"ae_threshold_{cls}"]["value"],
                                  "iforest_threshold": calibration[f"iforest_threshold_{cls}"]["value"],
                                  "validation_windows": calibration[f"ae_threshold_{cls}"]["n_windows"]}
                                 for cls in ("static", "moving")])
    lines += ["## Learned layer: autoencoder vs isolation forest (time-block TEST windows)", "",
              f"Both use the selected validation quantile q={quantile} ({100*quantile:.4g}th percentile), separately "
              "for static and moving windows. Held-out clean exceedance is measured below; it need not match the "
              "validation tail probability. Without attacked data the two cannot be ranked on detection. The AE is used online "
              "because it runs in numpy inside the latency budget; the isolation forest is scored offline only.", "",
              table(threshold_rows, ["class", "ae_threshold", "iforest_threshold", "validation_windows"], {}), "",
              table(dcmp, ["windows", "n", "ae_exceedance", "iforest_exceedance", "spearman_ae_vs_iforest"],
                    {"ae_exceedance": pct, "iforest_exceedance": pct, "spearman_ae_vs_iforest": f2, "n": str}), ""]
    lines += ["## Latency", "",
              f"Per-cycle processing (all layers including AE inference), over {lat['n']} cycles of all evaluated segments: "
              f"p50 {lat['p50_ms']:.3f} ms, **p99 {lat['p99_ms']:.3f} ms**, max {lat['max_ms']:.3f} ms "
              f"(budget: p99 < {cfg['latency']['p99_budget_ms']} ms). Measured with "
              f"{workers if workers is not None else cfg['eval']['workers']} worker(s) on this machine.", ""]
    lines += [f"Detector CPU p99 {lat['detector_p99_ms']:.3f} ms; frame grouping/decoding CPU p99 "
              f"{lat['assembly_cpu_p99_ms']:.3f} ms. Total processing above includes both. Timestamp-based "
              "assembly delay is separate and is reported by run_attack_eval.py; source I/O/capture waiting "
              "and offline IF scoring are excluded from processing.", ""]
    lines += ["## Operating point (calibrated on validation by scripts/calibrate.py)", "",
              "Soft kinematic and AE thresholds are quantiles of clean data; the quantile is the smallest candidate with "
              "validation alerts/min < 1 (all layers, after fusion). The test split played no part. Val minutes are short, so "
              "each rate rests on a handful of events.", ""]
    pdir = paths(cfg).processed
    srcs = [("time-block", paths(cfg).baseline)] + [(f"LOSO held={Path(f).stem}", pdir / f"baseline_loso_{Path(f).stem}.json")
                                               for f in cfg["data"]["files"]]
    for name, path in srcs:
        sq = load_baseline(path).get("soft_quantile")
        if sq is None:
            continue
        tab = pd.DataFrame(sq["table"])
        lines += [f"**{name}**: chosen q = {sq['value']} ({sq['rule'].split(';')[0]})", "",
                  table(tab, ["quantile", "val_alert_events", "val_minutes", "val_alerts_per_minute", "val_fp_flagged"],
                        {"val_fp_flagged": pct, "val_alerts_per_minute": f2}), ""]
        fm = load_baseline(path).get("fusion_mn")
        if fm:
            lines += [f"Fusion M/N chosen {fm['value'][0]}/{fm['value'][1]} ({fm['rule']}):", "",
                      table(pd.DataFrame(fm["table"]), ["m", "n", "val_alert_events", "val_minutes", "val_alerts_per_minute"],
                            {"val_alerts_per_minute": f2}), ""]
    target = 1.0
    tpm = p_test.set_index("layers").loc["all", "alerts_per_minute"]
    lpm = p_loso.set_index("layers").loc["all", "alerts_per_minute"]
    lines += ["## Against the target", "",
              f"- Target: under {target} false alert per minute on clean held-out data after persistence filtering.",
              f"- Time-block test: {tpm:.2f} alerts/min -> {'MET' if tpm < target else 'NOT MET'}.",
              f"- LOSO: {lpm:.2f} alerts/min -> {'MET' if lpm < target else 'NOT MET'}.", ""]
    (output / "clean_eval.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
