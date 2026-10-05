"""Layer 2: physics / kinematics, per track (slot-linked), in-ROI objects only.

Protocol checks remain global; all physics checks, including value-level RCS, use the ROI.

Every threshold rule is expressed as an exceedance ``z = value - bound`` (natural units) that flags when
``z > c``. Legacy profile: ``bound`` is the clean-train quantile/maximum and ``c = 0`` (identical to the
original ``value > bound``). Profile v2: models replace some bounds (smooth RCS envelope, conditional RCS
std, drift consistency) and ``c`` comes from ``baseline['rule_thresholds']``, calibrated from
out-of-recording clean episodes. ``capture_z`` stores every evaluated exceedance as ``scores['z:CODE']``
so offline calibration can re-threshold without re-running the detector.
"""

from __future__ import annotations

import math

import numpy as np

from phantomguard.config import bval
from phantomguard.detect.common import ObjVerdict, contract_of, rule_thresholds_of
from phantomguard.detect.evidence import support_record
from phantomguard.stats.baseline import rcs_out_of_band
from phantomguard.stats.v2 import RCS_GRID_STEP, interp
from phantomguard.tracks import Track


def lsq_rate(t: np.ndarray, r: np.ndarray) -> float:
    tc = t - t.mean()
    var = float((tc * tc).sum())
    return float((tc * (r - r.mean())).sum() / var) if var > 0 else 0.0


class KinematicChecker:
    def __init__(self, cfg: dict, baseline: dict, *, capture_z: bool = False):
        g = lambda k: bval(baseline, k)
        self.w = cfg["kinematic"]["window_cycles"]
        self.wr = cfg["kinematic"]["rcs_window_cycles"]
        self.roi = cfg["roi"]["max_range"]
        self.moving_threshold = cfg["motion"]["moving_threshold_mps"]
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
        self.capture_z = capture_z
        contract = contract_of(baseline)
        self.v2 = contract is not None
        self.off = set(contract.get("off", ())) if contract else set()
        self.c = rule_thresholds_of(baseline)
        if self.v2:
            self.env = g("rcs_envelope")
            self.std_env = g("rcs_std_envelope")
            self.drift = g("drift_model")
            self.drift_h = [h for h in self.drift["horizons"]]

    # ---- helpers
    def _thr(self, code: str, default: float = 0.0) -> float:
        return self.c.get(code, default)

    def _z(self, v: ObjVerdict, code: str, z: float) -> None:
        if self.capture_z:
            v.scores["z:" + code] = float(z)

    def check_value(self, o, v: ObjVerdict) -> None:
        fi = [v.frame_index]
        if self.rcs_integer and o.rcs != round(o.rcs):
            v.note("RCS_GRID", frames=fi, observed=o.rcs, suspect_frames=fi,
                   note="clean data holds only whole-dBsm RCS; the payload itself allows 0.5 dBsm steps")
        if "RCS_RANGE" not in self.off:
            z = max(o.rcs - self.rcs_hi, self.rcs_lo - o.rcs)
            self._z(v, "RCS_RANGE", z)
            if z > self._thr("RCS_RANGE"):
                v.note("RCS_RANGE", frames=fi, observed=o.rcs, lo=self.rcs_lo, hi=self.rcs_hi, suspect_frames=fi)
        if self.v2 and "RCS_ENV" not in self.off:
            lo = interp(self.env["knots"], self.env["lower"], o.range)
            hi = interp(self.env["knots"], self.env["upper"], o.range)
            z = max(o.rcs - hi, lo - o.rcs) / self.env["grid_step"]   # grid steps beyond the envelope
            self._z(v, "RCS_ENV", z)
            c = self._thr("RCS_ENV", 1.0)
            if z > c:
                n_eff = interp(self.env["knots"], self.env["n_eff_tracks"], o.range)
                v.note("RCS_ENV", frames=fi, observed=o.rcs, lo=lo - c * self.env["grid_step"], hi=hi + c * self.env["grid_step"],
                       normalized=z, suspect_frames=fi, suspect_track=v.track_id,
                       support=support_record(round(n_eff, 1), "supported" if n_eff >= 5 else "low_support",
                                              basis="independent tracks behind the smoothed envelope at this range",
                                              grid_step=self.env["grid_step"]))

    def check_track(self, o, tr: Track, v: ObjVerdict) -> None:
        pts = tr.points
        fi = [v.frame_index]
        cyc = lambda n: (pts[-n].cycle_index, pts[-1].cycle_index)
        z = o.speed - self.speed_max
        self._z(v, "SPEED", z)
        if z > self._thr("SPEED"):
            v.note("SPEED", frames=fi, observed=o.speed, hi=self.speed_max, suspect_frames=fi, suspect_track=tr.track_id,
                   cycles=cyc(1))
        if tr.total_points == 1:
            i = min(max(int(np.searchsorted(self.birth_edges, o.range)) - 1, 0), len(self.birth_nll) - 1)
            v.scores["birth_nll"] = float(self.birth_nll[i])
            if tr.born_by_jump:
                v.note("JUMP", frames=fi, observed=tr.gate_distance, hi=None, suspect_frames=fi, suspect_track=tr.track_id,
                       cycles=cyc(1), support=support_record(None, "supported", association=tr.assoc,
                                                             predecessor_track=tr.predecessor),
                       note="slot reused with a position jump larger than the reassignment threshold"
                            + (" (near the gate: association ambiguous)" if tr.assoc == "ambiguous_reset" else ""))
        if len(pts) >= 2 and pts[-2].rng <= self.roi:
            a, b = pts[-2], pts[-1]
            dt = b.t_s - a.t_s
            if dt > 0:
                acc = math.hypot(b.vx - a.vx, b.vy - a.vy) / dt
                v.scores["accel"] = acc
                self._z(v, "ACCEL", acc - self.accel_hard)
                if acc > self.accel_hard + self._thr("ACCEL"):
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
                self._z(v, "RR_RESID", res - self.rr_hard)
                if res > self.rr_hard + self._thr("RR_RESID"):
                    v.note("RR_RESID", frames=[p.frame_index for p in win], observed=rate, expected=expected,
                           lo=expected - self.rr_hard, hi=expected + self.rr_hard, normalized=res / self.rr_hard,
                           suspect_frames=fi, suspect_track=tr.track_id, cycles=(win[0].cycle_index, win[-1].cycle_index),
                           note="radial range-rate versus integrated reported radial velocity")
                T = t[-1] - t[0]
                if T > 0:
                    ps = math.hypot(win[-1].x - win[0].x, win[-1].y - win[0].y) / T
                    v.scores["pos_speed"] = ps
                    self._z(v, "POS_SPEED", ps - self.pos_speed_hard)
                    if ps > self.pos_speed_hard + self._thr("POS_SPEED"):
                        v.note("POS_SPEED", frames=[win[0].frame_index, win[-1].frame_index], observed=ps,
                               hi=self.pos_speed_hard, normalized=ps / self.pos_speed_hard, suspect_frames=fi,
                               suspect_track=tr.track_id, cycles=(win[0].cycle_index, win[-1].cycle_index))
        if len(pts) >= self.wr:
            win = list(pts)[-self.wr:]
            if all(p.rng <= self.roi for p in win):
                rc = np.fromiter((p.rcs for p in win), float, self.wr)
                sd = float(rc.std())
                v.scores["rcs_std"] = sd
                bound = self.rcs_std_hard
                if self.v2 and "RCS_STD_ENV" not in self.off:
                    mean_r = sum(p.rng for p in win) / self.wr
                    bound = max(interp(self.std_env["knots"], self.std_env["upper"], mean_r), self.std_env["floor"])
                self._z(v, "RCS_STD", sd - bound)
                if sd > bound + self._thr("RCS_STD"):
                    v.note("RCS_STD", frames=[p.frame_index for p in win], observed=sd, hi=bound,
                           normalized=sd / bound, suspect_frames=fi, suspect_track=tr.track_id,
                           cycles=(win[0].cycle_index, win[-1].cycle_index))
        if "RCS_BAND" not in self.off and rcs_out_of_band(o.range, o.rcs, self.rcs_band):
            edges, bands = self.rcs_band["edges"], self.rcs_band["bands"]
            i = min(max(int(np.searchsorted(edges, o.range, side="left")) - 1, 0), len(bands) - 1)
            lo, hi, n = bands[i]
            v.note("RCS_BAND", frames=fi, observed=o.rcs, lo=lo, hi=hi, suspect_frames=fi, suspect_track=tr.track_id,
                   cycles=cyc(1), support=support_record(n, "supported" if n >= 200 else "global_fallback",
                                                         range_bin=[edges[i], edges[i + 1]]))
        if self.v2 and "DRIFT" not in self.off:
            self._drift(o, tr, v)

    def _drift(self, o, tr: Track, v: ObjVerdict) -> None:
        """Position change over several horizons vs the integral of reported velocity (see stats/v2.py)."""
        pts = tr.points
        b = pts[-1]
        best: dict = {}                     # regime -> (z, h, er, et, a, m): the strongest horizon per regime
        for h in self.drift_h:
            if len(pts) <= h:
                continue
            a = pts[-1 - h]
            if a.rng > self.roi:
                continue
            if (b.cycle_index - a.cycle_index) != h:
                v.score_status.setdefault("drift", "gap_in_window")
                continue
            regime = "moving" if (b.cspd - a.cspd) / h >= self.drift["regime_speed"] else "static"
            m = self.drift["models"].get(f"{h}:{regime}")
            if not m or m.get("status") != "supported":
                v.score_status.setdefault("drift", "low_support")
                continue
            r0 = a.rng if a.rng > 0 else 1.0
            ux, uy = a.x / r0, a.y / r0
            dx, dy = b.x - a.x, b.y - a.y
            ix, iy = b.civx - a.civx, b.civy - a.civy
            er = (dx * ux + dy * uy) - m["B_radial"] * (ix * ux + iy * uy)
            et = (-dx * uy + dy * ux) - m["B_tangential"] * (-ix * uy + iy * ux)
            z = math.sqrt((er / m["scale_radial"]) ** 2 + (et / m["scale_tangential"]) ** 2)
            if regime not in best or z > best[regime][0]:
                best[regime] = (z, h, er, et, a, m)
        if not best:
            return
        v.score_status["drift"] = "scored"
        for regime, (z, h, er, et, a, m) in best.items():
            code = "DRIFT" if regime == "moving" else "DRIFT_STATIC"
            self._z(v, code, z)
            if z > self._thr(code, 1e9):
                v.note(code, frames=[a.frame_index, b.frame_index], cycles=(a.cycle_index, b.cycle_index),
                       observed=math.hypot(er, et), expected=0.0, normalized=z, suspect_frames=[v.frame_index],
                       suspect_track=tr.track_id,
                       support=support_record(m["n"], "supported", horizon=h, regime=regime, radial_residual=er,
                                              tangential_residual=et, quantisation_floor=self.drift["floor"]),
                       note=f"position change over {h} cycles differs from the integrated reported velocity ({regime} regime)")

    def check_colocation(self, items: list[tuple], verdict_frames: bool = True) -> None:
        """items: (x, y, track_age, verdict) for in-ROI objects; flags the younger of a too-close pair."""
        n = len(items)
        for i in range(n):
            xi, yi, ai, vi = items[i]
            for j in range(i + 1, n):
                xj, yj, aj, vj = items[j]
                d = math.hypot(xi - xj, yi - yj)
                younger, older = (vi, vj) if ai < aj else (vj, vi)
                if self.capture_z and d < self.coloc + 1.0:
                    key = "z:COLOC"
                    younger.scores[key] = max(younger.scores.get(key, -1e9), self.coloc - d)
                if d < self.coloc - self._thr("COLOC"):
                    pair = [vi.frame_index, vj.frame_index]
                    younger.note("COLOC", frames=pair, observed=d, lo=self.coloc, suspect_frames=pair,
                                 suspect_basis="colocated_pair",
                                 note="the younger track is flagged; track age does not show which object is forged")
