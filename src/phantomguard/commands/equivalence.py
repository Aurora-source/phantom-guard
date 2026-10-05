#!/usr/bin/env python3
"""Cross-platform equivalence of detector outputs (Windows vs Linux, same artifacts and streams).

    python -m phantomguard.commands.equivalence run --output runs/equivalence/windows.json
    python -m phantomguard.commands.equivalence compare runs/equivalence/windows.json runs/equivalence/linux.json

``run`` scores fixed streams with the configured artifacts: every clean recording in full (time-block
artifacts) and attacked validation streams (time-block context, dev seed 11, one per type at A3 plus T3
A4) and records, per stream, a SHA-256 of the canonical decisions (cycle reasons/alerts, per object
frame index, track id, reasons, alert, evidence codes) and every numeric score. ``compare`` requires
identical decision digests and reports the largest absolute score difference (BLAS kernels may differ in
the last bits; decisions must not).
"""

from __future__ import annotations

import os

for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import argparse
import hashlib
import json
import platform
import sys
import tempfile
from pathlib import Path

SCHEMA = "phantomguard.equivalence/1"
ATTACKS = (("T1", "A3", "moving"), ("T2", "A3", "static"), ("T3", "A4", "moving"), ("T4", "A3", "moving"))


def stream_record(results) -> dict:
    h = hashlib.sha256()
    scores: dict[str, float] = {}
    for r in results:
        objs = [(v.frame_index, v.track_id, list(v.reasons), bool(v.alert), sorted(e["reason"] for e in v.evidence))
                for v in r.objects]
        h.update(json.dumps([r.index, list(r.cycle_reasons), bool(r.cycle_alert), objs]).encode())
        for v in r.objects:
            for k, x in v.scores.items():
                scores[f"{v.frame_index}:{k}"] = float(x)
    return {"cycles": len(results), "decisions_sha256": h.hexdigest(), "scores": scores}


def run(cfg) -> dict:
    from phantomguard.commands.run_attack_eval import attack_jobs, attacked_stream, split_contexts
    from phantomguard.config import load_baseline, raw_path
    from phantomguard.detect.pipeline import Detector, load_artifacts
    from phantomguard.io.replay import ReplaySource

    context = split_contexts(cfg, "timeblock", "val")[0]
    baseline = load_baseline(context["baseline_path"])
    ae, lib = load_artifacts("timeblock", strict=True, cfg=cfg, baseline=baseline)
    streams = {}
    for f in cfg["data"]["files"]:
        streams[f"clean:{f}"] = stream_record(list(Detector(cfg, baseline, ae, lib).run(ReplaySource(raw_path(cfg, f)))))
    seg = max(context["test"], key=lambda s: s.hi - s.lo)
    for atype, level, motion in ATTACKS:
        job = next(j for j in attack_jobs(cfg, context, seg, [atype], [level], [11], 1)
                   if j["motion_case"] == motion and j["replay_provenance"] in {"not_applicable", "training"}
                   and j["replay_variant"] in {"not_applicable", "exact"})
        with tempfile.TemporaryDirectory() as tmp:
            src, _, _ = attacked_stream(cfg, context, seg, job, baseline, Path(tmp) / "labels.csv")
            streams[f"attacked:{atype}:{level}:{motion}:{seg.file}"] = stream_record(
                list(Detector(cfg, baseline, ae, lib).run(src)))
    import numpy
    return {"schema": SCHEMA, "platform": platform.platform(), "python": sys.version.split()[0],
            "numpy": numpy.__version__, "streams": streams}


def compare(a: dict, b: dict) -> dict:
    if a.get("schema") != SCHEMA or b.get("schema") != SCHEMA:
        raise SystemExit("equivalence files have an unsupported schema")
    out = {"platforms": [a["platform"], b["platform"]], "streams": {}, "ok": True}
    for name in sorted(set(a["streams"]) | set(b["streams"])):
        x, y = a["streams"].get(name), b["streams"].get(name)
        if x is None or y is None:
            out["streams"][name] = {"present": [x is not None, y is not None]}
            out["ok"] = False
            continue
        keys = set(x["scores"]) | set(y["scores"])
        diffs = [abs(x["scores"].get(k, float("nan")) - y["scores"].get(k, float("nan"))) for k in keys]
        missing = sum(d != d for d in diffs)
        same = x["decisions_sha256"] == y["decisions_sha256"] and x["cycles"] == y["cycles"]
        out["streams"][name] = {"cycles": [x["cycles"], y["cycles"]], "decisions_identical": same,
                                "max_abs_score_diff": max((d for d in diffs if d == d), default=0.0),
                                "score_keys_missing_on_one_side": missing}
        out["ok"] &= same and not missing
    return out


def main(argv=None) -> int:
    from phantomguard.config import add_path_arguments, config_from_args

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    r = sub.add_parser("run")
    r.add_argument("--output", type=Path, required=True)
    add_path_arguments(r)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    args = ap.parse_args(argv)
    if args.action == "run":
        res = run(config_from_args(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(res) + "\n", encoding="utf-8")
        print(json.dumps({k: v["decisions_sha256"][:16] for k, v in res["streams"].items()}, indent=2))
        return 0
    res = compare(json.loads(args.a.read_text(encoding="utf-8")), json.loads(args.b.read_text(encoding="utf-8")))
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
