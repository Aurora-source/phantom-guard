"""Detector profile v2: clean-data models behind the reworked rules.

Everything here is fitted from permitted clean TRAIN segments only (never labels, never test). Each
model states its quantisation floor and its independent support so the detector can report
``low_support`` / ``global_fallback`` instead of over-claiming. Recording identity, absolute time,
cycle numbers, counters and numeric slots are never inputs.

Models
------
arrival_pos   frame k of a burst arrives at ``a + b*k`` ticks (serial CAN transmission). Only LATE
              arrivals are evidence (one-sided): a two-sided test would also flag the faster
              back-to-back re-spacing used by the A3/A4 simulator for a reason unrelated to the attack.
rcs_envelope  smooth range-conditional [lower, upper] RCS envelope. Kernel-weighted quantiles with each
              track's weight damped (``1/sqrt(len)``) so long static clutter cannot dominate, shrunk
              towards the global envelope by independent track support, plus a one-grid-step margin.
rcs_std_envelope  the same construction for the rolling RCS standard deviation.
drift_model   position change over horizon h versus the integral of reported velocity,
              ``e_h = p_t - p_{t-h} - B * sum(v_j dt_j)``, in the line-of-sight frame with separate radial
              and tangential mapping/scale per horizon and motion regime. Reported (vx, vy) is only
              weakly tied to the Cartesian motion, so ``B`` and scales are fitted, not assumed.
"""

from __future__ import annotations

import math

import numpy as np

from phantomguard.stats.baseline import P_CYCLE, P_R, P_RCS, P_T, P_VR, P_VX, P_VY, P_X, P_Y

V2_SCHEMA = 1
RCS_GRID_STEP = 1.0          # clean data holds whole-dBsm RCS only (payload resolution is 0.5 dBsm)
POS_QUANT = 0.2              # position resolution of the payload
DRIFT_HORIZONS = (8, 16, 32)


# --------------------------------------------------------------------------- weighted statistics


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w) - 0.5 * w
    cum /= w.sum()
    return float(np.interp(q, cum, v))


def effective_tracks(track_weights: np.ndarray) -> float:
    """Number of independent tracks behind a kernel estimate: (sum w)^2 / sum w^2 over per-track weights."""
    s2 = float((track_weights ** 2).sum())
    return float(track_weights.sum() ** 2 / s2) if s2 > 0 else 0.0


# --------------------------------------------------------------------------- arrival by burst position


def fit_arrival_pos(pairs: list[tuple[int, int]]) -> dict:
    arr = np.asarray(pairs, dtype=float)
    k, off = arr[:, 0], arr[:, 1]
    b, a = np.polyfit(k, off, 1)
    resid = off - (a + b * k)
    return {"a": float(a), "b": float(b), "n": int(len(arr)), "max_position": int(k.max()),
            "resid_q": {str(q): float(np.quantile(resid, q)) for q in (0.5, 0.99, 0.999, 0.9999, 1.0)},
            "resid_std": float(resid.std()), "quantisation_floor_ticks": 1.0,
            "base_bound": float(np.quantile(resid, 0.9999)) + 1.0,
            "one_sided": "late arrivals only; see module docstring"}


# --------------------------------------------------------------------------- RCS envelopes


def _track_points(tracks, roi: float, col: int):
    """(range, value, per-point weight, track index) of in-ROI points; weight = 1/sqrt(track length)."""
    rs, vs, ws, ts = [], [], [], []
    for ti, p in enumerate(tracks):
        q = p[p[:, P_R] <= roi]
        if not len(q):
            continue
        rs.append(q[:, P_R]); vs.append(q[:, col]); ws.append(np.full(len(q), 1.0 / math.sqrt(len(q)))); ts.append(np.full(len(q), ti))
    return np.concatenate(rs), np.concatenate(vs), np.concatenate(ws), np.concatenate(ts)


def _envelope(rs, vs, ws, ts, knots, bandwidth, q_lo, q_hi, n0, floor_lo=None) -> dict:
    g_lo, g_hi = weighted_quantile(vs, ws, q_lo), weighted_quantile(vs, ws, q_hi)
    lo, hi, neff = [], [], []
    for k in knots:
        kw = np.exp(-0.5 * ((rs - k) / bandwidth) ** 2)
        w = ws * kw
        per_track = np.bincount(ts, weights=kw, minlength=int(ts.max()) + 1)
        n_trk = effective_tracks(per_track[per_track > 1e-3])
        neff.append(n_trk)
        if w.sum() <= 0 or n_trk < 1.0:
            lo.append(g_lo); hi.append(g_hi)
            continue
        a = n_trk / (n_trk + n0)
        lo.append(a * weighted_quantile(vs, w, q_lo) + (1 - a) * g_lo)
        hi.append(a * weighted_quantile(vs, w, q_hi) + (1 - a) * g_hi)
    return {"knots": [float(k) for k in knots], "lower": [float(x) for x in lo], "upper": [float(x) for x in hi],
            "n_eff_tracks": [round(float(x), 2) for x in neff], "global": [float(g_lo), float(g_hi)],
            "bandwidth": bandwidth, "shrink_n0": n0, "quantiles": [q_lo, q_hi]}


def fit_rcs_envelope(tracks, roi: float, q: float = 0.999, bandwidth: float = 2.0, n0: float = 8.0) -> dict:
    rs, vs, ws, ts = _track_points(tracks, roi, P_RCS)
    env = _envelope(rs, vs, ws, ts, np.arange(0.0, roi + 1e-9, 1.0), bandwidth, 1 - q, q, n0)
    env.update(grid_step=RCS_GRID_STEP, n_points=int(len(rs)), n_tracks=int(len(np.unique(ts))))
    return env


def fit_rcs_std_envelope(tracks, roi: float, window: int, q: float = 0.999, bandwidth: float = 3.0, n0: float = 8.0) -> dict:
    rs, vs, ts, ws = [], [], [], []
    sw = np.lib.stride_tricks.sliding_window_view
    for ti, p in enumerate(tracks):
        if len(p) < window:
            continue
        inside = p[:, P_R] <= roi
        ok = sw(inside, window).all(axis=1)
        if not ok.any():
            continue
        std = sw(p[:, P_RCS], window).std(axis=1)[ok]
        rng = sw(p[:, P_R], window).mean(axis=1)[ok]
        rs.append(rng); vs.append(std); ts.append(np.full(len(std), ti)); ws.append(np.full(len(std), 1.0 / math.sqrt(len(std))))
    rs, vs, ws, ts = map(np.concatenate, (rs, vs, ws, ts))
    env = _envelope(rs, vs, ws, ts, np.arange(0.0, roi + 1e-9, 1.5), bandwidth, 0.0, q, n0)
    env.pop("lower"), env.pop("quantiles")
    env.update(window=int(window), n_windows=int(len(rs)), quantile=q, floor=0.5 * RCS_GRID_STEP)
    return env


def interp(knots, values, x: float) -> float:
    return float(np.interp(x, knots, values))


# --------------------------------------------------------------------------- drift model


def _los_frame(x0, y0):
    r = np.hypot(x0, y0)
    r = np.where(r > 0, r, 1.0)
    ux, uy = x0 / r, y0 / r
    return ux, uy, -uy, ux


def drift_innovation_arrays(p: np.ndarray, h: int):
    """Radial/tangential position change and velocity integral over h steps for every end point t >= h.

    Uses observed dt (so a bridged gap integrates the held velocity over its real duration) and the
    line-of-sight frame at the window start. Returns (dr, dtau, ir, itau, mean_speed, n_gaps).
    """
    t = p[:, P_T]
    dt = np.diff(t)
    x, y, vx, vy = p[:, P_X], p[:, P_Y], p[:, P_VX], p[:, P_VY]
    cs = lambda a: np.concatenate([[0.0], np.cumsum(a[:-1] * dt)])
    ix, iy = cs(vx)[h:] - cs(vx)[:-h], cs(vy)[h:] - cs(vy)[:-h]
    dx, dy = x[h:] - x[:-h], y[h:] - y[:-h]
    ux, uy, tx, ty = _los_frame(x[:-h], y[:-h])
    cyc = p[:, P_CYCLE]
    gaps = (cyc[h:] - cyc[:-h]) - h
    speed = np.hypot(vx, vy)
    cum = np.concatenate([[0.0], np.cumsum(speed)])
    mean_speed = (cum[h:len(speed)] - cum[:len(speed) - h]) / h
    return (dx * ux + dy * uy, dx * tx + dy * ty, ix * ux + iy * uy, ix * tx + iy * ty, mean_speed, gaps)


def fit_drift_model(tracks, moving_threshold: float, horizons=DRIFT_HORIZONS) -> dict:
    """Per horizon and regime ('static'|'moving' by mean reported speed): B_radial, B_tangential and
    robust residual scales, floored at the position quantisation of two end points."""
    floor = POS_QUANT / math.sqrt(6.0)          # std of the difference of two uniform quantisation errors
    out = {"horizons": list(horizons), "regime_speed": moving_threshold, "floor": floor, "models": {}}
    for h in horizons:
        rows = [np.column_stack(drift_innovation_arrays(p, h)) for p in tracks if len(p) > h]
        if not rows:
            continue
        A = np.concatenate(rows)
        A = A[A[:, 5] == 0]                          # contiguous windows only for the fit
        for regime, mask in (("static", A[:, 4] < moving_threshold), ("moving", A[:, 4] >= moving_threshold)):
            a = A[mask]
            if len(a) < 200:
                out["models"][f"{h}:{regime}"] = {"status": "low_support", "n": int(len(a))}
                continue
            dr, dtau, ir, itau = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
            Br = float((dr @ ir) / (ir @ ir)) if (ir @ ir) > 1e-9 else 0.0
            Bt = float((dtau @ itau) / (itau @ itau)) if (itau @ itau) > 1e-9 else 0.0
            er, et = dr - Br * ir, dtau - Bt * itau
            mad = lambda v: 1.4826 * float(np.median(np.abs(v - np.median(v))))
            out["models"][f"{h}:{regime}"] = {
                "status": "supported", "n": int(len(a)), "B_radial": Br, "B_tangential": Bt,
                "corr_radial": float(np.corrcoef(dr, ir)[0, 1]) if ir.std() > 0 else None,
                "corr_tangential": float(np.corrcoef(dtau, itau)[0, 1]) if itau.std() > 0 else None,
                "scale_radial": max(mad(er), floor), "scale_tangential": max(mad(et), floor),
                "std_radial": float(er.std()), "std_tangential": float(et.std()),
                "q999_radial": float(np.quantile(np.abs(er), 0.999)), "q999_tangential": float(np.quantile(np.abs(et), 0.999))}
    return out


def drift_z(p: np.ndarray, model: dict, h: int, moving_threshold: float):
    """z_h per end point t (nan where unscored): sqrt((e_r/s_r)^2 + (e_t/s_t)^2) with the regime's model."""
    n = len(p)
    z = np.full(n, np.nan)
    if n <= h:
        return z
    dr, dtau, ir, itau, speed, gaps = drift_innovation_arrays(p, h)
    for regime, mask in (("static", speed < moving_threshold), ("moving", speed >= moving_threshold)):
        m = model["models"].get(f"{h}:{regime}")
        if not m or m.get("status") != "supported":
            continue
        er, et = dr - m["B_radial"] * ir, dtau - m["B_tangential"] * itau
        zz = np.sqrt((er / m["scale_radial"]) ** 2 + (et / m["scale_tangential"]) ** 2)
        sel = mask & (gaps == 0)
        z[h:][sel] = zz[sel]
    return z


# --------------------------------------------------------------------------- entry point


def learn_v2(cfg: dict, st, base: dict) -> dict:
    """New baseline entries (``{"value", "rule", "split"}``) for profile v2, from TRAIN SegmentStats ``st``."""
    roi = cfg["roi"]["max_range"]
    thr = cfg["motion"]["moving_threshold_mps"]
    wr = cfg["kinematic"]["rcs_window_cycles"]
    tracks = [p for p in st.tracks.values() if len(p)]
    mk = lambda value, rule: {"value": value, "rule": rule, "split": "train"}
    return {
        "arrival_pos": mk(fit_arrival_pos(st.arrival_k),
                          "arrival offset ~ a + b*k by burst position k (one-sided, late arrivals); train, robust linear fit"),
        "rcs_envelope": mk(fit_rcs_envelope(tracks, roi),
                           "range-smoothed q0.001/q0.999 envelope of in-ROI RCS, per-track damped weights, shrunk by track support"),
        "rcs_std_envelope": mk(fit_rcs_std_envelope(tracks, roi, wr),
                               f"range-smoothed q0.999 of rolling RCS std over {wr} cycles, shrunk by track support"),
        "drift_model": mk(fit_drift_model([p for p in tracks if (p[:, P_R] <= roi).all()], thr),
                          "radial/tangential position-vs-integrated-velocity mapping and robust scales per horizon and regime"),
    }


# --------------------------------------------------------------------------- profile assembly

V2_OFF = ["ARRIVAL", "RCS_BAND", "RCS_RANGE"]   # replaced by position-conditional / envelope models
LEGACY_TAIL_CODES = ["SLOT_RANGE", "SPEED", "ACCEL", "RR_RESID", "POS_SPEED", "RCS_STD", "COLOC"]


def hard_codes_v2() -> list[str]:
    """Structural violations and exact (zero-clean-violation) regularities alert immediately."""
    from phantomguard.detect.common import REASONS
    from phantomguard.detect.evidence import REASON_CLASS

    return sorted(c for c in REASONS if REASON_CLASS.get(c) in {"structural", "exact_regularity"})


def apply_v2(cfg: dict, base: dict, st) -> dict:
    """Add the profile-v2 contract, models, default thresholds and fusion policy to a baseline dict."""
    base.update(learn_v2(cfg, st, base))
    base["detector_contract"] = {
        "value": {"profile": "v2", "schema": V2_SCHEMA, "off": list(V2_OFF),
                  "new_codes": ["ARRIVAL_POS", "RCS_ENV", "DRIFT", "DRIFT_STATIC"],
                  "rescored_codes": {"REPLAY": "flags when the run of consecutive matching windows exceeds the "
                                               "calibrated threshold (legacy and uncalibrated: every hit)"}},
        "rule": "structural and exact-regularity rules stay hard; empirical tails and new conditional models are soft "
                "evidence whose exceedance thresholds are calibrated from out-of-recording clean episodes",
        "split": "train"}
    # Uncalibrated defaults: RCS_ENV flags one grid step beyond the envelope; DRIFT/DRIFT_STATIC are off and
    # REPLAY flags every hit (legacy behaviour) until calibrate --profile v2 runs.
    base["rule_thresholds"] = {"value": {"RCS_ENV": 1.0}, "rule": "default until calibrate --profile v2 runs",
                               "split": "train", "calibrated": False}
    base["fusion_policy"] = {"value": {"hard_codes": hard_codes_v2(), "cycle_m": 2, "cycle_n": 4,
                                       "learned_alone": False},
                             "rule": "hard = structural + exact_regularity classes; cycle-level soft reasons need 2 of 4 cycles",
                             "split": "train"}
    return base
