"""Measure clean-data statistics and turn them into thresholds.

``collect(segments)`` replays segments through the same cycle assembler and track linker the
detector uses, so offline statistics and online checks see identical inputs.
``derive_thresholds(train_stats)`` applies a recorded rule to each statistic, and
``exceedance(val_stats, thresholds)`` reports the fraction of held-out clean items each
threshold would flag.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from phantomguard.config import raw_path
from phantomguard.cycles import iter_cycles
from phantomguard.eval.splits import Segment
from phantomguard.io.replay import ReplaySource
from phantomguard.tracks import TrackManager


@dataclass
class SegmentStats:
    header_gaps: list = field(default_factory=list)  # after warm-up
    warmup_gaps: list = field(default_factory=list)
    counter_steps: list = field(default_factory=list)
    offsets: list = field(default_factory=list)  # every object arrival offset
    first_offsets: list = field(default_factory=list)
    burst_gaps: list = field(default_factory=list)  # consecutive object frames inside a cycle
    objs_per_cycle: list = field(default_factory=list)
    order_viol: list = field(default_factory=list)  # prev_range - range for every adjacent pair
    slots_all: list = field(default_factory=list)
    slots_birth: list = field(default_factory=list)
    step_jumps: list = field(default_factory=list)  # same-slot displacement between consecutive cycles
    min_pair_dist: list = field(default_factory=list)  # per cycle, in ROI
    rcs_all: list = field(default_factory=list)
    rcs_range_roi: list = field(default_factory=list)  # (range, rcs) of in-ROI objects
    dyn_values: set = field(default_factory=set)
    reserved_values: set = field(default_factory=set)
    status_values: set = field(default_factory=set)
    tracks: dict = field(default_factory=dict)  # track_id -> np.ndarray of points
    births_in_roi_per_cycle: list = field(default_factory=list)
    slot_returns: int = 0  # slot absent one cycle then back within reassign_jump
    n_cycles: int = 0
    n_objects: int = 0
    n_out_of_roi: int = 0

    def merge(self, o: "SegmentStats") -> None:
        for k, v in self.__dict__.items():
            ov = getattr(o, k)
            if isinstance(v, list):
                v.extend(ov)
            elif isinstance(v, set):
                v |= ov
            elif isinstance(v, dict):
                # Re-key sequentially: track ids restart in every segment.
                base = len(v)
                for i, vv in enumerate(ov.values()):
                    v[base + i] = vv
            elif isinstance(v, int):
                setattr(self, k, v + ov)


# Track point columns
P_CYCLE, P_T, P_X, P_Y, P_VX, P_VY, P_RCS, P_R, P_VR = range(9)


def collect_segment(cfg: dict, seg: Segment) -> SegmentStats:
    st = SegmentStats()
    roi = cfg["roi"]["max_range"]
    warm = cfg["protocol"]["cadence_warmup_cycles"]
    src = ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi))
    tm = TrackManager(cfg["tracks"]["reassign_jump_default"], cfg["units"]["tick_seconds"],
                      cfg["motion"]["moving_threshold_mps"], history=2, max_gap_cycles=cfg["tracks"]["max_gap_cycles"])
    pts: dict[int, list] = defaultdict(list)
    prev_hdr_t = prev_counter = None
    hdr_seen = 0
    prev_pos: dict[int, tuple] = {}
    prev2_pos: dict[int, tuple] = {}
    for cyc in iter_cycles(src):
        st.n_cycles += 1
        if cyc.header is not None:
            st.status_values.add(cyc.header.status)
            if prev_hdr_t is not None:
                gap = cyc.header_t - prev_hdr_t
                (st.warmup_gaps if hdr_seen <= warm else st.header_gaps).append(gap)
                st.counter_steps.append((cyc.header.meas_counter - prev_counter) % 65536)
            prev_hdr_t, prev_counter = cyc.header_t, cyc.header.meas_counter
            hdr_seen += 1
        objs = cyc.objects
        st.objs_per_cycle.append(len(objs))
        st.n_objects += len(objs)
        for i, ob in enumerate(objs):
            o = ob.obj
            st.offsets.append(ob.offset)
            st.slots_all.append(o.slot)
            st.rcs_all.append(o.rcs)
            if o.range <= roi:
                st.rcs_range_roi.append((o.range, o.rcs))
            st.dyn_values.add(o.dyn_prop)
            st.reserved_values.add(o.reserved)
            if i:
                st.burst_gaps.append(ob.t - objs[i - 1].t)
                st.order_viol.append(objs[i - 1].obj.range - o.range)
            if o.slot in prev_pos:
                px, py = prev_pos[o.slot]
                st.step_jumps.append(math.hypot(o.x - px, o.y - py))
            elif o.slot in prev2_pos:
                px, py = prev2_pos[o.slot]
                if math.hypot(o.x - px, o.y - py) <= cfg["tracks"]["reassign_jump_default"]:
                    st.slot_returns += 1
        if objs:
            st.first_offsets.append(objs[0].offset)
        prev2_pos = prev_pos
        prev_pos = {ob.obj.slot: (ob.obj.x, ob.obj.y) for ob in objs}
        in_roi = [(ob.obj.x, ob.obj.y) for ob in objs if ob.obj.range <= roi]
        st.n_out_of_roi += len(objs) - len(in_roi)
        if len(in_roi) >= 2:
            a = np.array(in_roi)
            d = np.sqrt(((a[:, None, :] - a[None, :, :]) ** 2).sum(-1))
            d[np.diag_indices_from(d)] = np.inf
            st.min_pair_dist.append(float(d.min()))
        births = 0
        for ob, tr in tm.update(cyc):
            p = tr.last
            if tr.total_points == 1:
                st.slots_birth.append(tr.slot)
                births += p.rng <= roi
            pts[tr.track_id].append((p.cycle_index, p.t_s, p.x, p.y, p.vx, p.vy, p.rcs, p.rng, p.vr))
        st.births_in_roi_per_cycle.append(births)
    st.tracks = {k: np.array(v, dtype=float) for k, v in pts.items()}
    return st


def collect(cfg: dict, segments: list[Segment]) -> SegmentStats:
    total = SegmentStats()
    for seg in segments:
        total.merge(collect_segment(cfg, seg))
    return total


# ---------------------------------------------------------------- track-level features


def track_is_moving(p: np.ndarray, cfg: dict) -> bool:
    spd = np.hypot(p[:, P_VX], p[:, P_VY])
    return int((spd >= cfg["motion"]["moving_threshold_mps"]).sum()) >= cfg["kinematic"]["min_moving_cycles"]


def window_residuals(p: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per window of w points: least-squares range-rate, mean reported radial velocity, residual."""
    n = len(p)
    if n < w:
        return np.empty(0), np.empty(0), np.empty(0)
    sw = np.lib.stride_tricks.sliding_window_view
    t = sw(p[:, P_T], w)
    r = sw(p[:, P_R], w)
    tc = t - t.mean(axis=1, keepdims=True)
    var = (tc * tc).sum(axis=1)
    rr = np.where(var > 0, (tc * (r - r.mean(axis=1, keepdims=True))).sum(axis=1) / np.where(var > 0, var, 1), 0.0)
    mv = sw(p[:, P_VR], w).mean(axis=1)
    return rr, mv, rr - mv


def rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return np.empty(0)
    s = np.lib.stride_tricks.sliding_window_view(x, w)
    return s.std(axis=1)


@dataclass
class TrackFeatures:
    speeds_moving: np.ndarray
    accel: np.ndarray  # |dv|/dt between consecutive points, in-ROI tracks
    accel_moving: np.ndarray
    rr_all: np.ndarray  # window range-rate, all in-ROI windows
    mv_all: np.ndarray  # window mean reported radial velocity, same windows
    rr_fit: tuple  # (rr, vr) arrays for moving windows
    pos_speed: np.ndarray  # window displacement / time, in-ROI
    rcs_std: np.ndarray
    birth_range: np.ndarray
    birth_speed: np.ndarray
    lifetimes: np.ndarray
    n_moving_tracks: int
    n_tracks_roi: int
    hold_frac: np.ndarray  # per moving track: fraction of steps with identical x, y, vx, vy
    pos_change_frac: np.ndarray
    vel_change_frac: np.ndarray

    def rr_resid(self, scale: float) -> np.ndarray:
        return self.rr_all - scale * self.mv_all


def track_features(st: SegmentStats, cfg: dict) -> TrackFeatures:
    w = cfg["kinematic"]["window_cycles"]
    wr = cfg["kinematic"]["rcs_window_cycles"]
    roi = cfg["roi"]["max_range"]
    thr = cfg["motion"]["moving_threshold_mps"]
    sp, acc, accm, rra, mva, rrs, vrs, psp, rstd, br, bs, life, hold, pch, vch = ([] for _ in range(15))
    n_mov = n_roi = 0
    for tid, p in st.tracks.items():
        if len(p) == 0:
            continue
        inside = p[:, P_R] <= roi
        if not inside.any():
            continue
        n_roi += 1
        moving = track_is_moving(p[inside], cfg)
        n_mov += moving
        # Only a true linked-track birth in ROI enters the birth distribution. Entering
        # the ROI later does not invent a new birth or reuse outside-ROI statistics.
        if inside[0]:
            br.append(p[0, P_R])
            bs.append(math.hypot(p[0, P_VX], p[0, P_VY]))
        life.append(int(inside.sum()))
        spd = np.hypot(p[:, P_VX], p[:, P_VY])
        if moving:
            sp.append(spd[inside & (spd >= thr)])
        if len(p) >= 2:
            dt = np.diff(p[:, P_T])
            dv = np.hypot(np.diff(p[:, P_VX]), np.diff(p[:, P_VY]))
            a = dv / np.where(dt > 0, dt, np.nan)
            steps_inside = inside[:-1] & inside[1:]
            acc.append(a[steps_inside])
            if moving:
                accm.append(a[steps_inside])
        rr, mv, rs = window_residuals(p, w)
        whole_window_inside = (np.lib.stride_tricks.sliding_window_view(inside, w).all(axis=1)
                               if len(p) >= w else np.empty(0, dtype=bool))
        rr, mv = rr[whole_window_inside], mv[whole_window_inside]
        rra.append(rr)
        mva.append(mv)
        if moving:
            rrs.append(rr)
            vrs.append(mv)
            if len(p) >= 5:
                d = np.diff(p[:, [P_X, P_Y, P_VX, P_VY]], axis=0)
                d = d[inside[:-1] & inside[1:]]
                if len(d):
                    hold.append(float((np.abs(d).sum(1) == 0).mean()))
                    pch.append(float((np.abs(d[:, :2]).sum(1) > 0).mean()))
                    vch.append(float((np.abs(d[:, 2:]).sum(1) > 0).mean()))
        if len(p) >= w:
            d = np.hypot(p[w - 1:, P_X] - p[:-w + 1, P_X], p[w - 1:, P_Y] - p[:-w + 1, P_Y])
            T = p[w - 1:, P_T] - p[:-w + 1, P_T]
            psp.append((d / np.where(T > 0, T, np.nan))[whole_window_inside])
        rcs_inside = (np.lib.stride_tricks.sliding_window_view(inside, wr).all(axis=1)
                      if len(p) >= wr else np.empty(0, dtype=bool))
        rstd.append(rolling_std(p[:, P_RCS], wr)[rcs_inside])
    cat = lambda xs: np.concatenate(xs) if xs else np.empty(0)
    return TrackFeatures(cat(sp), cat(acc), cat(accm), cat(rra), cat(mva), (cat(rrs), cat(vrs)), cat(psp),
                         cat(rstd), np.array(br), np.array(bs), np.array(life), n_mov, n_roi,
                         np.array(hold), np.array(pch), np.array(vch))


def fit_rr_scale(tf: TrackFeatures, min_abs_v: float = 0.5) -> tuple[float, float, int]:
    """Least-squares k in range_rate ~= k * reported radial velocity (moving windows, |v| >= min_abs_v)."""
    rr, vr = tf.rr_fit
    m = np.abs(vr) >= min_abs_v
    if m.sum() < 20:
        raise ValueError(f"cannot learn rr_scale: need at least 20 moving windows with |radial v| >= "
                         f"{min_abs_v}, found {int(m.sum())}; check training segments and ROI")
    k = float(np.sum(rr[m] * vr[m]) / np.sum(vr[m] ** 2))
    return k, float(np.corrcoef(rr[m], vr[m])[0, 1]), int(m.sum())


# ---------------------------------------------------------------- thresholds


def _q(a, q):
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    return float(np.quantile(a, q)) if len(a) else float("nan")


def entry(value, rule, **extra):
    d = {"value": value, "rule": rule, "split": "train"}
    d.update(extra)
    return d


def derive_thresholds(st: SegmentStats, tf: TrackFeatures, cfg: dict, kq: float | None = None) -> dict:
    """kq overrides the quantile used for the soft kinematic thresholds (see scripts/calibrate.py)."""
    m = cfg["baseline"]["margin_ticks"]
    hq = kq if kq is not None else cfg["baseline"]["hard_quantile"]
    sq = cfg["baseline"]["soft_quantile"]
    gaps = np.array(st.header_gaps)
    offs = np.array(st.offsets)
    bg = np.array(st.burst_gaps)
    ov = np.array(st.order_viol)
    b = {}
    b["cadence_lo"] = entry(int(gaps.min()) - m, f"min(train header gaps after {cfg['protocol']['cadence_warmup_cycles']}-cycle warm-up) - {m} ticks")
    b["cadence_hi"] = entry(int(gaps.max()) + m, f"max(train header gaps) + {m} ticks")
    b["cadence_median"] = entry(float(np.median(gaps)), "median(train header gaps after capture warm-up)")
    b["counter_step"] = entry(1, "every train counter step is +1 (mod 65536)",
                              observed=sorted(set(int(s) for s in st.counter_steps)))
    b["arrival_lo"] = entry(max(0, int(offs.min()) - m), f"min(train arrival offsets) - {m} ticks")
    b["arrival_hi"] = entry(int(offs.max()) + m, f"max(train arrival offsets) + {m} ticks")
    b["first_arrival_hi"] = entry(int(np.max(st.first_offsets)) + m, f"max(train first-object offset) + {m} ticks")
    b["burst_gap_lo"] = entry(int(bg.min()), "min(train gap between consecutive object frames); a CAN frame cannot be shorter")
    b["burst_gap_hi"] = entry(int(bg.max()) + m, f"max(train gap between consecutive object frames) + {m} ticks")
    b["objs_per_cycle_lo"] = entry(int(min(st.objs_per_cycle)), "min(train objects per cycle)")
    b["objs_per_cycle_hi"] = entry(int(max(st.objs_per_cycle)), "max(train objects per cycle)")
    b["range_order_tol"] = entry(round(float(ov.max()) + 0.1, 3),
                                 "max(train range-order violation, prev_range - range) + 0.1 (half a 0.2 position step)",
                                 train_frac_cycles_sorted=None)
    b["slot_max"] = entry(int(max(st.slots_all)), "max(train slot id)")
    sb = np.bincount(np.array(st.slots_birth), minlength=256)[: max(st.slots_all) + 1]
    b["slot_birth_hist"] = entry([int(x) for x in sb], "counts of slot ids at track birth (train)")
    sa = np.bincount(np.array(st.slots_all), minlength=256)[: max(st.slots_all) + 1]
    b["slot_use_hist"] = entry([int(x) for x in sa], "counts of slot ids over all train objects")
    b["offset_hist"] = entry([int(x) for x in np.bincount(offs.astype(int))], "histogram of train arrival offsets (ticks)")
    b["burst_gap_hist"] = entry({int(k): int(v) for k, v in zip(*np.unique(bg, return_counts=True))},
                                "histogram of train inter-frame gaps (ticks)")
    b["fixed_fields"] = entry({"dyn_prop": sorted(st.dyn_values), "reserved": sorted(st.reserved_values),
                               "status": sorted(st.status_values)}, "set of values seen in train; anything else is a violation")
    rcs = np.array(st.rcs_all)
    b["rcs_lo"] = entry(float(rcs.min()), "min(train RCS)")
    b["rcs_hi"] = entry(float(rcs.max()), "max(train RCS)")
    b["rcs_integer"] = entry(bool(np.all(rcs == np.round(rcs))), "all train RCS values are integers")
    u, c = np.unique(rcs, return_counts=True)
    b["rcs_hist"] = entry({str(float(k)): int(v) for k, v in zip(u, c)}, "histogram of train RCS (dBsm)")
    rr_ = np.array(st.rcs_range_roi)
    edges = np.arange(0.0, cfg["roi"]["max_range"] + 1.0, 1.0)
    bands = []
    for a, c in zip(edges, edges[1:]):
        sel = rr_[(rr_[:, 0] > a) & (rr_[:, 0] <= c), 1] if len(rr_) else np.empty(0)
        if len(sel) >= 200:
            bands.append([float(np.quantile(sel, 1 - hq)), float(np.quantile(sel, hq)), int(len(sel))])
        else:
            bands.append([float(rcs.min()), float(rcs.max()), int(len(sel))])
    b["rcs_by_range"] = entry({"edges": edges.tolist(), "bands": bands},
                              f"per 1-unit range bin: [q{1 - hq:.5f}, q{hq}] of train in-ROI RCS; bins with < 200 samples use the global [min, max]")
    sj = np.array(st.step_jumps)
    b["reassign_jump"] = entry(cfg["tracks"]["reassign_jump_default"],
                               "CLAUDE.md default 1.0; kept because train same-slot step distribution has a clear gap (see q-values)",
                               step_q9999=_q(sj, 0.9999), step_max=float(sj.max()), frac_above=float((sj > 1.0).mean()))
    # Kinematic envelopes (in-ROI tracks)
    b["speed_max"] = entry(float(np.max(tf.speeds_moving)) + 0.25, "max(train reported speed, moving in-ROI tracks) + one 0.25 m/s step")
    b["speed_soft"] = entry(_q(tf.speeds_moving, hq), f"q{hq} of train reported speed (moving in-ROI tracks)")
    b["accel_hard"] = entry(_q(tf.accel, hq), f"q{hq} of |dv|/dt between consecutive cycles (all in-ROI tracks)")
    b["accel_soft"] = entry(_q(tf.accel, sq), f"q{sq} of |dv|/dt (all in-ROI tracks)")
    k, corr, nk = fit_rr_scale(tf)
    b["rr_scale"] = entry(round(k, 4), "least-squares k in range_rate = k * reported radial velocity, moving in-ROI windows with |v_r| >= 0.5",
                          corr=corr, n_windows=nk)
    res = np.abs(tf.rr_resid(k))
    b["rr_resid_hard"] = entry(_q(res, hq),
                               f"q{hq} of |range-rate(LSQ over {cfg['kinematic']['window_cycles']} cycles) - rr_scale * mean reported radial v| (in-ROI windows)")
    b["rr_resid_soft"] = entry(_q(res, sq), f"q{sq} of the same residual")
    b["hold_frac_moving"] = entry([round(float(np.quantile(tf.hold_frac, q)), 3) for q in (0.05, 0.5, 0.95)] if len(tf.hold_frac) else [],
                                  "p5/p50/p95 over moving in-ROI tracks of the fraction of cycles where x, y, vx, vy are all unchanged")
    b["pos_speed_hard"] = entry(_q(tf.pos_speed, hq), f"q{hq} of window displacement / time (in-ROI)")
    b["rcs_std_hard"] = entry(_q(tf.rcs_std, hq), f"q{hq} of rolling RCS std over {cfg['kinematic']['rcs_window_cycles']} cycles (in-ROI)")
    b["rcs_std_soft"] = entry(_q(tf.rcs_std, sq), f"q{sq} of rolling RCS std")
    mp = np.array(st.min_pair_dist)
    b["colocation_min"] = entry(_q(mp, 1 - hq), f"q{1 - hq:.5f} of per-cycle minimum distance between in-ROI objects",
                                frac_zero=float((mp == 0).mean()))
    # Birth statistics (soft feature)
    edges = np.arange(0, cfg["roi"]["max_range"] + 1.0, 1.0)
    h, _ = np.histogram(tf.birth_range, bins=edges)
    b["birth_range_hist"] = entry({"edges": edges.tolist(), "counts": h.tolist()}, "histogram of birth range of in-ROI tracks (train)")
    b["birth_speed_soft"] = entry(_q(tf.birth_speed, sq), f"q{sq} of speed at birth (in-ROI tracks)")
    b["birth_rate_in_roi"] = entry(float(np.mean(st.births_in_roi_per_cycle)), "mean track births per cycle in ROI (train)")
    b["moving_speed_samples"] = entry(np.round(np.quantile(tf.speeds_moving, np.linspace(0, 1, 101)), 3).tolist(),
                                      "101 quantiles of moving in-ROI reported speed (attacker sampling)")
    b["rcs_std_samples"] = entry(np.round(np.quantile(tf.rcs_std, np.linspace(0, 1, 101)), 3).tolist(),
                                 "101 quantiles of rolling RCS std (attacker sampling)")
    return b


def rcs_out_of_band(rng: float, rcs: float, table: dict) -> bool:
    edges, bands = table["edges"], table["bands"]
    i = min(max(int(np.searchsorted(edges, rng, side="left")) - 1, 0), len(bands) - 1)
    lo, hi, _ = bands[i]
    return rcs < lo or rcs > hi


def exceedance(st: SegmentStats, tf: TrackFeatures, b: dict) -> dict:
    """Fraction of held-out clean items that each threshold would flag."""
    v = lambda k: b[k]["value"]
    gaps = np.array(st.header_gaps)
    offs = np.array(st.offsets)
    bg = np.array(st.burst_gaps)
    rcs = np.array(st.rcs_all)
    out = {
        "cadence": float(((gaps < v("cadence_lo")) | (gaps > v("cadence_hi"))).mean()),
        "counter_step": float((np.array(st.counter_steps) != 1).mean()) if st.counter_steps else 0.0,
        "arrival": float(((offs < v("arrival_lo")) | (offs > v("arrival_hi"))).mean()),
        "burst_gap": float(((bg < v("burst_gap_lo")) | (bg > v("burst_gap_hi"))).mean()),
        "range_order": float((np.array(st.order_viol) > v("range_order_tol")).mean()),
        "slot_max": float((np.array(st.slots_all) > v("slot_max")).mean()),
        "rcs_range": float(((rcs < v("rcs_lo")) | (rcs > v("rcs_hi"))).mean()),
        "speed_max": float((tf.speeds_moving > v("speed_max")).mean()) if len(tf.speeds_moving) else 0.0,
        "accel_hard": float((tf.accel[np.isfinite(tf.accel)] > v("accel_hard")).mean()),
        "rr_resid_hard": float((np.abs(tf.rr_resid(v("rr_scale"))) > v("rr_resid_hard")).mean()),
        "pos_speed_hard": float((tf.pos_speed[np.isfinite(tf.pos_speed)] > v("pos_speed_hard")).mean()),
        "rcs_std_hard": float((tf.rcs_std > v("rcs_std_hard")).mean()),
        "colocation": float((np.array(st.min_pair_dist) < v("colocation_min")).mean()),
        "rcs_by_range": float(np.mean([rcs_out_of_band(r, c, v("rcs_by_range")) for r, c in st.rcs_range_roi])) if st.rcs_range_roi else 0.0,
    }
    return out


def learn(cfg: dict, train: list[Segment], val: list[Segment]) -> tuple[dict, SegmentStats, TrackFeatures]:
    """Thresholds from train, with the val exceedance of each recorded next to it."""
    st_tr = collect(cfg, train)
    tf_tr = track_features(st_tr, cfg)
    b = derive_thresholds(st_tr, tf_tr, cfg)
    st_va = collect(cfg, val)
    tf_va = track_features(st_va, cfg)
    exc = exceedance(st_va, tf_va, b)
    keymap = {"cadence": ["cadence_lo", "cadence_hi"], "arrival": ["arrival_lo", "arrival_hi"],
              "burst_gap": ["burst_gap_lo", "burst_gap_hi"], "range_order": ["range_order_tol"],
              "slot_max": ["slot_max"], "rcs_range": ["rcs_lo", "rcs_hi"], "speed_max": ["speed_max"],
              "accel_hard": ["accel_hard"], "rr_resid_hard": ["rr_resid_hard"], "pos_speed_hard": ["pos_speed_hard"],
              "rcs_std_hard": ["rcs_std_hard"], "colocation": ["colocation_min"], "counter_step": ["counter_step"],
              "rcs_by_range": ["rcs_by_range"]}
    for k, keys in keymap.items():
        for kk in keys:
            b[kk]["val_exceedance"] = exc[k]
    b["_meta"] = {"train_segments": [s.__dict__ for s in train], "val_segments": [s.__dict__ for s in val],
                  "note": "val_exceedance = fraction of held-out clean items (gaps, frames, windows) beyond the threshold; "
                          "detector-level false-positive rates are reported by scripts/run_attack_eval.py"}
    return b, st_tr, tf_tr
