"""Generated evaluation CSVs, provenance manifests and an honest Markdown report."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from phantomguard.config import REPO_ROOT, raw_path


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def provenance(cfg: dict, artifacts: list[Path]) -> dict:
    def git(*args):
        proc = subprocess.run(["rtk", "proxy", "git", *args], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None
    packages = {}
    for name in ("numpy", "scipy", "scikit-learn", "torch", "matplotlib", "pandas"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unavailable"
    return {"generated_utc": datetime.now(timezone.utc).isoformat(), "git_head": git("rev-parse", "HEAD"),
            "git_branch": git("branch", "--show-current"), "git_dirty": bool(git("status", "--porcelain")),
            "configuration": cfg, "configuration_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(),
            "python": platform.python_version(), "platform": platform.platform(), "packages": packages,
            "recordings": [{"file": f, "path": str(raw_path(cfg, f)), "sha256": sha256(raw_path(cfg, f))}
                           for f in cfg["data"]["files"]],
            "artifacts": [{"path": str(p), "sha256": sha256(p), "details": artifact_details(p)} for p in artifacts],
            "implementation_sha256": {str(p.relative_to(REPO_ROOT)): sha256(p)
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
    lines = ["# Phases 3–6 evaluation", "", "Generated by `scripts/run_attack_eval.py`; configuration and full provenance are in "
             "`attack_eval_manifest.json`. Every requested run and unsupported/blocked cell appears in `attack_eval_runs.csv`.", "",
             f"Completed attack runs: {manifest['completed_attack_runs']}. Completed clean segment evaluations: "
             f"{manifest['completed_clean_runs']}.", ""]
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
                           "flagged_per_object_cycle": sum(r["flagged_count"] for r in rows) / n if n else None})
    lines += [_table(pooled, ("split", "object_cycles", "minutes", "false_alerts_per_minute", "flagged_per_object_cycle"))
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
        lines += [_table(successful, ("split", "file", "attack_type", "level", "layers", "motion_case", "replay_provenance",
                                      "attack_instances", "attack_instance_detection_rate", "object_detection_rate",
                                      "median_ttd_cycles_detected", "ae_auroc", "iforest_auroc")), ""]
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
              "require provider metadata and are not inferred from labels.",
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
              "segment identities are stored in the manifest's calibration entries. No attack/test result selects a model.", "",
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
