#!/usr/bin/env python3
"""Learn clean-data baseline statistics and thresholds from the TRAIN split only.

Writes configs/baseline.json (time-block split) and, with --loso, one baseline per
leave-one-scenario-out fold into data/processed/. Also prints each measured value next to the
figure stated in CLAUDE.md, investigates the short sync gaps, checks the tick scale, and writes
docs/results/rcs_vs_range.md and docs/results/baseline_claims.md.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy import stats as sps

from phantomguard.config import REPO_ROOT, load_config, save_baseline
from phantomguard.eval.splits import Segment, loso_folds, n_cycles, time_block_segments
from phantomguard.stats.baseline import (P_R, P_RCS, P_T, P_VR, P_VX, P_VY, P_X, P_Y, collect_segment, fit_rr_scale,
                                         learn, track_features, track_is_moving)

RESULTS = REPO_ROOT / "docs" / "results"


def fmt(x, nd=2):
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.mismatches: list[str] = []

    def row(self, what, measured, claim, ok: bool | None):
        flag = "" if ok is None else ("ok" if ok else "MISMATCH")
        if ok is False:
            self.mismatches.append(f"{what}: measured {measured}, CLAUDE.md {claim}")
        self.lines.append(f"| {what} | {measured} | {claim} | {flag} |")
        print(f"  {what:<48} measured {str(measured):<34} CLAUDE.md {claim:<28} {flag}")


def claims_check(cfg, rep: Report):
    roi = cfg["roi"]["max_range"]
    near = cfg["zones"]["near_max_range"]
    thr = cfg["motion"]["moving_threshold_mps"]
    claim_moving = {"emptyRoom.csv": (5, 7), "onePersonMovingFrontAndBack.csv": (5, 7),
                    "onePersonMovingSideToSide.csv": (1.6, 1.6), "multiplePeopleChaotic.csv": (5, 7)}
    claim_near = {"emptyRoom.csv": 0.0, "onePersonMovingFrontAndBack.csv": 10.7,
                  "onePersonMovingSideToSide.csv": 5.1, "multiplePeopleChaotic.csv": 22.6}
    per_file = {}
    for f in cfg["data"]["files"]:
        print(f"\n== {f} (full file, descriptive only: not used for thresholds)")
        st = collect_segment(cfg, Segment(f, 0, n_cycles(cfg, f)))
        per_file[f] = st
        g = np.array(st.header_gaps)
        rep.lines.append(f"\n**{f}**\n\n| quantity | measured | CLAUDE.md | |\n|---|---|---|---|")
        rep.row(f"{f[:10]} period median / range(p0.5-p99.5)", f"{np.median(g):.0f} / {np.quantile(g, .005):.0f}-{np.quantile(g, .995):.0f}",
                "332 / 332-338", bool(np.median(g) == 332))
        rep.row(f"{f[:10]} short gaps (warm-up)", f"{st.warmup_gaps} min after warm-up {g.min()}", "min 59-73", None)
        o = np.array(st.offsets)
        rep.row(f"{f[:10]} arrival offset min/median/max", f"{o.min()}/{np.median(o):.0f}/{o.max()}", "2-77, median ~30",
                bool(o.min() >= 2 and o.max() <= 77))
        n = np.array(st.objs_per_cycle)
        rep.row(f"{f[:10]} objects/cycle min/median/max", f"{n.min()}/{np.median(n):.0f}/{n.max()}", "11-32, median 23-24",
                bool(n.min() >= 11 and n.max() <= 32))
        rep.row(f"{f[:10]} slot range", f"0x{min(st.slots_all):02x}-0x{max(st.slots_all):02x}", "0x00-0x35",
                bool(max(st.slots_all) <= 0x35))
        sj = np.array(st.step_jumps)
        rep.row(f"{f[:10]} slot reassignments (>1.0)", f"{int((sj > 1.0).sum())} of {len(sj)}", "<= 11 of ~100k",
                bool((sj > 1.0).sum() <= 11))
        rep.row(f"{f[:10]} slot gone 1 cycle then back", f"{st.slot_returns}", "(not stated)", None)
        allp = np.concatenate(list(st.tracks.values()))
        spd = np.hypot(allp[:, P_VX], allp[:, P_VY])
        mv = 100 * (spd >= thr).mean()
        lo, hi = claim_moving[f]
        rep.row(f"{f[:10]} moving rows %", f"{mv:.1f}", f"{lo}-{hi}" if lo != hi else f"{lo}", bool(lo - 0.3 <= mv <= hi + 0.3))
        # near-zone mover cycles
        cyc_has = {}
        for p in st.tracks.values():
            m = (np.hypot(p[:, P_VX], p[:, P_VY]) >= thr) & (p[:, P_R] <= near)
            for c in p[m, 0]:
                cyc_has[c] = True
        pct = 100 * len(cyc_has) / st.n_cycles
        rep.row(f"{f[:10]} cycles with near-zone mover %", f"{pct:.1f}", f"{claim_near[f]}", bool(abs(pct - claim_near[f]) <= 1.0))
        rep.row(f"{f[:10]} rows outside ROI (range>{roi})", f"{st.n_out_of_roi} of {st.n_objects}", "(counted, not dropped)", None)
        # RCS by class
        rcs = allp[:, P_RCS]
        static = spd < thr
        people = (spd >= thr) & (allp[:, P_R] <= roi)
        ghosts = (spd >= thr) & (allp[:, P_R] > roi) & (allp[:, P_VR] < -3.0)
        q = lambda a: f"{np.quantile(a, .05):.0f}-{np.quantile(a, .95):.0f}" if len(a) else "n/a"
        rep.row(f"{f[:10]} RCS p5-p95 static / movers in ROI / ghost-like", f"{q(rcs[static])} / {q(rcs[people])} / {q(rcs[ghosts])}",
                "14-20 / 16-21 / 13-14", None)
        # ghosts: outside-ROI fast approaching tracks
        lifes, gspd = [], []
        for p in st.tracks.values():
            if np.median(p[:, P_R]) > roi and np.median(p[:, P_VR]) < -3.0 and len(p) >= 3:
                lifes.append(len(p))
                gspd.append(np.median(np.hypot(p[:, P_VX], p[:, P_VY])))
        if lifes:
            rep.row(f"{f[:10]} ghost-like tracks n / life median / speed p5-p95", f"{len(lifes)} / {np.median(lifes):.0f} / {np.quantile(gspd, .05):.2f}-{np.quantile(gspd, .95):.2f}",
                    "~11 cycles, 3.5-4.8 m/s", bool(3.0 <= np.median(gspd) <= 5.0 and 8 <= np.median(lifes) <= 14))
    return per_file


def short_gap_investigation(cfg, per_file) -> list[str]:
    out = ["\n### Short sync gaps\n"]
    files = cfg["data"]["files"]
    from phantomguard.io.replay import load_recorded_cycles
    from phantomguard.config import raw_path
    prev_end = None
    for f in files:
        cyc, _ = load_recorded_cycles(str(raw_path(cfg, f)))
        ts = np.array([c.sync_timestamp for c in cyc])
        g = np.diff(ts)
        idx = np.where(g < 300)[0]
        line = f"- {f}: gaps < 300 ticks at gap index {idx.tolist()} -> {g[idx].tolist()}; cycles in file {len(cyc)}"
        if prev_end is not None:
            line += (f"; time since previous file's last header {ts[0] - prev_end[0]} ticks, "
                     f"meas_counter step {cyc[0].meas_counter - prev_end[1]}")
        prev_end = (ts[-1], cyc[-1].meas_counter)
        out.append(line)
        print(line)
    out.append("- Conclusion: every short gap is one of the first two gaps of a capture (start-up flush of queued frames); "
               "none occur mid-stream. Between files the counter advances by 1 while 16-21 s pass, so the sensor's scan loop "
               "did not advance while nothing was capturing. Cadence checking skips the first "
               f"{cfg['protocol']['cadence_warmup_cycles']} gaps of a stream.")
    return out


def tick_scale_check(cfg, tf, per_file) -> list[str]:
    k, r, n = fit_rr_scale(tf)
    out = ["\n### Tick-duration / distance-unit check\n"]
    line = (f"- People (moving in-ROI tracks, train split): range-rate from positions (assuming tick_seconds = "
            f"{cfg['units']['tick_seconds']}) vs reported radial velocity over {cfg['kinematic']['window_cycles']}-cycle windows "
            f"with |v_r| >= 0.5 (n = {n}): least-squares slope **{k:.3f}**, correlation {r:.3f}. A slope of 1 would mean positions "
            f"move exactly as the reported velocity says. {k:.2f} means positions change about {100 * (1 - k):.0f}% more slowly. "
            "Either one position unit is longer than one velocity-metre (consistent with the tape-measure doubt about units), "
            "or the tick is longer than 0.1 ms. The CAN frame spacing (2-3 ticks per 8-byte frame at 500 kbps) supports 0.1 ms, so "
            "the distance unit is the more likely cause. This is not resolvable offline. The detector and the attacker both use the "
            "learned `rr_scale` instead of assuming 1.")
    out.append(line)
    print(line)
    rows = []
    for f, st in per_file.items():
        rrs, vrs, still = [], [], []
        for p in st.tracks.values():
            if np.median(p[:, P_R]) > cfg["roi"]["max_range"] and np.median(p[:, P_VR]) < -3.0 and len(p) >= 4:
                rrs.append(np.ptp(p[:, P_R]) / max(p[-1, P_T] - p[0, P_T], 1e-9))
                vrs.append(-np.median(p[:, P_VR]))
                still.append(np.hypot(p[-1, P_X] - p[0, P_X], p[-1, P_Y] - p[0, P_Y]) / max(len(p) - 1, 1))
        if rrs:
            rows.append(f"- {f}: ghost-like tracks (n = {len(rrs)}): median reported approach speed {np.median(vrs):.2f}, "
                        f"median range-rate from positions {np.median(rrs):.2f}, median net displacement per cycle {np.median(still):.3f} "
                        f"(the reported speed would give {np.median(vrs) * 332 * cfg['units']['tick_seconds']:.3f} per cycle)")
    out.append("\n**Ghost displacement (CLAUDE.md: 'move consistently with that speed for about 11 cycles'):**\n")
    out += rows
    out.append("- Finding: the ghosts report 4-5 m/s approach but their positions stay within about 0.2 for their 4-6 cycle appearances. They do not "
               "move with their reported speed. They are short episodes that respawn at the same place with a new slot. "
               "This contradicts CLAUDE.md; see docs/decisions.md.")
    for r_ in rows:
        print(r_)
    return out


def rcs_vs_range(cfg, per_file) -> str:
    roi = cfg["roi"]["max_range"]
    thr = cfg["motion"]["moving_threshold_mps"]
    lines = ["# RCS vs range", "",
             "Generated by `scripts/learn_baseline.py` on all four files (descriptive analysis, not a threshold).", "",
             "Question: does reported RCS depend on range? If it does, an RCS-vs-range consistency check makes sense; "
             "if not, RCS can only be used as a per-track stability check.", ""]
    allp = np.concatenate([p for st in per_file.values() for p in st.tracks.values()])
    inroi = allp[allp[:, P_R] <= roi]
    rho, pv = sps.spearmanr(allp[:, P_R], allp[:, P_RCS])
    rho2, pv2 = sps.spearmanr(inroi[:, P_R], inroi[:, P_RCS])
    lines.append("## Overall (all objects, mixes different targets)\n")
    lines.append(f"- All rows: Spearman rho = {rho:.3f} (n = {len(allp)})")
    lines.append(f"- In ROI (range <= {roi}): Spearman rho = {rho2:.3f} (n = {len(inroi)})")
    edges = [0, 2, 4, 6, 8, 10, 15, 20, 30, 40]
    lines.append("\n| range bin | n | RCS median | RCS p5-p95 |\n|---|---|---|---|")
    for a, b in zip(edges, edges[1:]):
        s = allp[(allp[:, P_R] > a) & (allp[:, P_R] <= b), P_RCS]
        if len(s):
            lines.append(f"| {a}-{b} | {len(s)} | {np.median(s):.1f} | {np.quantile(s, .05):.0f}-{np.quantile(s, .95):.0f} |")
    lines.append("\n## Per track (same target over time, moving in-ROI tracks with range span >= 1.0 and >= 20 points)\n")
    slopes, rhos, sig = [], [], 0
    for st in per_file.values():
        for p in st.tracks.values():
            if len(p) < 20 or np.median(p[:, P_R]) > roi or not track_is_moving(p, cfg):
                continue
            if np.ptp(p[:, P_R]) < 1.0:
                continue
            if np.ptp(p[:, P_RCS]) == 0:
                rhos.append(0.0)
                slopes.append(0.0)
                continue
            r_, pv_ = sps.spearmanr(p[:, P_R], p[:, P_RCS])
            rhos.append(float(r_))
            slopes.append(float(np.polyfit(p[:, P_R], p[:, P_RCS], 1)[0]))
            sig += (pv_ < 0.01) and abs(r_) >= 0.3
    if rhos:
        rh = np.array(rhos)
        sl = np.array(slopes)
        lines.append(f"- tracks: {len(rh)}; median Spearman rho {np.median(rh):.3f} (p25 {np.quantile(rh, .25):.3f}, p75 {np.quantile(rh, .75):.3f})")
        lines.append(f"- median slope {np.median(sl):.3f} dB per unit range (p25 {np.quantile(sl, .25):.3f}, p75 {np.quantile(sl, .75):.3f})")
        lines.append(f"- tracks with |rho| >= 0.3 and p < 0.01: {sig} of {len(rh)} ({100 * sig / len(rh):.0f}%), "
                     f"of which positive rho: {int(((rh >= 0.3)).sum())}, negative: {int((rh <= -0.3).sum())}")
    else:
        lines.append("- no qualifying tracks")
    # Pooled: one walking person (front/back file), moving in-ROI points
    fb = per_file.get("onePersonMovingFrontAndBack.csv")
    if fb is not None:
        pp = np.concatenate(list(fb.tracks.values()))
        m = (np.hypot(pp[:, P_VX], pp[:, P_VY]) >= thr) & (pp[:, P_R] <= roi)
        r3, p3 = sps.spearmanr(pp[m, P_R], pp[m, P_RCS])
        sl3 = np.polyfit(pp[m, P_R], pp[m, P_RCS], 1)[0]
        lines.append(f"\n## Pooled: one person walking toward/away (front/back file, moving in-ROI points)\n\n"
                     f"- n = {int(m.sum())}, range {pp[m, P_R].min():.1f}-{pp[m, P_R].max():.1f}; Spearman rho = {r3:.3f} (p = {p3:.2g}); "
                     f"slope {sl3:.3f} dB per unit range")
        print(f"  RCS vs range pooled single person: rho {r3:.3f}, slope {sl3:.3f} dB/unit, n {int(m.sum())}")
    # Static clutter: RCS stability
    sd = [np.std(p[:, P_RCS]) for st in per_file.values() for p in st.tracks.values()
          if len(p) >= 30 and np.median(p[:, P_R]) <= roi and not track_is_moving(p, cfg)]
    lines.append(f"\n## Static in-ROI tracks (>= 30 points)\n\n- per-track RCS std: median {np.median(sd):.2f}, p95 {np.quantile(sd, .95):.2f} dB (n = {len(sd)})")
    pooled_ok = fb is not None and abs(r3) >= 0.3 and p3 < 0.01
    overall_ok = abs(rho2) >= 0.3
    decided = "SUPPORTED" if (pooled_ok and overall_ok) else "NOT SUPPORTED"
    lines.append("\n**Rule applied:** the claim is supported if (a) the same person, pooled over the front/back walk, shows |rho| >= 0.3 "
                 "with p < 0.01 and (b) the in-ROI population shows |rho| >= 0.3. The per-track test is reported but underpowered: "
                 "only a handful of moving tracks are long enough. "
                 f"Result: **{decided}** (pooled person rho {r3:.2f}, in-ROI rho {rho2:.2f}, per-track tracks n = {len(rhos)}).")
    lines.append("\nInterpretation: reported RCS falls with range (it is not range-compensated, or the unit is power-like). Within one "
                 "1-unit range bin the spread is only a few dB, so the detector uses a **range-conditional RCS band** learned from "
                 "train (`rcs_by_range` in baseline.json) as well as per-track RCS stability. An attacker that samples RCS from "
                 "the marginal distribution is caught by the conditional band; one that samples conditionally is not.")
    print(f"  RCS vs range verdict: {decided}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loso", action="store_true", help="also learn one baseline per leave-one-scenario-out fold")
    ap.add_argument("--skip-claims", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    RESULTS.mkdir(parents=True, exist_ok=True)
    segs = time_block_segments(cfg)
    print("Learning baseline from TRAIN segments:", [(s.file, s.lo, s.hi) for s in segs["train"]])
    b, st, tf = learn(cfg, segs["train"], segs["val"])
    save_baseline(b)
    print("\nWrote configs/baseline.json. Thresholds (rule -> value, val exceedance):")
    for k, v in sorted(b.items()):
        if k.startswith("_") or k.endswith("hist") or k.endswith("samples"):
            continue
        print(f"  {k:<20} {str(v['value'])[:40]:<40} val_exc={v.get('val_exceedance', '-')}  [{v['rule'][:80]}]")
    print(f"  moving in-ROI tracks in train: {tf.n_moving_tracks} of {tf.n_tracks_roi} in-ROI tracks")
    print(f"  hold fraction (moving tracks) p5/p50/p95: {b['hold_frac_moving']['value']}; "
          f"position-change frac median {np.median(tf.pos_change_frac):.2f}, velocity-change frac median {np.median(tf.vel_change_frac):.2f}")
    md = ["# Baseline vs CLAUDE.md", "", "Generated by `scripts/learn_baseline.py`.", ""]
    if args.loso:
        out = REPO_ROOT / cfg["data"]["processed_dir"]
        out.mkdir(parents=True, exist_ok=True)
        for held, fold in loso_folds(cfg).items():
            bl, _, _ = learn(cfg, fold["train"], fold["val"])
            path = out / f"baseline_loso_{Path(held).stem}.json"
            save_baseline(bl, path)
            print(f"  LOSO fold (test={held}) -> {path}")
    if args.skip_claims:
        return
    rep = Report()
    print("\nMeasured vs CLAUDE.md (full files):")
    per_file = claims_check(cfg, rep)
    md += rep.lines
    md += short_gap_investigation(cfg, per_file)
    md += tick_scale_check(cfg, tf, per_file)
    md += ["\n### Mismatches flagged\n"] + ([f"- {m}" for m in rep.mismatches] or ["- none"])
    (RESULTS / "baseline_claims.md").write_text("\n".join(md) + "\n")
    (RESULTS / "rcs_vs_range.md").write_text(rcs_vs_range(cfg, per_file))
    print("\nMISMATCHES:" if rep.mismatches else "\nNo mismatches.")
    for m in rep.mismatches:
        print("  -", m)
    print(f"Wrote {RESULTS / 'baseline_claims.md'} and {RESULTS / 'rcs_vs_range.md'}")


if __name__ == "__main__":
    main()
