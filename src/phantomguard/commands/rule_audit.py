#!/usr/bin/env python3
"""Rule audit: provenance, clean exceedances and what zero observed violations can and cannot show.

    python -m phantomguard.commands.rule_audit --output runs/rule-audit

Runs the detector (any profile) over every configured clean recording in full and counts,
per reason code, the object-cycles (object rules) or cycles (cycle rules) where it fired. For each
code it reports its class (structural / exact_regularity / empirical_tail / ...), whether fusion
treats it as hard, where its bound comes from, and a one-sided 95% Clopper-Pearson upper bound on the
per-trial firing probability, converted to an upper bound on clean firings per minute at the observed
object/cycle rate. A hard rule that fired 0 times in n trials is *consistent with* a rate up to that
bound - not proven to be zero. The recordings were all inspected while the rules were designed, so the
counts are descriptive, not a held-out test.

Default ``--tag loso``: each recording is scored by the leave-one-recording-out artifacts that never saw
it, so learned bounds, the replay library and the autoencoder are applied out-of-recording. With a single
tag such as ``timeblock`` the recordings include that tag's own training portions (REPLAY then matches
training tracks against themselves); use it only for structural rules.
"""

from __future__ import annotations

import os

for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import argparse
import csv
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from scipy.stats import beta

from phantomguard.config import add_path_arguments, config_from_args, effective_cfg, load_baseline, paths, raw_path

# Where each bound comes from. "protocol": a property of the frame format/bus; "measured": an exact
# regularity seen without exception in clean data (an assumption about this sensor/firmware, checked
# only on these recordings); "learned": a fitted envelope/threshold stored in the baseline.
PROVENANCE = {
    "BAD_ID": "protocol: CAN id other than the radar header/object ids",
    "SHORT_HEADER": "protocol: header payload shorter than the fields decoded",
    "HEADER_LEN": "protocol (assumed DLC; CSVs do not store it, config protocol.header_len)",
    "NO_HEADER": "protocol: object frames without a cycle header",
    "FRAME_LEN": "protocol: 0x60B payload must be 8 bytes",
    "COUNT_MISMATCH": "protocol: header object count vs frames received",
    "COUNTER": "protocol: measurement counter +1 per cycle (16-bit wrap)",
    "DUP_SLOT": "protocol/measured: a slot never repeats within a cycle",
    "STATUS": "measured: header status byte always 0x01",
    "FIXED_FIELD": "measured: unused bits (byte 6, bits 4-3) always zero",
    "RCS_GRID": "measured: clean RCS always on an integer-dB grid (payload grid is 0.5 dB)",
    "RANGE_ORDER": "measured: objects of a cycle arrive sorted by range",
    "BURST_GAP": "measured: object frames arrive back to back (serial CAN spacing)",
    "CADENCE": "learned: cycle period range (baseline cadence_lo/hi)",
    "COUNT_RANGE": "learned: objects-per-cycle range with tolerance",
    "ARRIVAL": "learned: arrival offset window (legacy)",
    "ARRIVAL_POS": "learned: offset vs burst position line (v2), calibrated exceedance",
    "SLOT_RANGE": "learned: maximum slot id seen in training, calibrated exceedance",
    "RCS_RANGE": "learned: RCS min/max (legacy)",
    "SPEED": "learned: speed envelope, calibrated exceedance in v2",
    "ACCEL": "learned: acceleration quantile, calibrated exceedance in v2",
    "RR_RESID": "learned: range-rate residual quantile",
    "POS_SPEED": "learned: position-speed quantile",
    "RCS_STD": "learned: RCS std (v2: range-conditional)",
    "RCS_BAND": "learned: per-range RCS band (legacy)",
    "RCS_ENV": "learned: range-conditional RCS envelope (v2)",
    "JUMP": "learned: reassignment jump",
    "COLOC": "learned: minimum co-location distance",
    "DRIFT": "learned: drift model (fitted B, MAD scales), calibrated z",
    "DRIFT_STATIC": "learned: drift model, static windows, calibrated z",
    "REPLAY": "learned: training fingerprint library + stream history; v2 calibrated run length",
    "LEARNED": "learned: window autoencoder threshold (corroborating only unless a route is configured)",
}
CYCLE_CODES = {"BAD_ID", "SHORT_HEADER", "HEADER_LEN", "NO_HEADER", "COUNT_MISMATCH", "COUNTER", "STATUS",
               "CADENCE", "COUNT_RANGE"}


def cp_upper(k: int, n: int, conf: float = 0.95) -> float:
    return 1.0 if n == 0 else (1.0 if k >= n else float(beta.ppf(conf, k + 1, n - k)))


def scan(job):
    cfg, tag, file = job
    tag = f"loso_{Path(file).stem}" if tag == "loso" else tag
    from phantomguard.detect.pipeline import Detector, load_artifacts
    from phantomguard.io.replay import ReplaySource

    p = paths(cfg)
    baseline = load_baseline(p.baseline if tag == "timeblock" else p.processed / f"baseline_{tag}.json")
    ae, lib = load_artifacts(tag, strict=True, cfg=cfg, baseline=baseline)
    objs, cyc, first_t, last_t, n_obj, n_cyc = Counter(), Counter(), None, None, 0, 0
    for r in Detector(cfg, baseline, ae, lib).run(ReplaySource(raw_path(cfg, file))):
        n_cyc += 1
        n_obj += len(r.objects)
        if r.header_t is not None:
            first_t = r.header_t if first_t is None else first_t
            last_t = r.header_t
        cyc.update(set(r.cycle_reasons))
        for v in r.objects:
            objs.update(set(v.reasons))
    minutes = (last_t - first_t) * cfg["units"]["tick_seconds"] / 60 if first_t is not None else 0.0
    return file, objs, cyc, n_obj, n_cyc, minutes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="loso", help="'loso' (each recording by the fold that held it out) or one tag")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", type=Path)
    add_path_arguments(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    p = paths(cfg)
    first = f"loso_{Path(cfg['data']['files'][0]).stem}" if args.tag == "loso" else args.tag
    baseline = load_baseline(p.baseline if first == "timeblock" else p.processed / f"baseline_{first}.json")
    from phantomguard.detect.common import REASONS, contract_of
    from phantomguard.detect.evidence import REASON_CLASS
    from phantomguard.detect.fusion import Fusion

    fusion = Fusion(effective_cfg(cfg, baseline), ("protocol", "kinematic", "replay", "learned"))
    contract = contract_of(baseline) or {}
    off = set(contract.get("off", ()))
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(scan, [(cfg, args.tag, f) for f in cfg["data"]["files"]]))
    objs, cyc, n_obj, n_cyc, minutes = Counter(), Counter(), 0, 0, 0.0
    per_file = {}
    for file, o, c, no, nc, m in results:
        objs += o
        cyc += c
        n_obj, n_cyc, minutes = n_obj + no, n_cyc + nc, minutes + m
        per_file[file] = {"object_cycles": no, "cycles": nc, "minutes": round(m, 3), **{k: v for k, v in (o + c).items()}}
    thresholds = (baseline.get("rule_thresholds") or {}).get("value", {})
    rows = []
    for code in REASONS:
        is_cycle = code in CYCLE_CODES
        k, n = (cyc[code], n_cyc) if is_cycle else (objs[code], n_obj)
        up = cp_upper(k, n)
        per_min = n / minutes if minutes else 0.0
        rows.append({"code": code, "layer": REASONS[code][0], "class": REASON_CLASS.get(code, "unknown"),
                     "hard_in_fusion": fusion.hard(code), "evaluated": code not in off,
                     "calibrated_threshold": thresholds.get(code, ""), "trial": "cycle" if is_cycle else "object-cycle",
                     "clean_firings": k, "trials": n, "rate": k / n if n else None, "cp95_upper_rate": up,
                     "cp95_upper_firings_per_minute": up * per_min, "provenance": PROVENANCE.get(code, "")})
    out = args.output or p.output / "rule-audit"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "rule_audit.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    meta = {"tag": args.tag, "profile": contract.get("profile", "legacy"), "minutes": round(minutes, 3),
            "object_cycles": n_obj, "cycles": n_cyc, "per_file": per_file,
            "caveat": "all recordings were inspected while designing these rules: descriptive counts, not a held-out test"}
    (out / "rule_audit.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    md = ["# Rule audit", "", f"Generated by `python -m phantomguard.commands.rule_audit` ({meta['profile']} profile, "
          f"tag {args.tag}); {n_obj} object-cycles, {n_cyc} cycles, {minutes:.2f} provisional minutes of clean recording.",
          "", meta["caveat"] + ".", "",
          "| code | class | hard | evaluated | clean firings / trials | CP95 upper rate | CP95 upper firings/min | provenance |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['code']} | {r['class']} | {'yes' if r['hard_in_fusion'] else 'no'} | {'yes' if r['evaluated'] else 'no'} "
                  f"| {r['clean_firings']} / {r['trials']} | {r['cp95_upper_rate']:.2e} | {r['cp95_upper_firings_per_minute']:.3f} "
                  f"| {r['provenance']} |")
    (out / "rule_audit.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {out / 'rule_audit.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
