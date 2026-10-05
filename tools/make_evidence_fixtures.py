#!/usr/bin/env python3
"""Write small, truthful JSON fixtures of detector evidence and paired-localization rows.

Every value comes from a real recorded segment run through the configured detector artifacts (no
hand-edited numbers). Output goes to docs/parallel/fixtures/ for the backend/frontend agents.

    python tools/make_evidence_fixtures.py [--out docs/parallel/fixtures]
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from phantomguard.config import effective_cfg, load_baseline, load_config, paths, raw_path
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.eval.attack_adapter import attack_source
from phantomguard.eval.localize import control_alerts, localization_metrics, read_lineage, replay_lineage
from phantomguard.eval.metrics import lite, parse_labels
from phantomguard.eval.splits import time_block_segments
from phantomguard.io.replay import ReplaySource


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("docs/parallel/fixtures"))
    ap.add_argument("--recording", default="multiplePeopleChaotic.csv")
    ap.add_argument("--attack", default="T1")
    ap.add_argument("--level", default="A3")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)
    cfg = load_config()
    baseline = load_baseline(cfg=cfg)
    ae, lib = load_artifacts("timeblock", strict=True, cfg=cfg, baseline=baseline)
    sp = time_block_segments(cfg)
    seg = next(s for s in sp["test"] if s.file == args.recording)
    args.out.mkdir(parents=True, exist_ok=True)
    scoring = effective_cfg(cfg, baseline)
    layers = ("protocol", "kinematic", "replay", "learned")

    with tempfile.TemporaryDirectory() as tmp:
        labels_path = Path(tmp) / "labels.csv"
        source = attack_source(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)), cfg, baseline,
                               attack_type=args.attack, level=args.level, seed=args.seed,
                               train_segments=sp["train"], labels_path=labels_path, motion_case="moving")
        frames = list(source)
        labels = parse_labels(__import__("csv").DictReader(labels_path.open(newline="")))
        lineage = read_lineage(labels_path.with_suffix(".lineage.csv"))
        lifecycle = json.loads(labels_path.with_suffix(".instances.json").read_text())
        results = list(Detector(cfg, baseline, ae, lib).run(frames))
        cycles = [lite(r) for r in results]
        clean_source = ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi))
        clean = [lite(r) for r in Detector(cfg, baseline, ae, lib).run(clean_source)]
        control = control_alerts(clean, replay_lineage(clean_source), scoring, layers)
        loc = localization_metrics(cycles, labels, lineage, control, scoring, layers)

    forged = {l.frame_index for l in labels if l.is_attack}
    # First attacked cycle with an alerting object, else the first with any evidence-bearing flagged object.
    pick = next((r for r in results if any(v.alert and v.frame_index in forged for v in r.objects)), None)
    pick = pick or next((r for r in results if any(v.evidence for v in r.objects)), results[0])
    example = {"fixture": "detector cycle with evidence", "recording": args.recording, "attack": args.attack,
               "level": args.level, "seed": args.seed, "detector_artifact_id": ae.metadata["artifact_id"],
               "note": "Labels are NOT part of this record; evidence is derived from observed input and frozen "
                       "calibration only. Only objects with a reason carry evidence.",
               "cycle": {"index": pick.index, "cycle_reasons": pick.cycle_reasons, "cycle_alert": pick.cycle_alert,
                         "cycle_evidence": pick.cycle_evidence,
                         "objects": [asdict(v) for v in pick.objects if v.reasons or v.alert]}}
    (args.out / "evidence_cycle.json").write_text(json.dumps(example, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    paired = {"fixture": "offline paired clean/attacked localization (evaluator-only; never a detector input)",
              "recording": args.recording, "attack": args.attack, "level": args.level, "seed": args.seed,
              "layers": "all", "run_row": loc.row, "instances": loc.instances,
              "lifecycle": {k: lifecycle[k] for k in ("planner_version", "plan_log", "dropped_slot_capacity",
                                                       "requested_instances") if k in lifecycle}}
    (args.out / "paired_localization.json").write_text(json.dumps(paired, indent=2, allow_nan=False) + "\n",
                                                       encoding="utf-8")
    print(f"wrote {args.out}/evidence_cycle.json and paired_localization.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
