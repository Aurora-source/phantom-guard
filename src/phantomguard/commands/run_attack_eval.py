#!/usr/bin/env python3
"""Reproducible held-out attack evaluation, with explicit unavailable/unsupported cells.

Missing Phase 2 still permits independent fresh clean evaluation and generates a
blocked attack matrix/summary. Exit 2 means required real attack validation is
blocked; it is never labelled a successful attack benchmark.
"""

from __future__ import annotations

from phantomguard.config import add_path_arguments, config_from_args, paths

import argparse
import csv
import json
import hashlib
import time
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

# Each worker handles tiny online matrices plus offline batches. Unbounded BLAS
# threads multiply workspace allocations across spawned Windows processes.
for _thread_setting in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_thread_setting, "1")

import numpy as np

from phantomguard.config import BASELINE_PATH, REPO_ROOT, effective_cfg, load_baseline, load_config, raw_path
from phantomguard.detect.autoencoder import load_iforest, score_iforest
from phantomguard.detect.common import LAYERS
from phantomguard.detect.pipeline import MODELS_DIR, Detector, load_artifacts
from phantomguard.eval.attack_adapter import (ATTACK_TYPES, LEVEL_NAMES, REPLAY_PROVENANCE, AttackUnavailable,
                                             UnsupportedAttack, attack_source, check_available, support_reason)
from phantomguard.eval.localize import control_alerts, localization_metrics, read_lineage, replay_lineage
from phantomguard.eval.metrics import assembly_stats, attack_metrics, latency_stats, lite, parse_labels, score
from phantomguard.eval.report import provenance, write_report, portable_path
from phantomguard.eval.splits import Segment, loso_folds, time_block_segments
from phantomguard.io.replay import ReplaySource, load_recorded_cycles

SUBSETS = {**{layer: (layer,) for layer in LAYERS}, "all": LAYERS,
           **{f"all-minus-{layer}": tuple(x for x in LAYERS if x != layer) for layer in LAYERS}}


def variants(scoring_cfg: dict, ablate: tuple[str, ...] = ()) -> dict:
    """name -> (layers, scoring cfg). Layer subsets as before, plus optional rule-level ablations
    ``all-minus-<CODE>`` that ignore one reason code in fusion (evaluation only)."""
    from phantomguard.eval.metrics import with_fusion

    out = {name: (layers, scoring_cfg) for name, layers in SUBSETS.items()}
    for code in ablate:
        out[f"all-minus-{code}"] = (LAYERS, with_fusion(scoring_cfg, {"ignore_codes": [code]}))
    return out


def job_seed(seed: int, file: str) -> int:
    """Compatibility with the accepted evaluator's deterministic per-recording seed API."""
    import zlib
    return int(np.random.SeedSequence([seed, zlib.crc32(file.encode())]).generate_state(1)[0])


def split_contexts(cfg: dict, selection: str, part: str) -> list[dict]:
    contexts = []
    if selection in {"all", "timeblock"}:
        tb = time_block_segments(cfg)
        contexts.append({"split": "timeblock", "fold": "timeblock", "tag": "timeblock", "part": part,
                         "baseline_path": paths(cfg).baseline, "train": tb["train"], "val": tb["val"], "test": tb[part]})
    if selection in {"all", "loso"}:
        for held, fold in loso_folds(cfg).items():
            tag = f"loso_{Path(held).stem}"
            contexts.append({"split": "loso", "fold": held, "tag": tag, "part": part,
                             "baseline_path": paths(cfg).processed / f"baseline_{tag}.json",
                             "train": fold["train"], "val": fold["val"], "test": fold[part]})
    return contexts


def job_identity(context: dict, segment: Segment) -> dict:
    return {"split": context["split"], "part": context["part"], "fold": context["fold"],
            "file": segment.file, "segment_lo": segment.lo, "segment_hi": segment.hi, "tag": context["tag"]}


def attack_jobs(cfg: dict, context: dict, segment: Segment, types=None, levels=None, seeds=None, runs=None) -> list[dict]:
    jobs = []
    for attack_type in (types or ATTACK_TYPES):
        for level in (levels or LEVEL_NAMES):
            motions = ("static", "moving") if attack_type in {"T1", "T2"} else ("moving",)
            provenances = REPLAY_PROVENANCE if attack_type == "T3" else ("not_applicable",)
            variants = ("exact", "translated") if attack_type == "T3" else ("not_applicable",)
            for motion in motions:
                for prov in provenances:
                    for variant in variants:
                        for seed in (seeds or cfg["attack"]["seeds"]):
                            for run in range(runs if runs is not None else cfg["eval"]["runs_per_cell"]):
                                effective = int(np.random.SeedSequence([int(seed), run]).generate_state(1)[0])
                                jobs.append({**job_identity(context, segment), "attack_type": attack_type,
                                             "level": level, "motion_case": motion, "replay_provenance": prov,
                                             "replay_variant": variant, "configured_seed": int(seed), "run": run,
                                             "effective_seed": effective})
    return jobs


def detect_stream(cfg: dict, baseline: dict, tag: str, source, *, with_iforest: bool = True):
    ae, library = load_artifacts(tag, strict=True, cfg=cfg, baseline=baseline)
    iso = load_iforest(tag, cfg=cfg, baseline=baseline, required=True) if with_iforest else None
    detector = Detector(cfg, baseline, ae, library, capture_windows=True)
    results = list(detector.run(source))
    if not results:
        raise ValueError("evaluation stream emitted no cycles")
    # Identical captured online features/ordering; sklearn is scored once outside latency.
    entries = [(cycle, fi, window) for cycle in results for fi, window in cycle.learned_windows.items()]
    if entries and iso is not None:
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


_CONTROL_CACHE: dict = {}


def control_for(cfg: dict, baseline: dict, context: dict, segment: Segment, scoring_cfg: dict,
                ablate: tuple[str, ...] = ()) -> dict:
    """Clean control of the same recording interval with the same frozen artifacts, per layer subset."""
    key = (context["tag"], segment.file, segment.lo, segment.hi, json.dumps(scoring_cfg["fusion"], sort_keys=True), ablate)
    if key not in _CONTROL_CACHE:
        source = ReplaySource(raw_path(cfg, segment.file), (segment.lo, segment.hi))
        cycles = detect_stream(cfg, baseline, context["tag"], source, with_iforest=False)
        lineage = replay_lineage(source)
        _CONTROL_CACHE[key] = {name: control_alerts(cycles, lineage, vcfg, layers)
                               for name, (layers, vcfg) in variants(scoring_cfg, ablate).items()}
    return _CONTROL_CACHE[key]


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
        scoring_cfg = effective_cfg(cfg, baseline)
        source = ReplaySource(raw_path(cfg, segment.file), (segment.lo, segment.hi))
        cycles = detect_stream(cfg, baseline, context["tag"], source)
        timing = {**latency_stats(cycles), **assembly_stats(cycles, cfg),
                  "p99_budget_ms": cfg["latency"]["p99_budget_ms"],
                  "measurement_workers": context.get("measurement_workers", 1)}
        timing["latency_budget_met"] = timing["p99_ms"] < timing["p99_budget_ms"]
        rows = []
        for name, layers in SUBSETS.items():
            metrics = score(cycles, scoring_cfg, layers)
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
    """Content-addressed checkpoint; changing code/data/artifacts selects a new cache."""
    job, directory = payload[3], Path(payload[5])
    digest = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()[:20]
    path = directory / f"{digest}_result.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    result = _run_attack(payload)
    # Retry integration failures; successful and explicitly unsupported outcomes persist.
    if not any(str(r.get("status", "")).startswith("blocked") for r in result[0]):
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, allow_nan=False) + "\n", encoding="utf-8")
    return result


def _run_attack(payload):
    cfg, context, segment, job, provider, label_dir = payload
    reason = support_reason(job["attack_type"], job["level"], job["motion_case"])
    if reason:
        return [{**job, "status": "unsupported", "reason": reason}], [], None
    try:
        baseline = load_baseline(context["baseline_path"])
        scoring_cfg = effective_cfg(cfg, baseline)
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
        lifecycle_path = labels_path.with_suffix(".instances.json")
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8")) if lifecycle_path.exists() else None
        lineage_path = labels_path.with_suffix(".lineage.csv")
        lineage = read_lineage(lineage_path) if lineage_path.is_file() else None
        ablate = tuple(job.get("ablate_codes", ()))
        control = control_for(cfg, baseline, context, segment, scoring_cfg, ablate) if lineage is not None else None
        if any(label.is_attack and (label.attack_type != job["attack_type"] or label.level != job["level"])
               for label in labels):
            raise ValueError("sidecar metadata differs from requested attack cell")
        rows, instance_rows, score_cache = [], [], {}
        timing = {**latency_stats(cycles), **assembly_stats(cycles, cfg),
                  "p99_budget_ms": cfg["latency"]["p99_budget_ms"],
                  "measurement_workers": context.get("measurement_workers", 1)}
        timing["latency_budget_met"] = timing["p99_ms"] < timing["p99_budget_ms"]
        for name, (layers, vcfg) in variants(scoring_cfg, ablate).items():
            metrics, instances = attack_metrics(cycles, labels, vcfg, layers,
                                                 copy_records=False, score_cache=score_cache)
            loc = None
            if lineage is not None:
                loc = localization_metrics(cycles, labels, lineage, control[name], vcfg, layers)
                metrics.update(loc.row)
            if lifecycle is not None:
                planned = lifecycle["planned_instances"]
                outcomes = Counter(entry["outcome"] for entry in lifecycle.get("plan_log", []))
                dropped = Counter(entry["reason"] for entry in lifecycle.get("dropped_slot_capacity", []))
                metrics.update(requested_instances=lifecycle["requested_instances"], scheduled_instances=len(planned),
                               scheduled_unobserved_instances=sum(not i["emitted"] for i in planned),
                               unscheduled_no_material_instances=lifecycle["requested_instances"]-len(planned),
                               planner_version=lifecycle.get("planner_version", 1),
                               planner_no_source_material_instances=outcomes.get("no_source_material", 0),
                               planner_exhausted_instances=outcomes.get("planner_exhausted", 0),
                               planner_window_exhausted_instances=outcomes.get("window_exhausted", 0),
                               planner_retried_instances=sum(e.get("attempts", 1) > 1 for e in lifecycle.get("plan_log", [])),
                               slot_capacity_dropped_instances=dropped.get("slot_capacity", 0),
                               slot_capacity_partial_instances=dropped.get("slot_capacity_partial", 0),
                               planning_seconds=lifecycle.get("planning_seconds"))
                by_id = {str(i["attack_id"]): i for i in planned}
                for instance in instances:
                    schedule = by_id.get(instance["attack_id"])
                    if schedule:
                        instance["right_censored"] = schedule["truncated_by_eof"]
                        instance["scheduled_note"] = schedule.get("note")
                metrics["right_censored_instances"] = sum(i["right_censored"] for i in instances)
            status = "ok" if metrics["attack_instances"] else "no_eligible_attack"
            rows.append({**job, "layers": name, "status": status, **metrics,
                         "labels_path": portable_path(labels_path, cfg), "replay_provenance_scope": provenance_scope, **timing})
            if loc is not None:
                for instance in instances:
                    extra = dict(loc.instances.get(str(instance["attack_id"]), {}))
                    extra["forged_reason_counts"] = json.dumps(extra.pop("forged_reason_counts", {}), sort_keys=True)
                    instance.update(extra)
            instance_rows.extend({**job, "layers": name, **instance} for instance in instances)
        return rows, instance_rows, stream_counts(cycles, source, job)
    except UnsupportedAttack as exc:
        return [{**job, "status": "unsupported_provider", "reason": str(exc)}], [], None
    except (FileNotFoundError, ValueError, RuntimeError, TypeError) as exc:
        return [{**job, "status": "blocked_integration", "reason": str(exc)}], [], None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--provider", help="module:function adapter for a contributor's Phase 2 API")
    parser.add_argument("--split", choices=("all", "timeblock", "loso"), default="all")
    parser.add_argument("--part", choices=("test", "val"), default="test")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--preflight", action="store_true", help="generate availability/provenance without detector runs")
    parser.add_argument("--smoke", action="store_true", help="four representative supported cells on chaotic time-block test; not a full benchmark")
    parser.add_argument("--types", help="comma-separated attack types to run, e.g. T1,T4 (default all)")
    parser.add_argument("--levels", help="comma-separated levels to run, e.g. A3,A4 (default all)")
    parser.add_argument("--files", help="comma-separated recording file names to run (default all)")
    parser.add_argument("--seeds", help="comma-separated configured attack seeds (default attack.seeds)")
    parser.add_argument("--runs-per-cell", type=int, help="repetitions per cell (default eval.runs_per_cell)")
    parser.add_argument("--ablate-codes", help="comma-separated reason codes; adds an 'all-minus-CODE' row set per code "
                                              "(evaluation-only rule ablation, e.g. COLOC,RCS_BAND)")
    add_path_arguments(parser)
    args = parser.parse_args(argv)
    cfg = config_from_args(args)
    args.output_dir = paths(cfg).output if args.output_dir else paths(cfg).output / "attack-eval"
    if args.smoke:
        if args.part != 'test':
            parser.error('--smoke uses the unchanged time-block test segment')
        args.split = 'timeblock'
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
    for context in contexts:
        context["measurement_workers"] = workers
    artifacts = [paths(cfg).baseline]
    for context in contexts:
        artifacts.extend([Path(context["baseline_path"]), paths(cfg).models / f"ae_{context['tag']}.npz",
                          paths(cfg).models / f"iforest_{context['tag']}.pkl", paths(cfg).models / f"replay_library_{context['tag']}.pkl"])
    manifest = provenance(cfg, list(dict.fromkeys(artifacts)))
    manifest.update({"requested_split": args.split, "requested_part": args.part, "workers": workers,
                     "evaluation_scope": "smoke: four representative cells only" if args.smoke else "full configured sweep",
                     "seeds": cfg["attack"]["seeds"], "runs_per_cell": cfg["eval"]["runs_per_cell"],
                     "effective_seed_rule": "numpy SeedSequence([configured_seed, run_index]).generate_state(1)[0]",
                     "phase2_provider": provider_name, "preflight_only": args.preflight,
                     "split_identities": [{k: ([asdict(s) for s in v] if k in {"train", "val", "test"}
                                               else portable_path(v, cfg) if isinstance(v, Path) else v)
                                           for k, v in context.items()} for context in contexts], "calibration": {}})
    for context in contexts:
        try:
            baseline = load_baseline(context["baseline_path"])
            manifest["calibration"][context["tag"]] = {k: v for k, v in baseline.items()
                                                        if k.startswith(("ae_", "iforest_", "learned_"))
                                                        or k in {"soft_quantile", "rr_scale", "fusion_mn", "_meta"}}
        except FileNotFoundError as exc:
            blockers.append(str(exc))
    runs, instances, clean, exclusions, clean_payloads, attack_payloads = [], [], [], [], [], []
    cache_contract = {k: manifest[k] for k in ("configuration_sha256", "recordings", "artifacts", "implementation_sha256")}
    cache_id = hashlib.sha256(json.dumps(cache_contract, sort_keys=True).encode()).hexdigest()[:20]
    label_dir = paths(cfg).output / "attack_eval" / cache_id
    manifest["checkpoint_contract_sha256"] = cache_id
    pick = lambda text, allowed: [x.strip() for x in text.split(",") if x.strip()] if text else None
    types, levels = pick(args.types, ATTACK_TYPES), pick(args.levels, LEVEL_NAMES)
    seeds = [int(x) for x in pick(args.seeds, None)] if args.seeds else None
    files = set(pick(args.files, None) or [])
    ablate_codes = tuple(pick(args.ablate_codes, None) or ())
    manifest["filters"] = {"types": types, "levels": levels, "files": sorted(files) or None, "seeds": seeds,
                           "runs_per_cell": args.runs_per_cell}
    for bad, allowed, label in ((types, ATTACK_TYPES, "types"), (levels, LEVEL_NAMES, "levels")):
        if bad and set(bad) - set(allowed):
            parser.error(f"unknown {label}: {sorted(set(bad) - set(allowed))}")
    for context in contexts:
        for segment in context["test"]:
            if files and segment.file not in files:
                continue
            if not args.preflight:
                clean_payloads.append((cfg, context, segment))
            for job in attack_jobs(cfg, context, segment, types, levels, seeds, args.runs_per_cell):
                if ablate_codes:
                    job["ablate_codes"] = list(ablate_codes)
                if args.smoke and not (segment.file == 'multiplePeopleChaotic.csv' and job['motion_case'] == 'moving'
                    and job['configured_seed'] == cfg['attack']['seeds'][0] and job['run'] == 0
                    and (job['attack_type'],job['level']) in {('T1','A2'),('T2','A2'),('T3','A3'),('T4','A3')}
                    and job['replay_provenance'] in {'not_applicable','training'}
                    and job['replay_variant'] in {'not_applicable','exact'}):
                    continue
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
        started = time.monotonic()
        for index, (rows, inst, counts) in enumerate(attack_results, 1):
            runs.extend(rows)
            instances.extend(inst)
            if counts:
                exclusions.append(counts)
            if index == 1 or index % 25 == 0:
                print(f"Attack runs {index}/{len(attack_payloads)} ({time.monotonic()-started:.0f}s); "
                      f"latest status {rows[0]['status']}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for rows, counts in pool.map(run_clean, clean_payloads):
                clean.extend(rows)
                exclusions.append(counts)
            started = time.monotonic()
            for index, (rows, inst, counts) in enumerate(pool.map(run_attack, attack_payloads), 1):
                runs.extend(rows)
                instances.extend(inst)
                if counts:
                    exclusions.append(counts)
                if index == 1 or index % 25 == 0:
                    print(f"Attack runs {index}/{len(attack_payloads)} ({time.monotonic()-started:.0f}s); "
                          f"latest status {rows[0]['status']}", flush=True)
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
