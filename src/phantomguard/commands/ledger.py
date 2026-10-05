#!/usr/bin/env python3
"""Clean-episode failure ledger for the configured artifacts (offline diagnostic).

    python -m phantomguard.commands.ledger --split all --part test --output-dir runs/ledger

For every held-out clean segment (time-block test, LOSO folds) lists each persistent alert episode with the
recording/cycle/time/frame it started, track continuity, trigger reasons with observed values and bounds,
motion/ROI state, the M-of-N vote window, and model/config identities. Slot numbers are diagnostic labels.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

from phantomguard.commands.run_attack_eval import split_contexts
from phantomguard.config import add_path_arguments, config_from_args, effective_cfg, load_baseline, paths, raw_path
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.eval.ledger import clean_episode_ledger, identity, summarize_clean
from phantomguard.io.replay import ReplaySource


def run_segment(job):
    cfg, context, segment = job
    baseline = load_baseline(context["baseline_path"])
    ae, lib = load_artifacts(context["tag"], strict=True, cfg=cfg, baseline=baseline)
    scoring = effective_cfg(cfg, baseline)
    detector = Detector(cfg, baseline, ae, lib)
    source = ReplaySource(raw_path(cfg, segment.file), (segment.lo, segment.hi))
    eps, totals = clean_episode_ledger(detector, source, scoring, identity(baseline, ae, context["tag"], scoring),
                                       split=context["split"], part=context["part"])
    return eps, {"split": context["split"], "part": context["part"], "file": segment.file, **totals}


def write_csv(path: Path, rows: list[dict]) -> None:
    cols = list(dict.fromkeys(k for r in rows for k in r)) or ["note"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        w = csv.DictWriter(stream, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=("all", "timeblock", "loso"), default="all")
    ap.add_argument("--part", choices=("test", "val"), default="test")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output-dir", type=Path, default=None)
    add_path_arguments(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    out = args.output_dir or paths(cfg).output / "ledger"
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(cfg, ctx, seg) for ctx in split_contexts(cfg, args.split, args.part) for seg in ctx["test"]]
    if args.workers == 1:
        results = list(map(run_segment, jobs))
    else:
        with ProcessPoolExecutor(args.workers) as pool:
            results = list(pool.map(run_segment, jobs))
    episodes = [e for eps, _ in results for e in eps]
    summaries = [s for _, s in results]
    write_csv(out / "clean_episodes.csv", episodes)
    for s, (eps, _) in zip(summaries, results):
        s["by"] = summarize_clean(eps, {k: s[k] for k in ("cycles", "object_cycles", "minutes")})
    pooled = {}
    for split in sorted({s["split"] for s in summaries}):
        rows = [s for s in summaries if s["split"] == split]
        mins = sum(s["minutes"] for s in rows)
        ev = sum(s["episodes"] for s in rows)
        pooled[split] = {"episodes": ev, "minutes": mins, "per_minute": ev / mins if mins else None}
    (out / "clean_ledger_summary.json").write_text(json.dumps({"pooled": pooled, "segments": summaries}, indent=2,
                                                              allow_nan=False, default=str) + "\n", encoding="utf-8")
    for s in summaries:
        print(f"{s['split']:9s} {s['file']:34s} {s['episodes']:3d} episodes in {s['minutes']:.2f} min  {s['by']['by_first_reason']}")
    print({k: (v['episodes'], round(v['per_minute'], 3)) for k, v in pooled.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
