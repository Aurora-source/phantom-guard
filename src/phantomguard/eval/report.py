"""Generated evaluation CSVs, provenance manifests and an honest Markdown report."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import platform
import os
import subprocess
from collections import Counter
from collections import defaultdict
import statistics
from datetime import datetime, timezone
from pathlib import Path

from phantomguard.config import REPO_ROOT, raw_path, paths


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def portable_path(path: Path, cfg: dict | None = None) -> str:
    """Legacy repo-relative paths, or canonical configured payload paths.

    Keep the accepted one-argument interface for callers; generated manifests
    provide config so external data/model roots do not expose developer paths.
    """
    p = Path(path).resolve()
    if cfg is None:
        try:
            return p.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return p.as_posix()
    locations = [(paths(cfg).models, 'models'), (paths(cfg).processed, 'data/processed'),
                 (paths(cfg).raw, 'data/raw'), (paths(cfg).output, 'runs'), (paths(cfg).root, '')]
    for anchor, label in locations:
        try:
            return (Path(label) / p.relative_to(anchor)).as_posix()
        except ValueError:
            pass
    return path.name


def portable_config(cfg: dict) -> dict:
    import copy
    result = copy.deepcopy(cfg)
    result.pop('_paths', None)
    result.pop('paths', None)
    result['data']['raw_dir'] = 'data/raw'
    result['data']['processed_dir'] = 'data/processed'
    return result


def provenance(cfg: dict, artifacts: list[Path]) -> dict:
    def git(*args):
        try:
            proc = subprocess.run(["git", *args], cwd=paths(cfg).root, capture_output=True, text=True, check=False)
        except OSError:
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None
    packages = {}
    for name in ("numpy", "scipy", "scikit-learn", "torch", "matplotlib", "pandas"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unavailable"
    return {"generated_utc": datetime.now(timezone.utc).isoformat(), "git_head": git("rev-parse", "HEAD"),
            "git_branch": git("branch", "--show-current"), "git_dirty": bool(git("status", "--porcelain")),
            "configuration": portable_config(cfg), "configuration_sha256": hashlib.sha256(json.dumps(portable_config(cfg), sort_keys=True).encode()).hexdigest(),
            "python": platform.python_version(), "platform": platform.platform(), "packages": packages,
            "logical_cpu_count": os.cpu_count(),
            "blas_environment": {k: os.environ.get(k) for k in
                                 ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")},
            "processing_timer": "perf_counter elapsed time for grouping/decoding and detector CPU work; source I/O excluded",
            "recordings": [{"file": f, "path": "data/raw/" + f, "sha256": sha256(raw_path(cfg, f))}
                           for f in cfg["data"]["files"]],
            "artifacts": [{"path": portable_path(p, cfg), "sha256": sha256(p), "details": artifact_details(p)} for p in artifacts],
            "implementation_sha256": {p.relative_to(REPO_ROOT).as_posix(): sha256(p)
                                      for directory in (REPO_ROOT / "src", REPO_ROOT / "scripts", REPO_ROOT / "tools")
                                      for p in sorted(directory.rglob("*.py"))},
            "model_selection": "AE fixed for NumPy online deployment before attack testing; IF is an offline comparator",
            "historical_test_disclosure": "Test results had previously been inspected before the historical validation calibration; "
                                          "see docs/decisions.md. No new test-driven selection is performed."}


def artifact_details(path: Path) -> dict:
    """Describe actual local model settings without refitting or assuming defaults."""
    if not path.is_file():
        return {"status": "missing"}
    try:
        if path.name.startswith("ae_") and path.suffix == ".npz":
            import numpy as np
            with np.load(path, allow_pickle=True) as archive:
                params = archive["params"].item()
            return {"status": "ok", "metadata": params.get("metadata"),
                    "feature_width": len(params["mean"]),
                    "layer_widths": [list(w.shape) for w, _ in params["layers"]],
                    "train_seconds": params.get("train_seconds"),
                    "epochs_completed": params.get("epochs_completed"),
                    "stopping_reason": params.get("stopping_reason")}
        if path.name.startswith("iforest_") and path.suffix == ".pkl":
            import pickle
            artifact = pickle.loads(path.read_bytes())
            model = artifact["model"]
            return {"status": "ok", "metadata": artifact.get("metadata"),
                    "feature_width": int(model.n_features_in_), "trees": len(model.estimators_),
                    "max_samples": int(model.max_samples_), "max_features": model.max_features,
                    "contamination": model.contamination, "bootstrap": model.bootstrap,
                    "random_state": model.random_state}
        if path.name.startswith("replay_library_") and path.suffix == ".pkl":
            import pickle
            artifact = pickle.loads(path.read_bytes())
            return {"status": "ok", "metadata": artifact.get("metadata"),
                    "fingerprints": len(artifact["fingerprints"])}
    except Exception as exc:
        # Availability/provenance collection must report corrupt local artifacts;
        # scoring separately validates them and blocks the affected run.
        return {"status": "invalid", "reason": str(exc)}
    return {"status": "configuration"}


def write_csv(path: Path, rows: list[dict], default_columns: tuple[str, ...] = ("status",)) -> None:
    columns = list(dict.fromkeys(k for row in rows for k in row)) or list(default_columns)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _table(rows: list[dict], columns: tuple[str, ...]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        vals = []
        for key in columns:
            value = row.get(key)
            vals.append("unmeasured" if value is None else (f"{value:.4g}" if isinstance(value, float)
                                                           else str(value).replace("|", "\\|")))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def aggregate_runs(runs: list[dict], instances: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Pool integer denominators; AUROC is explicitly a mean of valid per-run AUROCs."""
    groups, times = defaultdict(list), defaultdict(list)
    for row in runs:
        if row.get("status") == "ok":
            groups[tuple(row.get(k) for k in keys)].append(row)
    for row in instances:
        if row.get("ttd_cycles") is not None:
            times[tuple(row.get(k) for k in keys)].append(row["ttd_cycles"])
    output = []
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        row = dict(zip(keys, key), completed_runs=len(rows))
        for name in ("attack_instances", "detected_instances", "identified_instances", "forged_object_frames",
                     "identified_object_frames", "undetected_instances", "right_censored_instances"):
            row[name] = sum(r[name] for r in rows)
        row["attack_instance_detection_rate"] = row["detected_instances"] / row["attack_instances"]
        row["attack_instance_identification_rate"] = row["identified_instances"] / row["attack_instances"]
        row["object_detection_rate"] = row["identified_object_frames"] / row["forged_object_frames"] if row["forged_object_frames"] else None
        row["median_ttd_cycles_detected"] = statistics.median(times[key]) if times[key] else None
        row["worst_run_p99_ms"] = max((r.get("p99_ms", 0) for r in rows), default=None)
        row["runs_over_latency_budget"] = sum(not r.get("latency_budget_met", False) for r in rows)
        for model in ("ae", "iforest"):
            valid = [r[f"{model}_auroc"] for r in rows if r.get(f"{model}_auroc") is not None]
            row[f"{model}_mean_run_auroc"] = statistics.mean(valid) if valid else None
            row[f"{model}_auroc_runs"] = len(valid)
        output.append(row)
    return output


def deck_evidence(runs: list[dict], clean: list[dict]) -> list[dict]:
    """Describe measured claim evidence without selecting a model or operating point."""
    out = []
    for claim, layer in (("Timing signature", "protocol"), ("Kinematic plausibility", "kinematic"),
                         ("Replay fingerprints", "replay"), ("Learned normal", "learned")):
        rows = [r for r in runs if r.get("status") == "ok" and r.get("layers") == layer]
        rates = []
        for level in ("A0", "A1", "A2", "A3", "A4"):
            lr = [r for r in rows if r["level"] == level]
            n = sum(r["attack_instances"] for r in lr)
            if n:
                rates.append(f"{level}: {sum(r['identified_instances'] for r in lr)/n:.1%}")
        evidence = "measured sensitivity, including misses" if rows else "attack sensitivity unmeasured"
        if layer == "learned" and rows:
            values = [r["ae_auroc"] for r in rows if r.get("ae_auroc") is not None]
            evidence = (f"mean valid per-run AE AUROC {statistics.mean(values):.3f}; " if values else "AUROC unavailable; ")
            evidence += "corroborating policy adds no incremental boolean alerts"
        row = {"claim": claim, "evidence": evidence, "direct_instance_identification_by_level": "; ".join(rates) or "unmeasured"}
        for split in ("timeblock", "loso"):
            cr = [r for r in clean if r.get("status") == "ok" and r.get("layers") == layer
                  and r.get("split") == split and r.get("part") == "test"]
            minutes = sum(r["minutes_exact"] for r in cr)
            row[f"clean_{split}_alerts_per_minute"] = sum(r["alert_events"] for r in cr)/minutes if minutes else None
        out.append(row)
    out += [{"claim": "RCS versus range", "evidence": "population/same-person evidence in rcs_vs_range.md; per-track evidence underpowered",
             "direct_instance_identification_by_level": "RCS sensitivity not isolated from other physics checks"},
            {"claim": "No labelled attack data needed", "evidence": "training/calibration provenance and leakage regression tests; labels used only for measurement",
             "direct_instance_identification_by_level": "not a sensitivity claim"}]
    return out


def write_report(output: Path, manifest: dict, runs: list[dict], instances: list[dict],
                 clean: list[dict], exclusions: list[dict]) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "attack_eval_runs.csv", runs)
    write_csv(output / "attack_eval_instances.csv", instances, ("attack_id", "detected", "ttd_cycles"))
    write_csv(output / "attack_eval_clean.csv", clean, ("split", "part", "file", "layers", "status"))
    write_csv(output / "attack_eval_exclusions.csv", exclusions, ("split", "file", "status"))
    manifest["run_status_counts"] = dict(Counter(row.get("status", "unknown") for row in runs))
    manifest["completed_attack_runs"] = sum(row.get("status") == "ok" and row.get("layers") == "all" for row in runs)
    manifest["completed_clean_runs"] = sum(row.get("status") == "ok" and row.get("layers") == "all" for row in clean)
    (output / "attack_eval_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                                                    encoding="utf-8")
    successful = [r for r in runs if r.get("status") == "ok"]
    matrix = aggregate_runs(runs, instances, ("split", "attack_type", "level", "layers", "motion_case",
                                              "replay_provenance", "replay_variant"))
    layers_matrix = aggregate_runs(runs, instances, ("split", "attack_type", "level", "layers"))
    write_csv(output / "attack_eval_matrix.csv", matrix)
    write_csv(output / "attack_eval_layers.csv", layers_matrix)
    lines = ["# Phantom Guard workflow evaluation", "", "Generated by `scripts/run_attack_eval.py`; configuration and full provenance are in "
             "`attack_eval_manifest.json`. Every requested run and unsupported/blocked cell appears in `attack_eval_runs.csv`.", "",
             f"Completed attack runs: {manifest['completed_attack_runs']}. Completed clean segment evaluations: "
             f"{manifest['completed_clean_runs']}.", ""]
    lines += ["Run-row outcomes: " + str(manifest["run_status_counts"]) + ". Unsupported/no-material runs are not passes.", ""]
    if manifest.get("blockers"):
        lines += ["## Validation limitations", ""] + [f"- {b}" for b in manifest["blockers"]] + [""]
    lines += ["## Measurements", ""]
    pooled = []
    for split in ("timeblock", "loso"):
        rows = [r for r in clean if r.get("status") == "ok" and r.get("split") == split
                and r.get("part") == "test" and r.get("layers") == "all"]
        if rows:
            n = sum(r["object_cycles"] for r in rows)
            minutes = sum(r["minutes_exact"] for r in rows)
            alerts = sum(r["alert_events"] for r in rows)
            pooled.append({"split": split, "object_cycles": n, "minutes": minutes,
                           "false_alerts_per_minute": alerts / minutes if minutes else None,
                           "flagged_per_object_cycle": sum(r["flagged_count"] for r in rows) / n if n else None,
                           "under_1_target_met": alerts/minutes < 1.0 if minutes else False})
    lines += [_table(pooled, ("split", "object_cycles", "minutes", "false_alerts_per_minute", "flagged_per_object_cycle", "under_1_target_met"))
              if pooled else "Fresh clean rates are unmeasured; blocked checks are not passes.", "",
              "Historical clean results were 4.73 alerts/minute for time-block test and 5.87 for LOSO. "
              "They are historical observations, not targets. The decisions log discloses that test had already been "
              "inspected before historical calibration. Fresh test results do not select thresholds or models.", ""]
    latency_rows = [r for r in clean if r.get("status") == "ok" and r.get("layers") == "all"]
    if latency_rows:
        lines += ["CPU processing (each segment separately; these p99 values are not pooled):", "",
                  _table(latency_rows, ("split", "file", "p99_ms", "p99_budget_ms", "latency_budget_met", "assembly_p99_ms")), ""]
    lines += ["## Attack type × level × layer", ""]
    if successful:
        lines += [_table(layers_matrix, ("split", "attack_type", "level", "layers",
                                      "attack_instances", "attack_instance_detection_rate", "object_detection_rate",
                                      "median_ttd_cycles_detected", "ae_mean_run_auroc", "iforest_mean_run_auroc")), "",
                  "Rates pool integer instance/object denominators across runs. AUROCs above are means of valid "
                  "per-run AUROCs, not AUROC of pooled scores. `attack_eval_matrix.csv` retains static/moving, "
                  "provenance and exact/translated variants; CSV run and instance files retain every seed/repetition.", "",
                  "Attacked CPU latency is in the per-run CSV and aggregated matrix (`worst_run_p99_ms`, "
                  "`runs_over_latency_budget`); clean latency below is a separate measurement.", ""]
        evading = [r for r in successful if r.get("layers") == "all" and r.get("attack_instances", 0)
                   and not r.get("detected_instances")]
        lines += [f"Fully evading observed runs: {len(evading)}. They are retained in the CSV; no evasion is suppressed.", ""]
    else:
        lines += ["Attack detection, evasion, time-to-detect and AE/IF AUROC are **unmeasured**. "
                  "No fixture is used as a headline attacker or performance result.", ""]
    lines += ["Every layer and leave-one-layer-out ablation uses the same emitted stream, label indices and windows. "
              "Static/moving object denominators are separate; T3 replay provenance separates earlier stream, training "
              "recordings and unseen evaluation material. Time-block 'unseen' means held-out segments of another "
              "recording with a seen training portion; LOSO 'unseen' means the wholly held-out recording. The scope "
              "is explicit in run rows. Unseen replay material is available only to the attacker, "
              "never added to the defender library.", "",
              "## Denominators and matching", "",
              "- Sidecars must cover every final emitted frame index exactly once. Slot IDs never join labels. "
              "Headers, malformed frames and duplicate-slot objects retain separate indices.",
              "- Instance detection means a hard cycle alert or persistent object alert during the first-to-last "
              "labelled cycle interval. It may detect a disturbed scene without identifying the forged object. "
              "Incidental clean alarms in that interval also satisfy this scene-alarm metric; no paired "
              "counterfactual attribution is claimed. Direct identification is reported separately.",
              "- Object identification means an alert on that exact labelled object frame. Header warnings are not "
              "broadcast to objects. Malformed object attempts count in the overall object denominator; "
              "their physical motion class is unknown. Overwrites use the replacement frame's final index.",
              "- All observed instances, including undetected and right-censored instances, remain in detection-rate "
              "denominators. EOF-touching instances are conservatively marked right-censored. Missing decision "
              "timestamps and undetected times remain blank, never zero. Scheduled attacks that never emit a frame "
              "require provider metadata and are not inferred from labels. The accepted provider writes a "
              "separate `.instances.json`: scheduled/unobserved and unscheduled/no-source-material counts are "
              "reported separately, and explicit lifecycle truncation replaces conservative EOF censoring.",
              "- Time-to-detect cycles run from first forged cycle to alerting cycle. Seconds use first forged "
              "arrival to cycle closure. EOF decision timing is unmeasured. Assembly delay is reported separately "
              "from CPU processing; tick duration remains configurable and unverified.",
              "- Clean false positives use decoded and malformed object attempts per object-cycle; ROI static/moving "
              "rates use decoded objects only. Episodes are nonalert-to-alert transitions per track plus hard cycle "
              "warning episodes. Unlinked malformed-object alerts each count one event because no physical track "
              "continuity is available. Duplicate verdicts on a linked track do not create extra episodes. "
              "Distinct streams reset persistence; absent observations age the configured M-of-N "
              "state. Clean episode rates come from independent attack-free evaluations.",
              "- AUROC needs both labels and finite scores on equivalent in-ROI windows; static/moving AUROCs use "
              "the window's calibration class. Insufficient history, missing "
              "scores and one-class subsets are excluded with counts. IF batch scoring is outside online CPU latency.", "",
              "## Calibration and deck claims", "",
              "AE is fixed for online deployment before attack testing; the isolation forest is an offline comparator. "
              "Actual static/moving threshold values, selected validation quantiles, model configuration and training "
              "segment identities and per-fold fusion M/N are stored in the manifest's calibration entries. "
              "The accepted upstream `learned_alone: false` policy makes AE corroborating evidence; standalone "
              "learned alert counts follow that policy, while AUROC measures its raw score. No new attack/test "
              "result selects a model.", "",
              _table(deck_evidence(runs, clean), ("claim", "evidence", "clean_timeblock_alerts_per_minute",
                                                 "clean_loso_alerts_per_minute", "direct_instance_identification_by_level")), "",
              "- Kinematic plausibility: windowed radial range rate uses learned `rr_scale` and quantisation-aware "
              "training envelopes. Coordinate units and tick duration remain assumptions. Attack sensitivity is "
              "supported only by successfully completed real provider runs above.",
              "- Timing signature: protocol rules include the documented two-gap capture warm-up, header/count/counter "
              "integrity, arrival windows, burst spacing and range order. A2 only repairs counts/counters/slot uniqueness; "
              "range-order and burst-contiguity awareness belong to A3+.",
              "- RCS versus range: the generated `rcs_vs_range.md` records population/same-person evidence and the "
              "underpowered per-track test. This is supporting physics evidence, not measured attack detection.",
              "- Learned normal: AE training uses only permitted clean training windows; thresholds use clean validation. "
              "Separate static/moving calibration and equivalent IF windows are reported; attacks never enter training.",
              "- No labelled attack data needed: labels are confined to sidecar evaluation joins; they do not enter "
              "the decoder, detector, learned features, thresholds or viewer colors.", "",
              "## Artifacts and excluded data", "",
              "`attack_eval_exclusions.csv` includes loader malformed/duplicate/header counts, out-of-ROI objects, "
              "score outcomes and assembly observations. `attack_eval_instances.csv` contains individual outcomes "
              "and censored time-to-detect. Model IDs, hashes, split identities, seeds and run indices accompany "
              "the configuration in the manifest. Raw recordings remain read-only.", ""]
    summary = output / "summary.md"
    summary.write_text("\n".join(lines), encoding="utf-8")
    return summary
