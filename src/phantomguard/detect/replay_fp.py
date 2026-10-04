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
                       moving_threshold: float, roi: float | None = None) -> list[tuple[int, tuple, tuple]]:
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
            if any(b.cycle_index - a.cycle_index != 1 for a, b in zip(win, win[1:])):
                continue
            if roi is not None and any(p.rng > roi for p in win):
                continue
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
        self.roi = cfg["roi"]["max_range"]
        self.library = frozenset(library or ())
        self.seen: dict[tuple, deque] = {}  # fp -> (track_id, cycle_index) occurrences
        self._order: deque = deque()

    def check(self, tr: Track, cycle_index: int) -> tuple[bool, str | None]:
        # Expire before matching: a forgotten source cannot trigger one final stale hit.
        while self._order and cycle_index - self._order[0][0] > self.history_cycles:
            old_cycle, old_fp, old_tid = self._order.popleft()
            occurrences = self.seen[old_fp]
            if occurrences and occurrences[0] == (old_tid, old_cycle):
                occurrences.popleft()
            if not occurrences:
                del self.seen[old_fp]
        pts = tr.points
        if len(pts) < self.k + 1:
            return False, None
        win = list(pts)[-(self.k + 1):]
        fps = track_fingerprints(win, self.k, self.dq, self.vq, self.min_complexity, self.thr, self.roi)
        if not fps:
            return False, None
        _, tfp, rfp = fps[-1]
        hit = None
        for fp in (tfp, rfp):
            if fp in self.library:
                hit = "library"
            for tid, when in self.seen.get(fp, ()):
                # Same-track overlapping windows are not independent replay evidence.
                if tid != tr.track_id or cycle_index - when > self.k:
                    hit = hit or ("concurrent" if when == cycle_index else "earlier_stream")
                    break
            self.seen.setdefault(fp, deque()).append((tr.track_id, cycle_index))
            self._order.append((cycle_index, fp, tr.track_id))
        return hit is not None, hit


def build_library(cfg: dict, tracks: list) -> set:
    """Fingerprints of recorded clean tracks (arrays in stats.baseline column layout), in-ROI only."""
    from phantomguard.stats.baseline import P_CYCLE, P_R, P_RCS, P_T, P_VR, P_VX, P_VY, P_X, P_Y

    rp = cfg["replay"]
    lib: set = set()
    for p in tracks:
        if len(p) <= rp["k_gram"]:
            continue
        pts = [TrackPoint(int(r[P_CYCLE]), r[P_T], r[P_X], r[P_Y], r[P_VX], r[P_VY], r[P_RCS], r[P_R], r[P_VR], -1)
               for r in p]
        for _, tfp, rfp in track_fingerprints(pts, rp["k_gram"], rp["disp_quant"], rp["vel_quant"],
                                              rp["min_complexity"], cfg["motion"]["moving_threshold_mps"],
                                              cfg["roi"]["max_range"]):
            lib.add(tfp)
            lib.add(rfp)
    return lib
