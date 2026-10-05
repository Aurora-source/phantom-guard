"""Layer 2: physics / kinematics, per track (slot-linked), in-ROI objects only.

Protocol checks remain global; all physics checks, including value-level RCS, use the ROI.
"""

from __future__ import annotations

import math

import numpy as np

from phantomguard.config import bval
from phantomguard.detect.common import ObjVerdict
from phantomguard.detect.evidence import support_record
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
        fi = [v.frame_index]
        if self.rcs_integer and o.rcs != round(o.rcs):
            v.note("RCS_GRID", frames=fi, observed=o.rcs, suspect_frames=fi,
                   note="clean data holds only whole-dBsm RCS; the payload itself allows 0.5 dBsm steps")
        if not self.rcs_lo <= o.rcs <= self.rcs_hi:
            v.note("RCS_RANGE", frames=fi, observed=o.rcs, lo=self.rcs_lo, hi=self.rcs_hi, suspect_frames=fi)

    def check_track(self, o, tr: Track, v: ObjVerdict) -> None:
        pts = tr.points
        fi = [v.frame_index]
        cyc = lambda n: (pts[-n].cycle_index, pts[-1].cycle_index)
        if o.speed > self.speed_max:
            v.note("SPEED", frames=fi, observed=o.speed, hi=self.speed_max, suspect_frames=fi, suspect_track=tr.track_id,
                   cycles=cyc(1))
        if tr.total_points == 1:
            i = min(max(int(np.searchsorted(self.birth_edges, o.range)) - 1, 0), len(self.birth_nll) - 1)
            v.scores["birth_nll"] = float(self.birth_nll[i])
            if tr.born_by_jump:
                v.note("JUMP", frames=fi, observed=o.range, suspect_frames=fi, suspect_track=tr.track_id, cycles=cyc(1),
                       note="slot reused with a position jump larger than the reassignment threshold")
        if len(pts) >= 2 and pts[-2].rng <= self.roi:
            a, b = pts[-2], pts[-1]
            dt = b.t_s - a.t_s
            if dt > 0:
                acc = math.hypot(b.vx - a.vx, b.vy - a.vy) / dt
                v.scores["accel"] = acc
                if acc > self.accel_hard:
                    v.note("ACCEL", frames=[a.frame_index, b.frame_index], observed=acc, hi=self.accel_hard,
                           suspect_frames=fi, suspect_track=tr.track_id, cycles=cyc(2),
                           normalized=acc / self.accel_hard)
        if len(pts) >= self.w:
            win = list(pts)[-self.w:]
            if all(p.rng <= self.roi for p in win):
                t = np.fromiter((p.t_s for p in win), float, self.w)
                r = np.fromiter((p.rng for p in win), float, self.w)
                vr = np.fromiter((p.vr for p in win), float, self.w)
                # The training envelope uses the same LSQ calculation on quantised frames.
                # It therefore includes 0.2-position quantisation and held sensor values.
                rate = lsq_rate(t, r)
                expected = self.rr_scale * vr.mean()
                res = abs(rate - expected)
                v.scores["rr_resid"] = res
                if res > self.rr_hard:
                    v.note("RR_RESID", frames=[p.frame_index for p in win], observed=rate, expected=expected,
                           lo=expected - self.rr_hard, hi=expected + self.rr_hard, normalized=res / self.rr_hard,
                           suspect_frames=fi, suspect_track=tr.track_id, cycles=(win[0].cycle_index, win[-1].cycle_index),
                           note="radial range-rate versus integrated reported radial velocity")
                T = t[-1] - t[0]
                if T > 0:
                    ps = math.hypot(win[-1].x - win[0].x, win[-1].y - win[0].y) / T
                    v.scores["pos_speed"] = ps
                    if ps > self.pos_speed_hard:
                        v.note("POS_SPEED", frames=[win[0].frame_index, win[-1].frame_index], observed=ps,
                               hi=self.pos_speed_hard, normalized=ps / self.pos_speed_hard, suspect_frames=fi,
                               suspect_track=tr.track_id, cycles=(win[0].cycle_index, win[-1].cycle_index))
        if len(pts) >= self.wr:
            win = list(pts)[-self.wr:]
            if all(p.rng <= self.roi for p in win):
                rc = np.fromiter((p.rcs for p in win), float, self.wr)
                sd = float(rc.std())
                v.scores["rcs_std"] = sd
                if sd > self.rcs_std_hard:
                    v.note("RCS_STD", frames=[p.frame_index for p in win], observed=sd, hi=self.rcs_std_hard,
                           normalized=sd / self.rcs_std_hard, suspect_frames=fi, suspect_track=tr.track_id,
                           cycles=(win[0].cycle_index, win[-1].cycle_index))
        if rcs_out_of_band(o.range, o.rcs, self.rcs_band):
            edges, bands = self.rcs_band["edges"], self.rcs_band["bands"]
            i = min(max(int(np.searchsorted(edges, o.range, side="left")) - 1, 0), len(bands) - 1)
            lo, hi, n = bands[i]
            v.note("RCS_BAND", frames=fi, observed=o.rcs, lo=lo, hi=hi, suspect_frames=fi, suspect_track=tr.track_id,
                   cycles=cyc(1), support=support_record(n, "supported" if n >= 200 else "global_fallback",
                                                         range_bin=[edges[i], edges[i + 1]]))

    def check_colocation(self, items: list[tuple]) -> None:
        """items: (x, y, track_age, verdict) for in-ROI objects; flags the younger of a too-close pair."""
        n = len(items)
        for i in range(n):
            xi, yi, ai, vi = items[i]
            for j in range(i + 1, n):
                xj, yj, aj, vj = items[j]
                d = math.hypot(xi - xj, yi - yj)
                if d < self.coloc:
                    younger, older = (vi, vj) if ai < aj else (vj, vi)
                    pair = [vi.frame_index, vj.frame_index]
                    younger.note("COLOC", frames=pair, observed=d, lo=self.coloc, suspect_frames=pair,
                                 suspect_basis="colocated_pair",
                                 note="the younger track is flagged; track age does not show which object is forged")
