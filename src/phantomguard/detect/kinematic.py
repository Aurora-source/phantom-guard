"""Layer 2: physics / kinematics, per track (slot-linked), in-ROI objects only.

Protocol checks remain global; all physics checks, including value-level RCS, use the ROI.
"""

from __future__ import annotations

import math

import numpy as np

from phantomguard.config import bval
from phantomguard.detect.common import ObjVerdict
from phantomguard.stats.baseline import rcs_out_of_band
from phantomguard.tracks import Track


def lsq_rate(t: np.ndarray, r: np.ndarray) -> float:
    tc = t - t.mean()
    var = float((tc * tc).sum())
    return float((tc * (r - r.mean())).sum() / var) if var > 0 else 0.0


class KinematicChecker:
    def __init__(self, cfg: dict, baseline: dict):
        g = lambda k: bval(baseline, k)
        self.w = cfg["kinematic"]["window_cycles"]
        self.wr = cfg["kinematic"]["rcs_window_cycles"]
        self.roi = cfg["roi"]["max_range"]
        self.rcs_integer = g("rcs_integer")
        self.rcs_lo, self.rcs_hi = g("rcs_lo"), g("rcs_hi")
        self.speed_max = g("speed_max")
        self.accel_hard = g("accel_hard")
        self.rr_scale = g("rr_scale")
        self.rr_hard = g("rr_resid_hard")
        self.pos_speed_hard = g("pos_speed_hard")
        self.rcs_std_hard = g("rcs_std_hard")
        self.rcs_band = g("rcs_by_range")
        self.coloc = g("colocation_min")
        bh = g("birth_range_hist")
        counts = np.asarray(bh["counts"], dtype=float) + 1.0  # Laplace smoothing
        self.birth_edges = np.asarray(bh["edges"])
        self.birth_nll = -np.log(counts / counts.sum())

    def check_value(self, o, v: ObjVerdict) -> None:
        if self.rcs_integer and o.rcs != round(o.rcs):
            v.add("RCS_GRID")
        if not self.rcs_lo <= o.rcs <= self.rcs_hi:
            v.add("RCS_RANGE")

    def check_track(self, o, tr: Track, v: ObjVerdict) -> None:
        pts = tr.points
        if o.speed > self.speed_max:
            v.add("SPEED")
        if tr.total_points == 1:
            i = min(max(int(np.searchsorted(self.birth_edges, o.range)) - 1, 0), len(self.birth_nll) - 1)
            v.scores["birth_nll"] = float(self.birth_nll[i])
            if tr.born_by_jump:
                v.add("JUMP")
        if len(pts) >= 2 and pts[-2].rng <= self.roi:
            a, b = pts[-2], pts[-1]
            dt = b.t_s - a.t_s
            if dt > 0:
                acc = math.hypot(b.vx - a.vx, b.vy - a.vy) / dt
                v.scores["accel"] = acc
                if acc > self.accel_hard:
                    v.add("ACCEL")
        if len(pts) >= self.w:
            win = list(pts)[-self.w:]
            if all(p.rng <= self.roi for p in win):
                t = np.fromiter((p.t_s for p in win), float, self.w)
                r = np.fromiter((p.rng for p in win), float, self.w)
                vr = np.fromiter((p.vr for p in win), float, self.w)
                # The training envelope uses the same LSQ calculation on quantised frames.
                # It therefore includes 0.2-position quantisation and held sensor values.
                res = abs(lsq_rate(t, r) - self.rr_scale * vr.mean())
                v.scores["rr_resid"] = res
                if res > self.rr_hard:
                    v.add("RR_RESID")
                T = t[-1] - t[0]
                if T > 0:
                    ps = math.hypot(win[-1].x - win[0].x, win[-1].y - win[0].y) / T
                    v.scores["pos_speed"] = ps
                    if ps > self.pos_speed_hard:
                        v.add("POS_SPEED")
        if len(pts) >= self.wr:
            win = list(pts)[-self.wr:]
            if all(p.rng <= self.roi for p in win):
                rc = np.fromiter((p.rcs for p in win), float, self.wr)
                sd = float(rc.std())
                v.scores["rcs_std"] = sd
                if sd > self.rcs_std_hard:
                    v.add("RCS_STD")
        if rcs_out_of_band(o.range, o.rcs, self.rcs_band):
            v.add("RCS_BAND")

    def check_colocation(self, items: list[tuple]) -> None:
        """items: (x, y, track_age, verdict) for in-ROI objects; flags the younger of a too-close pair."""
        n = len(items)
        for i in range(n):
            xi, yi, ai, vi = items[i]
            for j in range(i + 1, n):
                xj, yj, aj, vj = items[j]
                if math.hypot(xi - xj, yi - yj) < self.coloc:
                    (vi if ai < aj else vj).add("COLOC")
