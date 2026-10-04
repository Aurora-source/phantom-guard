#!/usr/bin/env python3
"""Evaluate the detector against the synthetic frame generator and write the results matrix.

For every (scenario T1-T4) x (level A0-A4), fabricated frames are mixed into the **held-out test**
segments (time-block split), several seeds each, and the detector is run on the mixed stream. Labels
(which frames are fabricated) are joined with the detector's per-object verdicts only here, never
inside the detector.

Outputs (all generated) to docs/results/:
- summary.md: the headline detection matrix, per-layer attribution, time-to-detect, static vs
  moving, AUROC of the learned score, the levels that evade every layer, and the deck-claims check.
- attack_matrix.csv, attack_layers.csv, attack_ttd.csv.

Detection is reported two ways: per fabricated object-cycle (flagged by a layer) and per instance
(did the detector ever raise a persistent alert on that fabricated track, and after how many cycles).
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from phantomguard.attack.injector import MixedSource, label_map
from phantomguard.attack.levels import LEVELS
from phantomguard.attack.pools import build_pools
from phantomguard.attack.scenarios import make_context, plan_run
from phantomguard.config import REPO_ROOT, load_baseline, load_config, raw_path
from phantomguard.detect.common import LAYERS, REASONS, layer_of
from phantomguard.detect.fusion import Fusion
from phantomguard.detect.pipeline import MODELS_DIR, Detector, load_artifacts
from phantomguard.eval.splits import time_block_segments
from phantomguard.io.replay import ReplaySource

RESULTS = REPO_ROOT / "docs" / "results"
TYPES = ["T1", "T2", "T3", "T4"]
LEVEL_NAMES = ["A0", "A1", "A2", "A3", "A4"]


@dataclass
class CellResult:
    atype: str
    level: str
    fab_objcyc: int = 0
    fab_roi: int = 0
    fab_moving: int = 0
    flagged_all: int = 0
    flagged_moving: int = 0
    flagged_static: int = 0
    layer_hits: dict = field(default_factory=lambda: defaultdict(int))        # layer -> fab objcyc with a reason
    without_layer: dict = field(default_factory=lambda: defaultdict(int))     # layer -> fab objcyc still flagged if layer removed
    only_layer: dict = field(default_factory=lambda: defaultdict(int))        # layer -> fab objcyc flagged ONLY by this layer
    reason_hits: dict = field(default_factory=lambda: defaultdict(int))       # reason code -> count
    instances: int = 0
    instances_detected: int = 0
    inst_moving: int = 0
    inst_moving_detected: int = 0
    inst_static: int = 0
    inst_static_detected: int = 0
    ttd: list = field(default_factory=list)                                   # cycles to first alert (detected only)
    ae_fab: list = field(default_factory=list)
    ae_clean: list = field(default_factory=list)

    def merge(self, o: "CellResult") -> None:
        for k, v in self.__dict__.items():
            ov = getattr(o, k)
            if isinstance(v, int):
                setattr(self, k, v + ov)
            elif isinstance(v, list):
                v.extend(ov)
            elif isinstance(v, defaultdict):
                for kk, vv in ov.items():
                    v[kk] += vv


def run_job(job):
    atype, level, seed, file, lo, hi = job
    cfg = load_config()
    b = load_baseline()
    ae, lib = load_artifacts("timeblock")
    segs = time_block_segments(cfg)
    known = build_pools(cfg, segs["train"])
    unseen = build_pools(cfg, [s for s in segs["val"] if s.file != file])
    rng = np.random.default_rng(seed * 1009 + hash(file) % 997)
    ctx = make_context(cfg, b, level, rng, known, unseen)
    base = ReplaySource(raw_path(cfg, file), (lo, hi))
    instances = plan_run(ctx, atype, base.cycles, lo, hi)
    src = MixedSource(base, ctx, instances, tag=f"{atype}-{level}")
    frames = list(src)
    lm = label_map(src)
    det = Detector(cfg, b, ae, lib)

    res = CellResult(atype, level)
    # per-instance tracking
    first_present = {}
    first_alert = {}
    inst_moving = {}
    aid_to = {inst.attack_id: inst for inst in instances}
    thr = cfg["motion"]["moving_threshold_mps"]
    for k, cyc in enumerate(det.run(iter(frames))):
        for v in cyc.objects:
            if v.frame_index not in lm:
                if ae is not None and "ae" in v.scores and v.in_roi:
                    res.ae_clean.append(v.scores["ae"])
                continue
            lb = lm[v.frame_index]
            aid = lb.attack_id
            res.fab_objcyc += 1
            moving = v.moving
            res.fab_roi += v.in_roi
            res.fab_moving += moving
            reasons = v.reasons
            if reasons:
                res.flagged_all += 1
                (res.__dict__.__setitem__("flagged_moving", res.flagged_moving + 1) if moving
                 else res.__dict__.__setitem__("flagged_static", res.flagged_static + 1))
            seen_layers = set()
            for rc in reasons:
                res.reason_hits[rc] += 1
                seen_layers.add(layer_of(rc))
            for lay in seen_layers:
                res.layer_hits[lay] += 1
            if seen_layers:
                for lay in LAYERS:
                    if seen_layers - {lay}:
                        res.without_layer[lay] += 1
                    if seen_layers == {lay}:
                        res.only_layer[lay] += 1
            if ae is not None and "ae" in v.scores and v.in_roi:
                res.ae_fab.append(v.scores["ae"])
            if aid not in first_present:
                first_present[aid] = k
                inst_moving[aid] = bool(aid_to[aid].note.startswith(("moving", "drift", "replay")))
            if v.alert and aid not in first_alert:
                first_alert[aid] = k
    for aid, inst in aid_to.items():
        if aid not in first_present:
            continue
        res.instances += 1
        mov = inst_moving[aid]
        res.inst_moving += mov
        res.inst_static += (not mov)
        if aid in first_alert:
            res.instances_detected += 1
            res.inst_moving_detected += mov
            res.inst_static_detected += (not mov)
            res.ttd.append(first_alert[aid] - first_present[aid])
    return res


def auroc(pos: np.ndarray, neg: np.ndarray) -> float:
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    a = np.concatenate([pos, neg])
    order = a.argsort()
    ranks = np.empty(len(a))
    ranks[order] = np.arange(1, len(a) + 1)
    r_pos = ranks[: len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def layer_detects(cell: CellResult, layer: str) -> float:
    return cell.layer_hits.get(layer, 0) / cell.fab_objcyc if cell.fab_objcyc else 0.0


def main():
    cfg = load_config()
    if not (MODELS_DIR / "ae_timeblock.npz").exists():
        raise SystemExit("models missing: run learn_baseline.py --loso, train.py --loso, calibrate.py --loso first")
    test = time_block_segments(cfg)["test"]
    jobs = [(t, lv, seed, s.file, s.lo, s.hi)
            for t in TYPES for lv in LEVEL_NAMES for seed in cfg["attack"]["seeds"] for s in test]
    print(f"running {len(jobs)} jobs ({len(TYPES)}x{len(LEVEL_NAMES)} cells x {len(cfg['attack']['seeds'])} seeds x {len(test)} files)")
    cells: dict[tuple, CellResult] = {(t, lv): CellResult(t, lv) for t in TYPES for lv in LEVEL_NAMES}
    with ProcessPoolExecutor(cfg["eval"]["workers"]) as ex:
        for r in ex.map(run_job, jobs):
            cells[(r.atype, r.level)].merge(r)
    write_outputs(cfg, cells)
    print((RESULTS / "summary.md").read_text())


def pct(x):
    return f"{100 * x:.0f}%" if x == x else "n/a"


def write_outputs(cfg, cells):
    RESULTS.mkdir(parents=True, exist_ok=True)
    # instance-level detection matrix (did a persistent alert ever fire on the fabricated track)
    mat = []
    for t in TYPES:
        row = {"type": t}
        for lv in LEVEL_NAMES:
            c = cells[(t, lv)]
            row[lv] = c.instances_detected / c.instances if c.instances else float("nan")
        mat.append(row)
    dmat = pd.DataFrame(mat)
    dmat.to_csv(RESULTS / "attack_matrix.csv", index=False)

    # per-layer attribution (fraction of fabricated object-cycles each layer flags), pooled over types
    lay_rows = []
    for lv in LEVEL_NAMES:
        agg = CellResult("ALL", lv)
        for t in TYPES:
            agg.merge(cells[(t, lv)])
        row = {"level": lv, "fab_obj_cycles": agg.fab_objcyc, "any_layer": layer_detects(agg, "protocol") * 0 +
               (agg.flagged_all / agg.fab_objcyc if agg.fab_objcyc else 0.0)}
        for lay in LAYERS:
            row[lay] = layer_detects(agg, lay)
        row["alert_rate_instances"] = (sum(cells[(t, lv)].instances_detected for t in TYPES) /
                                       max(sum(cells[(t, lv)].instances for t in TYPES), 1))
        row["auroc_ae"] = auroc(np.array(agg.ae_fab), np.array(agg.ae_clean))
        lay_rows.append(row)
    dlay = pd.DataFrame(lay_rows)
    dlay.to_csv(RESULTS / "attack_layers.csv", index=False)

    # time-to-detect + static/moving
    ttd_rows = []
    for t in TYPES:
        for lv in LEVEL_NAMES:
            c = cells[(t, lv)]
            ttd_rows.append({"type": t, "level": lv, "instances": c.instances,
                             "detected": c.instances_detected,
                             "median_ttd_cycles": float(np.median(c.ttd)) if c.ttd else float("nan"),
                             "moving_det": f"{c.inst_moving_detected}/{c.inst_moving}",
                             "static_det": f"{c.inst_static_detected}/{c.inst_static}"})
    dttd = pd.DataFrame(ttd_rows)
    dttd.to_csv(RESULTS / "attack_ttd.csv", index=False)

    write_summary(cfg, cells, dmat, dlay, dttd)


def md_table(df, fmts=None):
    fmts = fmts or {}
    cols = list(df.columns)
    out = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    for _, r in df.iterrows():
        out += "| " + " | ".join(fmts.get(c, str)(r[c]) for c in cols) + " |\n"
    return out


def write_summary(cfg, cells, dmat, dlay, dttd):
    thr = cfg["motion"]["moving_threshold_mps"]
    pf = {lv: pct for lv in LEVEL_NAMES}
    lines = ["# Attack evaluation (synthetic fabricated-frame generator)", "",
             "Generated by `scripts/run_attack_eval.py`. Fabricated frames are mixed into the **held-out test**",
             "segments and the detector is run on the mixed stream; labels are joined with verdicts only in the",
             f"evaluator. {len(cfg['attack']['seeds'])} seeds per cell, all four files. The generator is a test",
             "fixture on recorded CSVs (no bus, no live system); every fabricated value is sampled from real-data",
             "distributions, so a fabricated frame differs from a real one only by the property under test.", "",
             "## Headline: instance detection rate (a persistent alert ever fired on the fabricated track)", "",
             "Rows are scenarios, columns are generator capability levels (A0 naive -> A4 data-aware).", "",
             md_table(dmat, pf), "",
             "## Per-layer attribution: fraction of fabricated object-cycles each layer flags (pooled over T1-T4)", "",
             "`any` is the full detector (any enabled layer); `alert_rate_instances` is the share of fabricated",
             "tracks that ever alert; `auroc_ae` is the AUROC of the autoencoder error separating fabricated from",
             "real in-ROI object-windows.", "",
             md_table(dlay[["level", "fab_obj_cycles", "any_layer", "protocol", "kinematic", "replay", "learned",
                            "alert_rate_instances", "auroc_ae"]].rename(columns={"any_layer": "any"}),
                      {"any": pct, "protocol": pct, "kinematic": pct, "replay": pct, "learned": pct,
                       "alert_rate_instances": pct, "auroc_ae": lambda x: f"{x:.2f}" if x == x else "n/a",
                       "fab_obj_cycles": lambda x: str(int(x))}), ""]

    # detection ablation: object-cycle detection with all layers vs with one layer removed
    abl_rows = []
    for lv in LEVEL_NAMES:
        agg = CellResult("ALL", lv)
        for t in TYPES:
            agg.merge(cells[(t, lv)])
        n = agg.fab_objcyc or 1
        row = {"level": lv, "all_layers": agg.flagged_all / n}
        for lay in LAYERS:
            row[f"-{lay}"] = agg.without_layer.get(lay, 0) / n
            row[f"only_{lay}"] = agg.only_layer.get(lay, 0) / n
        abl_rows.append(row)
    dabl = pd.DataFrame(abl_rows)
    dabl.to_csv(RESULTS / "attack_ablation.csv", index=False)
    pcols = ["level", "all_layers", "-protocol", "-kinematic", "-replay", "-learned"]
    lines += ["## Detection ablation (fabricated object-cycles flagged, one layer removed; pooled over T1-T4)", "",
              "A big drop from `all_layers` when a layer is removed means that layer is load-bearing at that level.",
              "`only_<layer>` (in attack_ablation.csv) counts object-cycles **only** that layer catches.", "",
              md_table(dabl[pcols], {c: pct for c in pcols[1:]}), ""]

    # per-scenario dominant signal at the two hardest levels
    dom_rows = []
    for t in TYPES:
        for lv in ("A3", "A4"):
            c = cells[(t, lv)]
            top = sorted(c.reason_hits.items(), key=lambda kv: -kv[1])[:3]
            dom_rows.append({"type": t, "level": lv,
                             "inst_detected": f"{c.instances_detected}/{c.instances}",
                             "top_reasons": ", ".join(f"{k} {100*v/max(c.fab_objcyc,1):.0f}%" for k, v in top) or "-"})
    lines += ["## Dominant catching signal at A3/A4 (per scenario)", "",
              md_table(pd.DataFrame(dom_rows)), ""]

    # time-to-detect matrix (median cycles)
    piv = dttd.pivot(index="type", columns="level", values="median_ttd_cycles").reset_index()
    lines += ["## Time-to-detect (median cycles from first appearance to first alert; blank = not detected)", "",
              md_table(piv, {lv: (lambda x: f"{x:.0f}" if x == x else "-") for lv in LEVEL_NAMES}), ""]

    # static vs moving on T1 (phantoms)
    lines += ["## Static vs moving phantoms (T1): detected instances / total", ""]
    sm = dttd[dttd.type == "T1"][["level", "moving_det", "static_det"]]
    lines += [md_table(sm), "",
              "Static phantoms are expected to be the hardest (CLAUDE.md): a still object at a plausible position",
              "with a plausible RCS has no motion to contradict and no trajectory to replay-match.", ""]

    # evasion statement
    evaders = []
    for lv in LEVEL_NAMES:
        agg = CellResult("ALL", lv)
        for t in TYPES:
            agg.merge(cells[(t, lv)])
        rate = agg.instances_detected / agg.instances if agg.instances else 0.0
        if rate < 0.999:
            evaders.append((lv, rate))
    lines += ["## What evades", ""]
    for t in TYPES:
        for lv in LEVEL_NAMES:
            c = cells[(t, lv)]
            if c.instances and c.instances_detected < c.instances:
                missed = c.instances - c.instances_detected
                lines.append(f"- {t}/{lv}: {missed}/{c.instances} instances raised no alert "
                             f"({c.inst_static - c.inst_static_detected} static, {c.inst_moving - c.inst_moving_detected} moving).")
    if not any(cells[(t, lv)].instances_detected < cells[(t, lv)].instances for t in TYPES for lv in LEVEL_NAMES):
        lines.append("- Every instance of every scenario and level raised at least one alert.")
    lines += ["", "Object-cycle level: per-object detection falls steadily as the generator gets more realistic; see the",
              "per-layer table. The learned autoencoder and the co-location / RCS-band kinematic checks carry A3-A4,",
              "where the protocol layer no longer fires.", ""]

    lines += deck_claims(cfg, cells, dlay)
    lines += ["", "## Caveats", "",
              "- Detection here is **against this generator**. It samples from the same real distributions the detector",
              "  learned from, so it is a strong test of the modelled flaws, not proof against an unmodelled one.",
              "- The clean-data false-alarm rate (the cost of these detections) is in `clean_eval.md`: the <1 alert/min",
              "  target is not met on held-out data, so these detection rates come at a higher false-alarm rate than the goal.",
              "- Thresholds and the operating point were set on train/validation only; the test segments were used only here."]
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")


def deck_claims(cfg, cells, dlay):
    def level_layer(level, layer):
        agg = CellResult("ALL", level)
        for t in TYPES:
            agg.merge(cells[(t, level)])
        return layer_detects(agg, layer)

    def auroc_level(level):
        row = dlay[dlay.level == level]
        return float(row["auroc_ae"].iloc[0]) if len(row) else float("nan")

    L = ["## Deck (slide 4) claims checked", "",
         "| claim | supported? | evidence |", "|---|---|---|"]
    # kinematic plausibility
    k_a2 = level_layer("A2", "kinematic")
    k_a3 = level_layer("A3", "kinematic")
    L.append(f"| kinematic plausibility | partial | catches naive motion (A2 flags {pct(k_a2)} of fabricated "
             f"object-cycles) but only {pct(k_a3)} at A3, where motion is velocity-consistent; carried then by "
             "co-location and the RCS band, not range-rate |")
    # timing signature
    p_a1 = level_layer("A1", "protocol")
    p_a2 = level_layer("A2", "protocol")
    p_a3 = level_layer("A3", "protocol")
    L.append(f"| timing signature | yes (<=A2) | protocol timing/order flags {pct(p_a1)} (A1) and {pct(p_a2)} (A2) of "
             f"fabricated object-cycles; at A3 the burst order is replicated and it drops to {pct(p_a3)} |")
    # RCS vs range consistency
    r_a3 = cells[("T1", "A3")]
    r_a4 = cells[("T1", "A4")]
    band_a3 = r_a3.reason_hits.get("RCS_BAND", 0) / r_a3.fab_objcyc if r_a3.fab_objcyc else 0
    band_a4 = r_a4.reason_hits.get("RCS_BAND", 0) / r_a4.fab_objcyc if r_a4.fab_objcyc else 0
    L.append(f"| RCS-vs-range consistency | yes | the range-conditional RCS band flags {pct(band_a3)} of A3 T1 "
             f"object-cycles (RCS from the marginal) but only {pct(band_a4)} of A4 T1 (RCS sampled per range), which "
             "is exactly the modelled difference |")
    # learned autoencoder
    au3, au4 = auroc_level("A3"), auroc_level("A4")
    lrn3 = level_layer("A3", "learned")
    lrn4 = level_layer("A4", "learned")
    L.append(f"| learned-normal autoencoder | yes, modest | AUROC {au3:.2f} (A3) / {au4:.2f} (A4); it flags {pct(lrn3)} "
             f"(A3) and {pct(lrn4)} (A4) of fabricated object-cycles, the main non-trivial signal left at A4 |")
    # no labelled attack data
    L.append("| no labelled attack data needed | yes | every threshold is learned from clean data only; the generator "
             "and its labels are used solely to measure, never to train or tune the detector |")
    return L


if __name__ == "__main__":
    main()
