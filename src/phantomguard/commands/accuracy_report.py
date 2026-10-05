#!/usr/bin/env python3
"""Assemble docs/results/detector-accuracy from generated evidence (no number is typed by hand).

    python -m phantomguard.commands.accuracy_report --out docs/results/detector-accuracy \
        --final runs/final-compare --dev runs/dev-compare-final --dev-relaxed runs/dev-compare-relaxed \
        --rule-audit runs/rule-audit-v2 --latency runs/latency/latency.json --learned runs/learned-compare \
        --t4 runs/t4-study --equivalence runs/equivalence/compare.json --coverage runs/final-v2/coverage_matrix.csv \
        --baseline configs/baseline.json --processed-dir data/processed

Copies each input's generated markdown/CSV/JSON into the output directory and writes README.md with
the calibration table (from the baselines), headline rows (from the comparison JSON) and links. Inputs
that are not given are listed as missing rather than silently skipped.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def _copy(src: Path, dst: Path, names) -> list[str]:
    out = []
    for n in names:
        if (src / n).is_file():
            shutil.copyfile(src / n, dst / n)
            text = (dst / n).read_bytes().replace(b"\r\n", b"\n")
            (dst / n).write_bytes(text)
            out.append(n)
    return out


def calibration_table(baselines: dict[str, Path]) -> list[str]:
    lines = ["| context | " + " | ".join(["CV episodes / min", "95% Poisson"]) + " | thresholds |", "|---|---|---|---|"]
    for tag, path in baselines.items():
        if not path.is_file():
            lines.append(f"| {tag} | missing | | |")
            continue
        b = json.loads(path.read_text(encoding="utf-8"))
        rt = b.get("rule_thresholds", {})
        cv = rt.get("cv", {})
        th = ", ".join(f"{k} {'inactive' if v is None else v}" for k, v in sorted(rt.get("value", {}).items()))
        lines.append(f"| {tag} | {cv.get('episodes')} / {cv.get('minutes')} | "
                     f"{[round(x, 3) for x in cv.get('poisson_ci95_per_minute', [])]} | {th} |")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    for name in ("final", "dev", "dev-relaxed", "rule-audit", "learned", "t4"):
        ap.add_argument(f"--{name}", type=Path)
    ap.add_argument("--latency", type=Path, action="append", default=[],
                    help="latency JSON(s); the first is the headline, every one is copied (e.g. Windows, Linux, legacy)")
    ap.add_argument("--final-heldback", type=Path, help="comparison on held-back seeds (test part)")
    ap.add_argument("--equivalence", type=Path)
    ap.add_argument("--coverage", type=Path)
    ap.add_argument("--ledger-before", type=Path, help="clean-episode ledger of the legacy profile (test part)")
    ap.add_argument("--ledger-after", type=Path, help="clean-episode ledger of profile v2 (test part)")
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--processed-dir", type=Path, required=True)
    ap.add_argument("--files", default="emptyRoom,onePersonMovingFrontAndBack,onePersonMovingSideToSide,multiplePeopleChaotic")
    args = ap.parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    missing, sections = [], []
    cmp_files = ("comparison.md", "comparison_cells.csv", "comparison_regressions.csv", "comparison_gains.csv", "comparison.json")
    for label, src, sub in (("Final fixed-seed matrix (test part)", args.final, "final"),
                            ("Held-back seeds (test part)", args.final_heldback, "final-heldback"),
                            ("Development comparison (validation part)", args.dev, "dev"),
                            ("Alternative operating point: relaxed v2 (validation part)", args.dev_relaxed, "dev-relaxed")):
        if src is None:
            missing.append(label)
            continue
        (out / sub).mkdir(exist_ok=True)
        copied = _copy(src, out / sub, cmp_files)
        sections.append(f"- **{label}**: [{sub}/comparison.md]({sub}/comparison.md)" + ("" if copied else " (empty)"))
    for label, src, names in (("Rule audit (provenance, out-of-recording clean firings, Clopper-Pearson)", args.rule_audit,
                               ("rule_audit.md", "rule_audit.csv", "rule_audit.json")),
                              ("Learned-layer comparison", args.learned,
                               ("learned_compare_summary.json", "learned_compare_thresholds.csv")),
                              ("T4 separability study", args.t4, ("t4_study.json",))):
        if src is None:
            missing.append(label)
            continue
        copied = _copy(src, out, names)
        sections.append(f"- **{label}**: " + ", ".join(f"[{n}]({n})" for n in copied))
    for lat in args.latency:
        if lat.is_file():
            shutil.copyfile(lat, out / lat.name)
            sections.append(f"- **Latency benchmark**: [{lat.name}]({lat.name})")
    for label, src, name in (("Windows/Linux equivalence", args.equivalence, "equivalence.json"),
                             ("Coverage matrix (final run)", args.coverage, "coverage_matrix.csv")):
        if src is None or not src.is_file():
            missing.append(label)
            continue
        shutil.copyfile(src, out / name)
        sections.append(f"- **{label}**: [{name}]({name})")
    headline = []
    if args.final and (args.final / "comparison.json").is_file():
        res = json.loads((args.final / "comparison.json").read_text(encoding="utf-8"))
        from phantomguard.commands.compare_profiles import pooled

        for tag in ("before", "after"):
            for split, v in pooled(res["clean"][tag]).items():
                headline.append(f"| {'legacy' if tag == 'before' else 'v2'} | {split} | {v['events']} | {v['minutes']} | "
                                f"{v['per_minute']} | {v['ci95']} | {v['max_recording_per_minute']} |")
    latency_lines = []
    for path in args.latency:
        if not path.is_file():
            continue
        lat = json.loads(path.read_text(encoding="utf-8"))
        latency_lines.append(f"- `{path.name}` ({lat['profile']}): " + (f"Detector p99 (worst stream): wall {lat['worst_stream_wall_detector_p99_ms']} ms, thread CPU "
                        f"{lat['worst_stream_thread_cpu_detector_p99_ms']} ms (CPU percentiles valid: "
                        f"{lat['clocks']['thread_cpu_percentiles_valid']}); budget {lat['budget_ms']} ms met on "
                        f"{lat['budget_met_on']}: {lat['budget_met']}. Hardware: {lat['hardware']['processor'] or lat['hardware']['machine']}, "
                        f"{lat['hardware']['logical_cpus']} logical CPUs, {lat['hardware']['platform']}."))
    latency_line = "\n".join(latency_lines) or "Latency benchmark not supplied."
    boot_lines = []
    for name, d in (("legacy", args.ledger_before), ("v2", args.ledger_after)):
        if d is None:
            missing.append(f"clean-episode ledger ({name})")
            continue
        from phantomguard.eval.bootstrap import ledger_bootstrap

        sub = out / f"ledger-{name}"
        sub.mkdir(exist_ok=True)
        _copy(d, sub, ("clean_episodes.csv", "clean_ledger_summary.json"))
        for split, r in ledger_bootstrap(d).items():
            boot_lines.append(f"| {name} | {split} | {r['episodes']} | {r['minutes']} | {r['per_minute']} | "
                              f"{r['bootstrap_ci95']} | {r['p_rate_below_1_per_min']} |")
        sections.append(f"- **Clean-episode failure ledger ({name})**: [ledger-{name}/clean_episodes.csv](ledger-{name}/clean_episodes.csv)")
    baselines = {"timeblock": args.baseline,
                 **{f"loso_{s}": args.processed_dir / f"baseline_loso_{s}.json" for s in args.files.split(",")}}
    md = ["# Detector accuracy (profile v2) - generated results", "",
          "Generated by `python -m phantomguard.commands.accuracy_report` from the evidence listed below.",
          "Recorded sensor data with simulated CAN injection; coordinate units, tick duration and the frame",
          "layout are provisional and configurable. All four recordings are consecutive segments of one session:",
          "no rate here is a guarantee for another session (see docs/data-intake.md).", "",
          "## Clean held-out persistent alert episodes (test part, `all` layers)", "",
          "| profile | split | episodes | minutes | per minute | 95% Poisson | worst recording per minute |",
          "|---|---|---|---|---|---|---|", *(headline or ["| (final comparison not supplied) | | | | | | |"]), "",
          "Poisson intervals assume independent episodes; episodes cluster, so treat them as a guide.", "",
          "Clustered bootstrap (30 s contiguous blocks resampled within each split; one session, so this is",
          "within-session variability only). `P(rate<1)` is the bootstrap fraction below 1 episode/min.", "",
          "| profile | split | episodes | minutes | per minute | bootstrap 95% | P(rate<1) |",
          "|---|---|---|---|---|---|---|", *(boot_lines or ["| (ledgers not supplied) | | | | | | |"]), "",
          "## Calibration (out-of-recording CV over train+validation; thresholds from clean data only)", "",
          *calibration_table(baselines), "",
          "## Latency", "", latency_line, "",
          "## Evidence", "", *sections, ""]
    if missing:
        md += ["## Not supplied", "", *[f"- {m}" for m in missing], ""]
    (out / "README.md").write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {out / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
