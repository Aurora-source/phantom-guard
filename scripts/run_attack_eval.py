#!/usr/bin/env python3
"""Reproducible held-out attack evaluation, with explicit unavailable/unsupported cells.

Missing Phase 2 still permits independent fresh clean evaluation and generates a
blocked attack matrix/summary. Exit 2 means required real attack validation is
blocked; it is never labelled a successful attack benchmark.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np

from phantomguard.config import BASELINE_PATH, REPO_ROOT, load_baseline, load_config, raw_path
from phantomguard.detect.autoencoder import load_iforest, score_iforest
from phantomguard.detect.common import LAYERS
from phantomguard.detect.pipeline import MODELS_DIR, Detector, load_artifacts
from phantomguard.eval.attack_adapter import (ATTACK_TYPES, LEVEL_NAMES, REPLAY_PROVENANCE, AttackUnavailable,
                                             UnsupportedAttack, attack_source, check_available, support_reason)
from phantomguard.eval.metrics import assembly_stats, attack_metrics, latency_stats, lite, parse_labels, score
from phantomguard.eval.report import provenance, write_report
from phantomguard.eval.splits import Segment, loso_folds, time_block_segments
from phantomguard.io.replay import ReplaySource, load_recorded_cycles

SUBSETS = {**{layer: (layer,) for layer in LAYERS}, "all": LAYERS,
           **{f"all-minus-{layer}": tuple(x for x in LAYERS if x != layer) for layer in LAYERS}}


def split_contexts(cfg: dict, selection: str, part: str) -> list[dict]:
    contexts = []
    if selection in {"all", "timeblock"}:
        tb = time_block_segments(cfg)
        contexts.append({"split": "timeblock", "fold": "timeblock", "tag": "timeblock", "part": part,
                         "baseline_path": BASELINE_PATH, "train": tb["train"], "val": tb["val"], "test": tb[part]})
    if selection in {"all", "loso"}:
        for held, fold in loso_folds(cfg).items():
            tag = f"loso_{Path(held).stem}"
            contexts.append({"split": "loso", "fold": held, "tag": tag, "part": part,
                             "baseline_path": REPO_ROOT / cfg["data"]["processed_dir"] / f"baseline_{tag}.json",
                             "train": fold["train"], "val": fold["val"], "test": fold[part]})
    return contexts


def job_identity(context: dict, segment: Segment) -> dict:
    return {"split": context["split"], "part": context["part"], "fold": context["fold"],
            "file": segment.file, "segment_lo": segment.lo, "segment_hi": segment.hi, "tag": context["tag"]}


def attack_jobs(cfg: dict, context: dict, segment: Segment) -> list[dict]:
    jobs = []
    for attack_type in ATTACK_TYPES:
        for level in LEVEL_NAMES:
            motions = ("static", "moving") if attack_type in {"T1", "T2"} else ("moving",)
            provenances = REPLAY_PROVENANCE if attack_type == "T3" else ("not_applicable",)
            variants = ("exact", "translated") if attack_type == "T3" else ("not_applicable",)
            for motion in motions:
                for prov in provenances:
                    for variant in variants:
                        for seed in cfg["attack"]["seeds"]:
                            for run in range(cfg["eval"]["runs_per_cell"]):
                                effective = int(np.random.SeedSequence([int(seed), run]).generate_state(1)[0])
                                jobs.append({**job_identity(context, segment), "attack_type": attack_type,
                                             "level": level, "motion_case": motion, "replay_provenance": prov,
                                             "replay_variant": variant, "configured_seed": int(seed), "run": run,
                                             "effective_seed": effective})
    return jobs


def detect_stream(cfg: dict, baseline: dict, tag: str, source):
    ae, library = load_artifacts(tag, strict=True, cfg=cfg, baseline=baseline)
    iso = load_iforest(tag, cfg=cfg, baseline=baseline, required=True)
    detector = Detector(cfg, baseline, ae, library, capture_windows=True)
    results = list(detector.run(source))
    if not results:
        raise ValueError("evaluation stream emitted no cycles")
    # Identical captured online features/ordering; sklearn is scored once outside latency.
    entries = [(cycle, fi, window) for cycle in results for fi, window in cycle.learned_windows.items()]
    if entries:
        scores = score_iforest(iso, [entry[2][0] for entry in entries])
        by_index = {v.frame_index: v for cycle in results for v in cycle.objects}
        for (_, fi, (_, moving)), value in zip(entries, scores):
            by_index[fi].scores["iforest"] = float(value)
            by_index[fi].score_status["iforest"] = "ok"
    cycles = [lite(result) for result in results]
    for result in cycles:
        for verdict in result.objects:
            if "iforest" not in verdict.score_status:
                verdict.score_status["iforest"] = verdict.score_status.get("ae", "unavailable")
    return cycles


def stream_counts(cycles, source: ReplaySource, identity: dict) -> dict:
    outcomes = Counter(f"{model}:{status}" for c in cycles for v in c.objects
                       for model, status in v.score_status.items())
    frame_reasons = Counter(reason for c in cycles for f in c.frames for reason in f.reasons)
    return {**identity, "status": "ok", "load_report_scope": "full recording; segment counts separately below",
            **source.report.as_dict(), "emitted_frames": sum(len(c.frames) for c in cycles),
            "decoded_objects": sum(f.kind == "object" for c in cycles for f in c.frames),
            "malformed_object_attempts": sum(f.kind == "malformed" for c in cycles for f in c.frames),
            "emitted_bad_id_frames": frame_reasons["BAD_ID"],
            "emitted_short_headers": frame_reasons["SHORT_HEADER"],
            "emitted_missing_header_cycles": sum("NO_HEADER" in c.cycle_reasons for c in cycles),
            "emitted_count_mismatch_cycles": sum("COUNT_MISMATCH" in c.cycle_reasons for c in cycles),
            "emitted_duplicate_slot_object_frames": frame_reasons["DUP_SLOT"],
            "emitted_range_order_object_frames": frame_reasons["RANGE_ORDER"],
            "emitted_burst_gap_object_frames": frame_reasons["BURST_GAP"],
            "out_of_roi_objects": sum(not v.in_roi for c in cycles for v in c.objects
                                      if any(f.frame_index == v.frame_index and f.kind == "object" for f in c.frames)),
            "score_outcomes": json.dumps(dict(outcomes), sort_keys=True),
            "layer_status": json.dumps(cycles[0].layer_status, sort_keys=True)}


def run_clean(payload):
    cfg, context, segment = payload
    identity = job_identity(context, segment)
    try:
        baseline = load_baseline(context["baseline_path"])
        source = ReplaySource(raw_path(cfg, segment.file), (segment.lo, segment.hi))
        cycles = detect_stream(cfg, baseline, context["tag"], source)
        timing = {**latency_stats(cycles), **assembly_stats(cycles, cfg),
                  "p99_budget_ms": cfg["latency"]["p99_budget_ms"]}
        timing["latency_budget_met"] = timing["p99_ms"] < timing["p99_budget_ms"]
        rows = []
        for name, layers in SUBSETS.items():
            metrics = score(cycles, cfg, layers)
            rows.append({**identity, "layers": name, "status": "ok", **metrics.row(),
                         "minutes_exact": metrics.minutes, "flagged_count": metrics.flagged,
                         "roi_object_cycles": metrics.roi_obj_cycles,
                         "roi_moving_object_cycles": metrics.roi_moving_obj_cycles,
                         "out_of_roi_object_cycles": metrics.obj_cycles-metrics.roi_obj_cycles, **timing})
        return rows, stream_counts(cycles, source, identity)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        row = {**identity, "layers": "all", "status": "blocked_artifacts_or_data", "reason": str(exc)}
        return [row], row


def run_attack(payload):
    cfg, context, segment, job, provider, label_dir = payload
    reason = support_reason(job["attack_type"], job["level"], job["motion_case"])
    if reason:
        return [{**job, "status": "unsupported", "reason": reason}], [], None
    try:
        baseline = load_baseline(context["baseline_path"])
        source = ReplaySource(raw_path(cfg, segment.file), (segment.lo, segment.hi))
        from hashlib import sha256
        digest = sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()[:20]
        labels_path = Path(label_dir) / f"{digest}_labels.csv"
        unseen = []
        provenance_scope = "not_applicable"
        if job["attack_type"] == "T3":
            provenance_scope = job["replay_provenance"]
            if job["replay_provenance"] == "unseen":
                # Time-block recordings all contributed training portions. The owner
                # permits unseen *portions* of another file; do not call them unseen files.
                candidates = time_block_segments(cfg)["test"]
                unseen = [s for s in candidates if s.file != segment.file]
                provenance_scope = "held_out_segments_of_seen_recordings"
        if context["split"] == "loso":
            # Held recording stays unseen even when evaluating validation tails of
            # the other three files. Never substitute the validation victim as unseen.
            unseen = loso_folds(cfg)[context["fold"]]["test"]
            if job["attack_type"] == "T3" and job["replay_provenance"] == "unseen":
                provenance_scope = "wholly_unseen_recording"
        attacked = attack_source(source, cfg, baseline, attack_type=job["attack_type"], level=job["level"],
                                 seed=job["effective_seed"], run_index=job["run"], train_segments=context["train"],
                                 replay_provenance=job["replay_provenance"] if job["attack_type"] == "T3" else "training",
                                 unseen_segments=unseen, labels_path=labels_path, motion_case=job["motion_case"],
                                 replay_variant=job["replay_variant"] if job["attack_type"] == "T3" else "exact",
                                 provider=provider)
        cycles = detect_stream(cfg, baseline, context["tag"], attacked)
        if not labels_path.is_file():
            raise ValueError(f"attacker did not write sidecar {labels_path}")
        with labels_path.open(newline="", encoding="utf-8") as stream:
            labels = parse_labels(csv.DictReader(stream))
        if any(label.is_attack and (label.attack_type != job["attack_type"] or label.level != job["level"])
               for label in labels):
            raise ValueError("sidecar metadata differs from requested attack cell")
        rows, instance_rows = [], []
        timing = {**latency_stats(cycles), **assembly_stats(cycles, cfg),
                  "p99_budget_ms": cfg["latency"]["p99_budget_ms"]}
        timing["latency_budget_met"] = timing["p99_ms"] < timing["p99_budget_ms"]
        for name, layers in SUBSETS.items():
            metrics, instances = attack_metrics(cycles, labels, cfg, layers)
            status = "ok" if metrics["attack_instances"] else "no_eligible_attack"
            rows.append({**job, "layers": name, "status": status, **metrics,
                         "labels_path": str(labels_path), "replay_provenance_scope": provenance_scope, **timing})
            instance_rows.extend({**job, "layers": name, **instance} for instance in instances)
        return rows, instance_rows, stream_counts(cycles, source, job)
    except UnsupportedAttack as exc:
        return [{**job, "status": "unsupported_provider", "reason": str(exc)}], [], None
    except (FileNotFoundError, ValueError, RuntimeError, TypeError) as exc:
        return [{**job, "status": "blocked_integration", "reason": str(exc)}], [], None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "results")
    parser.add_argument("--provider", help="module:function adapter for a contributor's Phase 2 API")
    parser.add_argument("--split", choices=("all", "timeblock", "loso"), default="all")
    parser.add_argument("--part", choices=("test", "val"), default="test")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--preflight", action="store_true", help="generate availability/provenance without detector runs")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    workers = args.workers if args.workers is not None else cfg["eval"]["workers"]
    if workers < 1 or cfg["eval"]["runs_per_cell"] < 1 or not cfg["attack"]["seeds"]:
        parser.error("workers/runs must be positive and configured seeds must be nonempty")
    blockers = []
    missing = [str(raw_path(cfg, f)) for f in cfg["data"]["files"] if not raw_path(cfg, f).is_file()]
    if missing:
        blockers.append("Raw recordings unavailable: " + ", ".join(missing))
    try:
        provider_name = check_available(args.provider)
        phase2 = True
    except AttackUnavailable as exc:
        blockers.append(str(exc))
        provider_name, phase2 = None, False
    contexts = split_contexts(cfg, args.split, args.part) if not missing else []
    artifacts = [BASELINE_PATH]
    for context in contexts:
        artifacts.extend([Path(context["baseline_path"]), MODELS_DIR / f"ae_{context['tag']}.npz",
                          MODELS_DIR / f"iforest_{context['tag']}.pkl", MODELS_DIR / f"replay_library_{context['tag']}.pkl"])
    manifest = provenance(cfg, list(dict.fromkeys(artifacts)))
    manifest.update({"requested_split": args.split, "requested_part": args.part, "workers": workers,
                     "seeds": cfg["attack"]["seeds"], "runs_per_cell": cfg["eval"]["runs_per_cell"],
                     "effective_seed_rule": "numpy SeedSequence([configured_seed, run_index]).generate_state(1)[0]",
                     "phase2_provider": provider_name, "preflight_only": args.preflight,
                     "split_identities": [{k: ([asdict(s) for s in v] if k in {"train", "val", "test"}
                                               else str(v) if isinstance(v, Path) else v)
                                           for k, v in context.items()} for context in contexts], "calibration": {}})
    for context in contexts:
        try:
            baseline = load_baseline(context["baseline_path"])
            manifest["calibration"][context["tag"]] = {k: v for k, v in baseline.items()
                                                        if k.startswith(("ae_", "iforest_", "learned_"))
                                                        or k in {"soft_quantile", "rr_scale", "_meta"}}
        except FileNotFoundError as exc:
            blockers.append(str(exc))
    runs, instances, clean, exclusions, clean_payloads, attack_payloads = [], [], [], [], [], []
    label_dir = REPO_ROOT / "runs" / "attack_eval"
    for context in contexts:
        for segment in context["test"]:
            if not args.preflight:
                clean_payloads.append((cfg, context, segment))
            for job in attack_jobs(cfg, context, segment):
                unsupported = support_reason(job["attack_type"], job["level"], job["motion_case"])
                if unsupported:
                    runs.append({**job, "status": "unsupported", "reason": unsupported})
                elif not phase2:
                    runs.append({**job, "status": "blocked_phase2", "reason": blockers[0] if blockers else "missing provider"})
                elif args.preflight:
                    runs.append({**job, "status": "not_executed_preflight"})
                else:
                    attack_payloads.append((cfg, context, segment, job, args.provider, label_dir))
    if missing:
        runs.append({"status": "blocked_data", "reason": blockers[0]})
    if workers == 1:
        clean_results = map(run_clean, clean_payloads)
        attack_results = map(run_attack, attack_payloads)
        for rows, counts in clean_results:
            clean.extend(rows)
            exclusions.append(counts)
        for rows, inst, counts in attack_results:
            runs.extend(rows)
            instances.extend(inst)
            if counts:
                exclusions.append(counts)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for rows, counts in pool.map(run_clean, clean_payloads):
                clean.extend(rows)
                exclusions.append(counts)
            for rows, inst, counts in pool.map(run_attack, attack_payloads):
                runs.extend(rows)
                instances.extend(inst)
                if counts:
                    exclusions.append(counts)
    failures = [r for r in clean+runs if str(r.get("status", "")).startswith("blocked")]
    if any(r.get("status") == "blocked_artifacts_or_data" for r in clean):
        blockers.append("Some clean segments could not run all detector layers; see per-run reasons.")
    if any(r.get("status") == "blocked_integration" for r in runs):
        blockers.append("Some attack runs failed integration or label validation; see per-run reasons.")
    manifest["blockers"] = list(dict.fromkeys(blockers))
    summary = write_report(args.output_dir, manifest, runs, instances, clean, exclusions)
    print(f"Wrote {summary}; attack statuses={dict(Counter(r['status'] for r in runs))}")
    return 2 if blockers or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
