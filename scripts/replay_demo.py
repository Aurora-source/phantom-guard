#!/usr/bin/env python3
"""Replay a recorded CSV through the detector and show (or export) the scene.

  python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv            # live window, ~real time
  python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --export   # PNGs + GIF to docs/results/

Attack injection (--attack/--level) is not implemented in this repo yet; those flags exit with an error.
"""

from __future__ import annotations

import argparse
import sys
import time

import matplotlib

from phantomguard.config import REPO_ROOT, load_baseline, load_config, raw_path
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.eval.splits import time_block_segments
from phantomguard.io.replay import ReplaySource


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="onePersonMovingFrontAndBack.csv")
    ap.add_argument("--part", default="test", choices=["train", "val", "test", "all"],
                    help="which time-block split segment of the file to replay")
    ap.add_argument("--start", type=int, default=0, help="cycles to skip inside the segment")
    ap.add_argument("--cycles", type=int, default=0, help="cycles to show (0 = all)")
    ap.add_argument("--attack", default=None, choices=["T1", "T2", "T3", "T4"], help="inject a fabricated-frame scenario")
    ap.add_argument("--level", default=None, choices=["A0", "A1", "A2", "A3", "A4"], help="generator realism level (default A3)")
    ap.add_argument("--export", action="store_true", help="headless: write PNG screenshots and a GIF")
    ap.add_argument("--around-first-alert", action="store_true", help="export the window around the first alert")
    ap.add_argument("--gif-cycles", type=int, default=150)
    ap.add_argument("--stride", type=int, default=2, help="GIF: keep every n-th cycle")
    ap.add_argument("--seed", type=int, default=11, help="generator seed")
    args = ap.parse_args()
    if args.attack and not args.level:
        args.level = "A3"
    if args.export:
        matplotlib.use("Agg")
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter

    from phantomguard.viz.console import ConsoleView

    cfg = load_config()
    b = load_baseline()
    ae, lib = load_artifacts("timeblock")
    if args.part == "all":
        lo, hi = 0, len(ReplaySource(raw_path(cfg, args.file)).cycles)
    else:
        seg = next(s for s in time_block_segments(cfg)[args.part] if s.file == args.file)
        lo, hi = seg.lo, seg.hi

    fab_frames: set[int] = set()
    if args.attack:
        from phantomguard.attack.injector import MixedSource
        from phantomguard.attack.pools import build_pools
        from phantomguard.attack.scenarios import make_context, plan_run

        segs = time_block_segments(cfg)
        known = build_pools(cfg, segs["train"])
        unseen = build_pools(cfg, [s for s in segs["val"] if s.file != args.file])
        ctx = make_context(cfg, b, args.level, np.random.default_rng(args.seed), known, unseen)
        base = ReplaySource(raw_path(cfg, args.file), (lo, hi))
        insts = plan_run(ctx, args.attack, base.cycles, lo, hi)
        src = MixedSource(base, ctx, insts, tag=f"{args.attack}-{args.level}")
        frames_in = list(src)
        fab_frames = {lb.frame_index for lb in src.labels}
        title = f"{args.file} [{args.part}] {args.attack}/{args.level}  (ringed = fabricated; red = flagged)"
        source_iter = iter(frames_in)
    else:
        title = f"{args.file} [{args.part}] clean replay (no attacks: red = false positive)"
        source_iter = ReplaySource(raw_path(cfg, args.file), (lo, hi))

    det = Detector(cfg, b, ae, lib)
    results = list(det.run(source_iter))
    results = results[args.start:]
    if args.cycles:
        results = results[: args.cycles]

    def show(view, res):
        view.render(res, title, fab_frames=fab_frames)

    view = ConsoleView(cfg["roi"]["max_range"], cfg["units"]["tick_seconds"])
    if not args.export:
        period = 332 * cfg["units"]["tick_seconds"]
        plt.ion()
        for r in results:
            t0 = time.perf_counter()
            show(view, r)
            plt.pause(max(0.001, period - (time.perf_counter() - t0)))
        plt.ioff()
        plt.show()
        return
    out = REPO_ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    # center on the first fabricated cycle for an attack demo, else first alert / middle
    if fab_frames:
        fab_cyc = next((i for i, r in enumerate(results) if any(v.frame_index in fab_frames for v in r.objects)), None)
        center = fab_cyc if fab_cyc is not None else len(results) // 2
    else:
        center = next((i for i, r in enumerate(results) if r.cycle_alert or any(v.alert for v in r.objects)), None)
        center = center if (args.around_first_alert and center is not None) else len(results) // 2
    lo_i = max(0, center - args.gif_cycles // 2)
    clip = results[lo_i: lo_i + args.gif_cycles]
    tag = f"{args.attack}_{args.level}" if args.attack else "clean"
    stem = f"{args.file.replace('.csv', '')}_{tag}"
    name = "first_alert" if (args.around_first_alert and not args.attack) else ("attack" if args.attack else "mid")
    shot_i = (center - lo_i) if (fab_frames or args.around_first_alert) else len(clip) // 2
    v = ConsoleView(cfg["roi"]["max_range"], cfg["units"]["tick_seconds"])
    for r in clip[: shot_i + 1]:
        show(v, r)
    p = out / f"demo_{stem}_{name}.png"
    v.fig.savefig(p, dpi=110)
    print("wrote", p)
    frames = clip[:: args.stride]
    gif_view = ConsoleView(cfg["roi"]["max_range"], cfg["units"]["tick_seconds"])
    anim = FuncAnimation(gif_view.fig, lambda k: show(gif_view, frames[k]), frames=len(frames))
    gp = out / f"demo_{stem}.gif"
    fps = max(1, round(1 / (332 * cfg["units"]["tick_seconds"] * args.stride)))
    anim.save(gp, writer=PillowWriter(fps=fps), dpi=70)
    print("wrote", gp, f"({len(frames)} frames, {fps} fps)")


if __name__ == "__main__":
    main()
