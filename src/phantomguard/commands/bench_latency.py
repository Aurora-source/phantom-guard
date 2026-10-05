#!/usr/bin/env python3
"""Serial per-cycle detector latency benchmark (offline; never run on the deployment server).

    python -m phantomguard.commands.bench_latency --tag timeblock --output runs/latency/latency.json

One process, one thread, BLAS pinned to one thread, artifacts loaded before timing, the first
``--warmup`` cycles of every stream excluded. Streams: every configured recording in full (clean), plus
one attacked stream per ``--attack TYPE:LEVEL:MOTION`` on the time-block test segment of the busiest
recording (forged objects raise the per-cycle object count).

Two clocks are reported and never mixed:
* ``wall_*``  perf_counter around Detector.process_cycle (+ assembly): what a live loop would wait;
              includes preemption by other processes, so it depends on machine load.
* ``thread_cpu_*`` time.thread_time around process_cycle: CPU actually spent by the detector thread.
              Its resolution is platform dependent (~15.6 ms on Windows); per-cycle percentiles are
              marked invalid when the clock is coarser than ``--max-cpu-resolution-ms``, and only the
              mean CPU per cycle (total / cycles) is then reported for that platform.
The 10 ms budget is judged on the detector p99 of the valid CPU clock where available, else wall.
"""

from __future__ import annotations

import os

for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

import numpy as np

from phantomguard.config import add_path_arguments, config_from_args, effective_cfg, load_baseline, paths, raw_path
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.io.replay import ReplaySource

BENCH_SCHEMA = "phantomguard.latency/1"


def pct(values, qs=(50, 95, 99, 99.9)) -> dict:
    a = np.asarray(values, dtype=float)
    if not len(a):
        return {}
    return {**{f"p{q:g}": round(float(np.percentile(a, q)), 4) for q in qs}, "max": round(float(a.max()), 4),
            "mean": round(float(a.mean()), 4)}


def measure(detector: Detector, source, warmup: int) -> dict:
    wall, det_wall, cpu, objs = [], [], [], []
    started_cpu, started_wall = time.thread_time(), time.perf_counter()
    for i, r in enumerate(detector.run(source)):
        if i < warmup:
            continue
        wall.append(r.latency_ms)
        det_wall.append(r.detector_cpu_ms)
        cpu.append(r.detector_thread_cpu_ms)
        objs.append(len(r.objects))
    return {"cycles": len(wall), "wall": wall, "det_wall": det_wall, "cpu": cpu, "objects": objs,
            "total_thread_cpu_s": time.thread_time() - started_cpu, "total_wall_s": time.perf_counter() - started_wall}


def effective_resolution_ms(samples) -> float:
    """Smallest positive step between distinct per-cycle CPU samples. get_clock_info may report 100 ns on
    Windows while GetThreadTimes only advances in ~15.6 ms scheduler ticks; the samples reveal that."""
    vals = np.unique(np.round(np.asarray(samples, dtype=float), 6))
    steps = np.diff(vals)
    steps = steps[steps > 0]
    return float(steps.min()) if len(steps) else float("inf")


def summarize(name: str, m: dict, cpu_valid: bool) -> dict:
    objs = np.asarray(m["objects"])
    out = {"stream": name, "cycles": m["cycles"], "objects_per_cycle": pct(objs, (50, 99)),
           "wall_detector_plus_assembly_ms": pct(m["wall"]), "wall_detector_ms": pct(m["det_wall"]),
           "thread_cpu_mean_ms_per_cycle": round(1000 * m["total_thread_cpu_s"] / max(1, m["cycles"]), 4),
           "cpu_to_wall_ratio": round(m["total_thread_cpu_s"] / m["total_wall_s"], 4) if m["total_wall_s"] else None}
    if cpu_valid:
        out["thread_cpu_detector_ms"] = pct(m["cpu"])
    if len(objs):
        top = objs >= np.percentile(objs, 90)
        out["wall_detector_ms_busiest_decile_p99"] = round(float(np.percentile(np.asarray(m["det_wall"])[top], 99)), 4)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="timeblock")
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--attack", action="append", default=None,
                    help="TYPE:LEVEL:MOTION attacked stream(s) (default T2:A3:moving, T1:A4:moving)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--max-cpu-resolution-ms", type=float, default=0.1)
    ap.add_argument("--output", type=Path)
    add_path_arguments(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    p = paths(cfg)
    bpath = p.baseline if args.tag == "timeblock" else p.processed / f"baseline_{args.tag}.json"
    baseline = load_baseline(bpath)
    ae, lib = load_artifacts(args.tag, strict=True, cfg=cfg, baseline=baseline)
    res = time.get_clock_info("thread_time").resolution * 1000
    # Calibrate validity on the first clean stream: reported resolution is not trusted on its own.
    probe = measure(Detector(cfg, baseline, ae, lib), ReplaySource(raw_path(cfg, cfg["data"]["files"][0])), args.warmup)
    eff = effective_resolution_ms(probe["cpu"])
    cpu_valid = max(res, eff) <= args.max_cpu_resolution_ms
    streams = []
    for f in cfg["data"]["files"]:
        m = probe if f == cfg["data"]["files"][0] else measure(Detector(cfg, baseline, ae, lib),
                                                               ReplaySource(raw_path(cfg, f)), args.warmup)
        streams.append(summarize(f"clean:{f}", m, cpu_valid))
        print(f"clean {f}: wall p99 {streams[-1]['wall_detector_ms']['p99']} ms", flush=True)
    from phantomguard.commands.run_attack_eval import split_contexts
    from phantomguard.eval.attack_adapter import attack_source

    context = split_contexts(cfg, "timeblock", "test")[0]
    busiest = max(context["test"], key=lambda s: s.hi - s.lo)
    for spec in args.attack or ["T2:A3:moving", "T1:A4:moving"]:
        atype, level, motion = spec.split(":")
        with tempfile.TemporaryDirectory() as tmp:
            src = attack_source(ReplaySource(raw_path(cfg, busiest.file), (busiest.lo, busiest.hi)), cfg, baseline,
                                attack_type=atype, level=level, seed=args.seed, train_segments=context["train"],
                                labels_path=Path(tmp) / "labels.csv", motion_case=motion)
            m = measure(Detector(cfg, baseline, ae, lib), src, args.warmup)
        streams.append(summarize(f"attacked:{spec}:{busiest.file}", m, cpu_valid))
        print(f"attacked {spec}: wall p99 {streams[-1]['wall_detector_ms']['p99']} ms", flush=True)
    worst_wall = max(s["wall_detector_ms"]["p99"] for s in streams)
    worst_cpu = max(s["thread_cpu_detector_ms"]["p99"] for s in streams) if cpu_valid else None
    budget = float(cfg["latency"]["p99_budget_ms"])
    try:
        import importlib.metadata as md
        versions = {n: md.version(n) for n in ("numpy", "scipy")}
    except Exception:  # pragma: no cover - metadata is informational
        versions = {}
    report = {"schema": BENCH_SCHEMA, "tag": args.tag, "baseline": str(bpath),
              "profile": ((baseline.get("detector_contract") or {}).get("value") or {}).get("profile", "legacy"),
              "hardware": {"platform": platform.platform(), "processor": platform.processor(), "machine": platform.machine(),
                           "logical_cpus": os.cpu_count(), "python": platform.python_version(), **versions,
                           "blas_threads": os.environ.get("OPENBLAS_NUM_THREADS")},
              "clocks": {"thread_time_resolution_ms": res, "thread_time_effective_resolution_ms": round(eff, 6),
                         "thread_cpu_percentiles_valid": cpu_valid,
                         "wall": "perf_counter; includes preemption"},
              "warmup_cycles_excluded_per_stream": args.warmup, "streams": streams,
              "budget_ms": budget, "worst_stream_wall_detector_p99_ms": worst_wall,
              "worst_stream_thread_cpu_detector_p99_ms": worst_cpu,
              "budget_met_on": "thread_cpu" if cpu_valid else "wall (CPU clock too coarse on this platform)",
              "budget_met": (worst_cpu if cpu_valid else worst_wall) < budget}
    out = args.output or p.output / "latency" / "latency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("worst_stream_wall_detector_p99_ms", "worst_stream_thread_cpu_detector_p99_ms",
                                             "budget_met_on", "budget_met")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
