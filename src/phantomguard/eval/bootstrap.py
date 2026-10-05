"""Clustered bootstrap intervals for clean alert-episode rates (no independence assumption between episodes).

Episodes cluster in time (one busy stretch produces several), so a Poisson interval is too narrow. Here
each recording interval is cut into contiguous time blocks (default 30 s); blocks are resampled with
replacement within each split, keeping every episode with its block, and the pooled rate is recomputed.
The interval still treats blocks as exchangeable and comes from one session: it describes variability
inside these recordings, not across sessions.
"""

from __future__ import annotations

import numpy as np


def blocks(episode_times_s: list[float], minutes: float, block_s: float) -> list[tuple[int, float]]:
    """(episodes, minutes) per contiguous block of one recording interval; the last block may be short."""
    total_s = minutes * 60.0
    n = max(1, int(np.ceil(total_s / block_s)))
    counts = np.zeros(n, dtype=int)
    for t in episode_times_s:
        counts[min(n - 1, max(0, int(t // block_s)))] += 1
    lengths = np.full(n, block_s, dtype=float)
    lengths[-1] = total_s - block_s * (n - 1)
    return [(int(c), float(l) / 60.0) for c, l in zip(counts, lengths)]


def block_bootstrap_rate(segments: list[tuple[list[float], float]], block_s: float = 30.0, n_boot: int = 4000,
                         seed: int = 0, alpha: float = 0.05) -> dict:
    """segments: [(episode start times in seconds from the interval start, interval minutes)]."""
    bl = [b for times, mins in segments for b in blocks(times, mins, block_s)]
    if not bl:
        return {"blocks": 0}
    ev = np.array([b[0] for b in bl], dtype=float)
    mi = np.array([b[1] for b in bl], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(bl), size=(n_boot, len(bl)))
    rates = ev[idx].sum(1) / mi[idx].sum(1)
    lo, hi = np.quantile(rates, [alpha / 2, 1 - alpha / 2])
    return {"blocks": len(bl), "block_seconds": block_s, "episodes": int(ev.sum()), "minutes": round(float(mi.sum()), 3),
            "per_minute": round(float(ev.sum() / mi.sum()), 3), "bootstrap_ci95": [round(float(lo), 3), round(float(hi), 3)],
            "p_rate_below_1_per_min": round(float((rates < 1.0).mean()), 3), "n_boot": n_boot, "seed": seed}


def ledger_bootstrap(ledger_dir, block_s: float = 30.0) -> dict:
    """Per split, from a clean-episode ledger directory (clean_episodes.csv + clean_ledger_summary.json)."""
    import csv
    import json
    from pathlib import Path

    d = Path(ledger_dir)
    summary = json.loads((d / "clean_ledger_summary.json").read_text(encoding="utf-8"))
    eps: dict = {}
    with (d / "clean_episodes.csv").open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            eps.setdefault((r["split"], r["file"]), []).append(r)
    out = {}
    for split in sorted({s["split"] for s in summary["segments"]}):
        segs = []
        for s in summary["segments"]:
            if s["split"] != split:
                continue
            rows = eps.get((split, s["file"]), [])
            # start_time_s is measured from the first header of the interval by the ledger
            segs.append(([float(r["start_time_s"] or 0.0) for r in rows], float(s["minutes"])))
        out[split] = block_bootstrap_rate(segs, block_s)
    return out
