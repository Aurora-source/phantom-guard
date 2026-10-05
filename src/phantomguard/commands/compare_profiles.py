#!/usr/bin/env python3
"""Instance-paired comparison of two attack-evaluation outputs (e.g. legacy vs profile v2).

    python -m phantomguard.commands.compare_profiles --before runs/dev-legacy --after runs/dev-v2 \
        --out docs/results/detector-accuracy/dev --title "Development comparison (validation part)"

Both inputs must come from run_attack_eval on the same split/part/seeds. Instances are paired by
(context, recording interval, cell, effective seed, attack id) and the pairing is verified: if the
attacker streams differ (different forged frame counts for a paired instance) the report says so and
stops. Every number in the markdown comes from the CSVs written alongside it.

Reported, per attack type x level (``all`` layers):
* exact identification (an alert on a forged 0x60B frame of the instance) and instance detection;
* instances identified only before / only after (regressions are listed, never netted away);
* paired localisation: exact recall over forged frames, false-suspect rate on real frames, paired excess
  alert events against the clean control of the same interval;
* undetected and right-censored counts;
* clean held-out alert episodes per recording with exact Poisson intervals (a guide: episodes cluster);
* rule ablations present in either input (``all-minus-CODE``): identified instances lost without CODE.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from phantomguard.eval.calibration import poisson_ci

CELL = ("attack_type", "level")
PAIR = ("split", "fold", "file", "segment_lo", "segment_hi", "attack_type", "level", "motion_case", "replay_provenance",
        "replay_variant", "effective_seed", "attack_id")


def read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def truthy(v) -> bool:
    return str(v).strip().lower() in {"true", "1", "1.0"}


def num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def instances(directory: Path, layers: str) -> dict:
    out = {}
    for r in read(directory / "attack_eval_instances.csv"):
        if r["layers"] == layers:
            out[tuple(r[k] for k in PAIR)] = r
    return out


def clean_rates(directory: Path) -> list[dict]:
    rows = []
    for r in read(directory / "attack_eval_clean.csv"):
        if r.get("layers") == "all" and r.get("status") == "ok":
            ev, mins = int(num(r["alert_events"])), num(r["minutes_exact"] or r["minutes"])
            lo, hi = poisson_ci(ev, mins)
            rows.append({"split": r["split"], "fold": r["fold"], "file": r["file"], "events": ev, "minutes": round(mins, 3),
                         "per_minute": round(ev / mins, 3) if mins else None, "ci95": [round(lo, 3), round(hi, 3)]})
    return rows


def compare(before: Path, after: Path) -> dict:
    layer_sets = {r["layers"] for d in (before, after) for r in read(d / "attack_eval_runs.csv") if r.get("layers")}
    b, a = instances(before, "all"), instances(after, "all")
    common = sorted(set(b) & set(a))
    mismatch = [k for k in common if b[k]["forged_object_frames"] != a[k]["forged_object_frames"]]
    if mismatch:
        raise SystemExit(f"attacker streams differ for {len(mismatch)} paired instances (e.g. {mismatch[0]}); "
                         "regenerate both evaluations from the same planner/seeds")
    cells = defaultdict(lambda: defaultdict(float))
    regressions, gains = [], []
    for k in common:
        x, y = b[k], a[k]
        c = cells[(k[5], k[6])]
        c["instances"] += 1
        for tag, r in (("before", x), ("after", y)):
            c[f"{tag}_identified"] += truthy(r["identified"])
            c[f"{tag}_detected"] += truthy(r["detected"])
            c[f"{tag}_right_censored_undetected"] += truthy(r["right_censored"]) and not truthy(r["detected"])
            c[f"{tag}_forged_frames"] += num(r.get("forged_object_frames_loc") or r["forged_object_frames"])
            c[f"{tag}_forged_frames_alerting"] += num(r.get("forged_frames_alerting"))
            c[f"{tag}_window_real_excess_alert_frames"] += num(r.get("window_real_excess_alert_frames"))
            c[f"{tag}_paired_exact"] += r.get("paired_status") == "exact"
        if truthy(x["identified"]) and not truthy(y["identified"]):
            regressions.append(dict(zip(PAIR, k)))
            c["lost"] += 1
        if truthy(y["identified"]) and not truthy(x["identified"]):
            gains.append(dict(zip(PAIR, k)))
            c["gained"] += 1
    rows = []
    for (t, lvl), c in sorted(cells.items()):
        n = c["instances"]
        row = {"attack_type": t, "level": lvl, "paired_instances": int(n)}
        for tag in ("before", "after"):
            row[f"{tag}_identified"] = int(c[f"{tag}_identified"])
            row[f"{tag}_identification_rate"] = round(c[f"{tag}_identified"] / n, 4) if n else None
            row[f"{tag}_detected"] = int(c[f"{tag}_detected"])
            row[f"{tag}_exact_frame_recall"] = (round(c[f"{tag}_forged_frames_alerting"] / c[f"{tag}_forged_frames"], 4)
                                                if c[f"{tag}_forged_frames"] else None)
            row[f"{tag}_real_excess_alert_frames"] = int(c[f"{tag}_window_real_excess_alert_frames"])
            row[f"{tag}_undetected_right_censored"] = int(c[f"{tag}_right_censored_undetected"])
        row["gained_identified"], row["lost_identified"] = int(c["gained"]), int(c["lost"])
        rows.append(row)
    ablations = {}
    for d, tag in ((before, "before"), (after, "after")):
        per = defaultdict(lambda: defaultdict(int))
        for r in read(d / "attack_eval_instances.csv"):
            per[r["layers"]][(r["attack_type"], r["level"])] += truthy(r["identified"])
        ablations[tag] = {v: {f"{t}|{l}": n for (t, l), n in sorted(m.items())} for v, m in per.items()
                          if v.startswith("all-minus-") or v == "all"}
    return {"rows": rows, "regressions": regressions, "gains": gains, "unpaired_before": len(set(b) - set(a)),
            "unpaired_after": len(set(a) - set(b)), "layer_sets": sorted(layer_sets),
            "clean": {"before": clean_rates(before), "after": clean_rates(after)}, "ablations": ablations}


def pooled(rows: list[dict]) -> dict:
    out = {}
    for split in sorted({r["split"] for r in rows}):
        ev = sum(r["events"] for r in rows if r["split"] == split)
        mins = sum(r["minutes"] for r in rows if r["split"] == split)
        lo, hi = poisson_ci(ev, mins)
        out[split] = {"events": ev, "minutes": round(mins, 3), "per_minute": round(ev / mins, 3) if mins else None,
                      "ci95": [round(lo, 3), round(hi, 3)],
                      "max_recording_per_minute": max((r["per_minute"] or 0) for r in rows if r["split"] == split)}
    return out


def markdown(res: dict, title: str, names: tuple[str, str]) -> str:
    bn, an = names
    lines = [f"# {title}", "", f"Generated by `python -m phantomguard.commands.compare_profiles`. Before = {bn}, after = {an}.",
             "Instances are paired on identical attacker streams (verified by forged-frame counts).", "",
             "## Clean held-out alert episodes (`all` layers)", "",
             "| split | before events / min | before rate (95% Poisson) | after events / min | after rate (95% Poisson) |",
             "|---|---|---|---|---|"]
    pb, pa = pooled(res["clean"]["before"]), pooled(res["clean"]["after"])
    for split in sorted(set(pb) | set(pa)):
        x, y = pb.get(split, {}), pa.get(split, {})
        lines.append(f"| {split} | {x.get('events')} / {x.get('minutes')} | {x.get('per_minute')} {x.get('ci95')} | "
                     f"{y.get('events')} / {y.get('minutes')} | {y.get('per_minute')} {y.get('ci95')} |")
    lines += ["", "Per recording (after): " + "; ".join(f"{r['split']}/{r['fold']}/{r['file']}: {r['events']} in {r['minutes']} min"
                                                       for r in res["clean"]["after"]),
              "", "Poisson intervals are a guide only: episodes cluster in time and the recordings are consecutive "
                  "segments of one session.", "",
              "## Exact identification by attack type and level", "",
              "| type | level | paired | identified before | after | gained | lost | exact frame recall before | after | real excess alert frames before | after | undetected right-censored before | after |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        lines.append(f"| {r['attack_type']} | {r['level']} | {r['paired_instances']} | {r['before_identified']} ({r['before_identification_rate']}) "
                     f"| {r['after_identified']} ({r['after_identification_rate']}) | {r['gained_identified']} | {r['lost_identified']} "
                     f"| {r['before_exact_frame_recall']} | {r['after_exact_frame_recall']} | {r['before_real_excess_alert_frames']} "
                     f"| {r['after_real_excess_alert_frames']} | {r['before_undetected_right_censored']} | {r['after_undetected_right_censored']} |")
    lines += ["", f"Unpaired instances: before {res['unpaired_before']}, after {res['unpaired_after']}.",
              f"Instances identified before but not after: {len(res['regressions'])} (listed in comparison_regressions.csv).", ""]
    for tag, name in (("before", bn), ("after", an)):
        abl = res["ablations"].get(tag, {})
        if len(abl) > 1:
            lines += [f"## Rule ablations ({name}): identified instances", "",
                      "| variant | " + " | ".join(sorted(abl["all"])) + " |", "|---|" + "---|" * len(abl["all"])]
            for v in sorted(abl):
                lines.append(f"| {v} | " + " | ".join(str(abl[v].get(c, 0)) for c in sorted(abl["all"])) + " |")
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="Detector comparison")
    ap.add_argument("--names", default="legacy,v2")
    args = ap.parse_args(argv)
    res = compare(args.before, args.after)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "comparison_cells.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(res["rows"][0]))
        w.writeheader()
        w.writerows(res["rows"])
    for name in ("regressions", "gains"):
        with (args.out / f"comparison_{name}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(PAIR))
            w.writeheader()
            w.writerows(res[name])
    (args.out / "comparison.json").write_text(json.dumps({k: v for k, v in res.items() if k not in {"regressions", "gains"}},
                                                         indent=2) + "\n", encoding="utf-8")
    (args.out / "comparison.md").write_text(markdown(res, args.title, tuple(args.names.split(","))), encoding="utf-8")
    print(f"wrote {args.out / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
