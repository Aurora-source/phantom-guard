#!/usr/bin/env python3
"""Replay clean or attacked CAN frames through the same detector and matplotlib viewer.

  python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --export
  python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --export

Attack injection requires the separate Phase 2 provider. Labels remain in its sidecar;
scene colours and alert logs use detector verdicts only. Green means "not flagged".
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import matplotlib

from phantomguard.config import REPO_ROOT, bval, load_baseline, load_config, raw_path
from phantomguard.detect.common import CycleResult
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.eval.splits import time_block, time_block_segments
from phantomguard.io.replay import ReplaySource


def output_stem(file: str, part: str, attack: str | None, level: str | None, seed: int,
                start: int = 0, cycles: int = 0) -> str:
    run = f"{attack}_{level}_seed{seed}" if attack else "clean"
    selection = f"_start{start}_cycles{cycles}" if start or cycles else ""
    return f"demo_{Path(file).stem}_{part}_{run}{selection}"


def _write_alert_log(results: list[CycleResult], path: Path, tick_seconds: float) -> None:
    """Lossless object identifiers; cycle alerts never claim a particular forged object."""
    t0 = next((r.header_t for r in results if r.header_t is not None), None)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cycle", "frame_index", "header_timestamp_ticks",
                                              "object_timestamp_ticks", "elapsed_seconds", "slot", "kind", "reasons", "scores",
                                              "flagged", "alert", "in_roi"])
        writer.writeheader()
        for r in results:
            elapsed = ((r.header_t - t0) * tick_seconds
                       if r.header_t is not None and t0 is not None else "")
            common = {"cycle": r.index, "header_timestamp_ticks": r.header_t,
                      "elapsed_seconds": elapsed}
            if r.cycle_reasons:
                writer.writerow(dict(common, kind="cycle", reasons=";".join(r.cycle_reasons),
                                     flagged=True, alert=r.cycle_alert))
            for v in r.objects:
                if v.flagged or v.alert:
                    writer.writerow(dict(common, frame_index=v.frame_index, slot=v.slot, kind="object",
                                         reasons=";".join(v.reasons), flagged=v.flagged, alert=v.alert,
                                         in_roi=v.in_roi, object_timestamp_ticks=getattr(v, "timestamp_ticks", None),
                                         scores=json.dumps(v.scores, sort_keys=True)))


def export_replay(results: list[CycleResult], cfg: dict, out: Path, stem: str, title: str, *, period_seconds: float,
                  gif_cycles: int = 150, stride: int = 2, around_first_alert: bool = False) -> list[Path]:
    """Export a nonempty clip and the full selected replay's detector alert log."""
    if not results:
        raise ValueError("empty replay clip: no cycles to export")
    if gif_cycles < 1 or stride < 1 or period_seconds <= 0:
        raise ValueError("gif_cycles, stride and period_seconds must be positive")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter
    from phantomguard.viz.console import ConsoleView

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    first = next((i for i, r in enumerate(results) if r.cycle_alert or any(v.alert for v in r.objects)), None)
    center = first if around_first_alert and first is not None else len(results) // 2
    lo = max(0, min(center - gif_cycles // 2, len(results) - gif_cycles))
    clip = results[lo:lo + gif_cycles]
    screenshot = center - lo
    name = "first_alert" if around_first_alert and first is not None else "mid"
    png = out / f"{stem}_{name}.png"
    gif = out / f"{stem}.gif"
    log = out / f"{stem}_alerts.csv"
    roi, tick = cfg["roi"]["max_range"], cfg["units"]["tick_seconds"]
    views = []
    try:
        view = ConsoleView(roi, tick)
        views.append(view)
        for r in clip[:screenshot + 1]:
            view.render(r, title)
        view.fig.savefig(png, dpi=110)
        gif_view = ConsoleView(roi, tick)
        views.append(gif_view)
        indices = list(range(0, len(clip), stride))
        last = -1

        def draw(k):
            nonlocal last
            target = indices[k]
            # Include alerts in cycles omitted by the GIF stride. Repeated animation
            # callbacks redraw the scene without adding duplicate log entries.
            for r in clip[last + 1:target]:
                gif_view.render(r, title)
            gif_view.render(clip[target], title)
            last = max(last, target)

        fps = max(1, round(1 / (period_seconds * stride)))
        anim = FuncAnimation(gif_view.fig, draw, frames=len(indices), repeat=False)
        anim.save(gif, writer=PillowWriter(fps=fps), dpi=70)
        _write_alert_log(results, log, tick)
    finally:
        for view in views:
            plt.close(view.fig)
    return [png, gif, log]


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="onePersonMovingFrontAndBack.csv")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "results")
    ap.add_argument("--part", default="test", choices=["train", "val", "test", "all"])
    ap.add_argument("--start", type=int, default=0, help="cycles to skip inside the segment")
    ap.add_argument("--cycles", type=int, default=0, help="cycles to show (0 = all)")
    ap.add_argument("--attack", choices=["T1", "T2", "T3", "T4"])
    ap.add_argument("--level", choices=["A0", "A1", "A2", "A3", "A4"])
    ap.add_argument("--provider", help="optional Phase 2 adapter factory as module:function")
    ap.add_argument("--seed", type=int, help="default: first configured attack seed")
    ap.add_argument("--export", action="store_true", help="headless: PNG, GIF and detector alert CSV")
    ap.add_argument("--around-first-alert", action="store_true", help="export around the first alert")
    ap.add_argument("--gif-cycles", type=int, default=150)
    ap.add_argument("--stride", type=int, default=2, help="GIF: keep every n-th cycle")
    args = ap.parse_args(argv)
    if bool(args.attack) != bool(args.level):
        ap.error("--attack and --level must be supplied together")
    if args.provider and not args.attack:
        ap.error("--provider requires --attack and --level")
    if args.attack and args.part not in ("val", "test"):
        ap.error("attacks may be injected only into validation/test segments (--part val or test)")
    if args.start < 0 or args.cycles < 0:
        ap.error("--start and --cycles must be nonnegative")
    if args.gif_cycles < 1 or args.stride < 1:
        ap.error("--gif-cycles and --stride must be positive")
    if args.seed is not None and args.seed < 0:
        ap.error("--seed must be nonnegative")
    if args.export:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from phantomguard.viz.console import ConsoleView

    try:
        cfg = load_config(args.config)
        b = load_baseline(args.baseline)
        seed = args.seed if args.seed is not None else cfg["attack"]["seeds"][0]
        path = Path(args.file)
        if not path.is_file():
            path = raw_path(cfg, args.file)
        source = ReplaySource(path)
        if not source.cycles:
            raise ValueError(f"empty recording: {path}")
        load_report = source.report.as_dict()
        bounds = time_block(len(source.cycles), cfg["splits"]["train_frac"], cfg["splits"]["val_frac"])
        lo, hi = (0, len(source.cycles)) if args.part == "all" else bounds[args.part]
        source = ReplaySource(path, (lo, hi))
        stem = output_stem(args.file, args.part, args.attack, args.level, seed, args.start, args.cycles)
        if args.attack:
            from phantomguard.eval.attack_adapter import attack_source

            label_dir = args.output_dir if args.export else REPO_ROOT / cfg["data"]["processed_dir"] / "demo_labels"
            source = attack_source(source, cfg, b, attack_type=args.attack, level=args.level, seed=seed,
                                   train_segments=time_block_segments(cfg)["train"],
                                   replay_provenance="training", labels_path=label_dir / f"{stem}_labels.csv",
                                   provider=args.provider)
        ae, lib = load_artifacts("timeblock", strict=True, cfg=cfg, baseline=b)
        det = Detector(cfg, b, ae, lib)
        results = list(det.run(source))
        results = results[args.start:]
        if args.cycles:
            results = results[:args.cycles]
        if not results:
            raise ValueError("empty replay clip: selected segment/start contains no cycles")
        # Pacing is a display choice based on learned cadence; it cannot change detector verdicts.
        nominal = float(bval(b, "cadence_median")) if "cadence_median" in b else (
            float(bval(b, "cadence_lo")) + float(bval(b, "cadence_hi"))) / 2
        period = nominal * cfg["units"]["tick_seconds"]
        if period <= 0:
            raise ValueError("baseline cadence must define a positive display period")
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        ap.error(str(exc))

    print("recording load report (whole CSV):", json.dumps(load_report, sort_keys=True))
    print(f"selected replay: {len(results)} cycles, "
          f"{sum(not v.in_roi for r in results for v in r.objects)} outside-ROI object-cycles, "
          f"{sum(v.x is None for r in results for v in r.objects)} undecodable object frames")
    run = f"{args.attack} {args.level} seed {seed}" if args.attack else "clean replay; alerts are false positives"
    title = f"{path.name} [{args.part}] {run} (green = not flagged)"
    if args.export:
        for p in export_replay(results, cfg, args.output_dir, stem, title, period_seconds=period,
                               gif_cycles=args.gif_cycles, stride=args.stride,
                               around_first_alert=args.around_first_alert):
            print("wrote", p)
        return
    view = ConsoleView(cfg["roi"]["max_range"], cfg["units"]["tick_seconds"])
    plt.ion()
    try:
        for i, r in enumerate(results):
            if not plt.fignum_exists(view.fig.number):
                break
            t0 = time.perf_counter()
            view.render(r, title)
            gap = period
            if i + 1 < len(results) and r.header_t is not None and results[i + 1].header_t is not None:
                gap = max(0, results[i + 1].header_t - r.header_t) * cfg["units"]["tick_seconds"]
            plt.pause(max(0.001, gap - (time.perf_counter() - t0)))
    finally:
        plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()
