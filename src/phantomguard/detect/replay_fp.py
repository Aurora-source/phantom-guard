"""Layer 3: replay fingerprint.

For moving tracks, each cycle step becomes a symbol, and the last k symbols form a fingerprint.
Two families: translation-invariant (dx, dy, vx, vy) as in CLAUDE.md, and rotation-invariant
(d_range, |displacement|, radial v, speed), which also catches a copy rotated about the sensor.
A window matches if the same fingerprint occurred in the training library, earlier in this stream,
or in another live track. Windows with fewer than ``min_complexity`` distinct symbols are not
fingerprinted, because real tracks that hold their values repeat trivially.
"""

from __future__ import annotations

import math
from collections import deque

from phantomguard.tracks import Track, TrackPoint


def symbols(a: TrackPoint, b: TrackPoint, dq: float, vq: float) -> tuple[tuple, tuple]:
    t_sym = (round((b.x - a.x) / dq), round((b.y - a.y) / dq), round(b.vx / vq), round(b.vy / vq))
    r_sym = (round((b.rng - a.rng) / (dq / 2)), round(math.hypot(b.x - a.x, b.y - a.y) / (dq / 2)),
             round(b.vr / vq), round(math.hypot(b.vx, b.vy) / vq))
    return t_sym, r_sym


def track_fingerprints(points: list[TrackPoint], k: int, dq: float, vq: float, min_complexity: int,
                       moving_threshold: float) -> list[tuple[int, tuple, tuple]]:
    """(index of last point, translation fp, rotation fp) for every eligible window of a track."""
    out = []
    t_syms, r_syms = [], []
    for i in range(1, len(points)):
        ts, rs = symbols(points[i - 1], points[i], dq, vq)
        t_syms.append(ts)
        r_syms.append(rs)
        if len(t_syms) >= k:
            tw, rw = tuple(t_syms[-k:]), tuple(r_syms[-k:])
            win = points[i - k:i + 1]
            moving = sum(math.hypot(p.vx, p.vy) >= moving_threshold for p in win) >= k // 2
            if moving and len(set(tw)) >= min_complexity:
                out.append((i, ("T",) + tw, ("R",) + rw))
    return out


class ReplayChecker:
    def __init__(self, cfg: dict, library: set | None = None):
        rp = cfg["replay"]
        self.k, self.dq, self.vq = rp["k_gram"], rp["disp_quant"], rp["vel_quant"]
        self.min_complexity = rp["min_complexity"]
        self.history_cycles = rp["history_cycles"]
        self.thr = cfg["motion"]["moving_threshold_mps"]
        self.library = library or set()
        self.seen: dict[tuple, tuple[int, int]] = {}  # fp -> (track_id, cycle_index) first seen in stream
        self._order: deque = deque()

    def check(self, tr: Track, cycle_index: int) -> tuple[bool, str | None]:
        pts = tr.points
        if len(pts) < self.k + 1:
            return False, None
        win = list(pts)[-(self.k + 1):]
        fps = track_fingerprints(win, self.k, self.dq, self.vq, self.min_complexity, self.thr)
        if not fps:
            return False, None
        _, tfp, rfp = fps[-1]
        hit = None
        for fp in (tfp, rfp):
            if fp in self.library:
                hit = "library"
            prev = self.seen.get(fp)
            if prev is not None and prev[0] != tr.track_id:
                hit = hit or "stream"
            if prev is None:
                self.seen[fp] = (tr.track_id, cycle_index)
                self._order.append((cycle_index, fp))
        while self._order and cycle_index - self._order[0][0] > self.history_cycles:
            _, old = self._order.popleft()
            if self.seen.get(old, (None, -1))[1] <= cycle_index - self.history_cycles:
                self.seen.pop(old, None)
        return hit is not None, hit


def build_library(cfg: dict, tracks: list) -> set:
    """Fingerprints of recorded clean tracks (arrays in stats.baseline column layout), in-ROI only."""
    import numpy as np

    from phantomguard.stats.baseline import P_CYCLE, P_R, P_RCS, P_T, P_VR, P_VX, P_VY, P_X, P_Y

    rp = cfg["replay"]
    lib: set = set()
    for p in tracks:
        if len(p) <= rp["k_gram"] or np.median(p[:, P_R]) > cfg["roi"]["max_range"]:
            continue
        pts = [TrackPoint(int(r[P_CYCLE]), r[P_T], r[P_X], r[P_Y], r[P_VX], r[P_VY], r[P_RCS], r[P_R], r[P_VR], -1)
               for r in p]
        for _, tfp, rfp in track_fingerprints(pts, rp["k_gram"], rp["disp_quant"], rp["vel_quant"],
                                              rp["min_complexity"], cfg["motion"]["moving_threshold_mps"]):
            lib.add(tfp)
            lib.add(rfp)
    return lib
