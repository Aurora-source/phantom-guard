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
    ap.add_argument("--attack", default=None)
    ap.add_argument("--level", default=None)
    ap.add_argument("--export", action="store_true", help="headless: write PNG screenshots and a GIF")
    ap.add_argument("--around-first-alert", action="store_true", help="export the window around the first alert")
    ap.add_argument("--gif-cycles", type=int, default=150)
    ap.add_argument("--stride", type=int, default=2, help="GIF: keep every n-th cycle")
    args = ap.parse_args()
    if args.attack or args.level:
        sys.exit("attack injection is not implemented in this repo yet (Phase 2 attacker missing); run without --attack")
    if args.export:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter

    from phantomguard.viz.console import ConsoleView

    cfg = load_config()
    b = load_baseline()
    ae, lib = load_artifacts("timeblock")
    if args.part == "all":
        lo, hi = 0, 10**9
    else:
        seg = next(s for s in time_block_segments(cfg)[args.part] if s.file == args.file)
        lo, hi = seg.lo, seg.hi
    det = Detector(cfg, b, ae, lib)
    results = list(det.run(ReplaySource(raw_path(cfg, args.file), (lo, hi))))
    results = results[args.start:]
    if args.cycles:
        results = results[: args.cycles]
    title = f"{args.file} [{args.part}] clean replay (no attacks: red = false positive)"
    view = ConsoleView(cfg["roi"]["max_range"], cfg["units"]["tick_seconds"])
    if not args.export:
        period = 332 * cfg["units"]["tick_seconds"]
        plt.ion()
        for r in results:
            t0 = time.perf_counter()
            view.render(r, title)
            plt.pause(max(0.001, period - (time.perf_counter() - t0)))
        plt.ioff()
        plt.show()
        return
    out = REPO_ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    first = next((i for i, r in enumerate(results) if r.cycle_alert or any(v.alert for v in r.objects)), None)
    center = first if (args.around_first_alert and first is not None) else len(results) // 2
    lo_i = max(0, center - args.gif_cycles // 2)
    clip = results[lo_i: lo_i + args.gif_cycles]
    stem = args.file.replace(".csv", "")
    # Screenshots: one mid-clip, and the first alert if any
    use_alert = args.around_first_alert and first is not None
    for i, name in (((center - lo_i), "first_alert") if use_alert else (len(clip) // 2, "mid"),):
        v = ConsoleView(cfg["roi"]["max_range"], cfg["units"]["tick_seconds"])
        for r in clip[: i + 1]:  # replay the log up to this cycle
            v.render(r, title)
        p = out / f"demo_{stem}_{name}.png"
        v.fig.savefig(p, dpi=110)
        print("wrote", p)
    frames = clip[:: args.stride]
    gif_view = ConsoleView(cfg["roi"]["max_range"], cfg["units"]["tick_seconds"])
    anim = FuncAnimation(gif_view.fig, lambda k: gif_view.render(frames[k], title), frames=len(frames))
    gp = out / f"demo_{stem}.gif"
    fps = max(1, round(1 / (332 * cfg["units"]["tick_seconds"] * args.stride)))
    anim.save(gp, writer=PillowWriter(fps=fps), dpi=70)
    print("wrote", gp, f"({len(frames)} frames, {fps} fps, first alert at clip index {first})")


if __name__ == "__main__":
    main()
