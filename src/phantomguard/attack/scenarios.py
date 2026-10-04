"""Synthetic frame-pattern generators (T1-T4) for detector evaluation.

This module produces the *content* of an alternative input stream: a set of fabricated radar
objects that resemble real ones to varying degrees (capability levels A0-A4). It does not touch the
bus or any live system; it is a test-fixture generator that runs entirely on the recorded CSVs, so
the detector can be measured on a stream that mixes real and fabricated frames. Which frames are
fabricated is recorded in a side labels file by ``injector.py`` and never reaches the detector.

Design rules mirror CLAUDE.md:
- Every fabricated value (slot, arrival offset, position, velocity, RCS) is sampled from
  distributions measured on real clean data (``pools.py`` / ``configs/baseline.json``), so a
  fabricated frame is distinguishable from a real one only by the property the test is about,
  never by a generation artefact.
- Levels are cumulative. A1 arrives in the real window with a free slot; A2 also keeps the header
  count / slot uniqueness valid; A3 adds smooth constant-velocity motion consistent with the
  reported velocity (via the learned ``rr_scale``) and stable RCS from the real marginal; A4 is
  built from real recorded tracks with range-conditional RCS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from phantomguard.attack.levels import LEVELS, Level
from phantomguard.attack.pools import Pools
from phantomguard.config import bval
from phantomguard.frames import FIELDS
from phantomguard.io.replay import RecordedCycle
from phantomguard.stats.baseline import P_R, P_RCS, P_VR, P_VX, P_VY, P_X, P_Y

# A per-cycle object plan: either explicit fields or a marker to emit random bytes (A0).
Fields = tuple[float, float, float, float, float]  # (x, y, vx, vy, rcs)


@dataclass
class ObjPlan:
    slot_pref: int | None            # preferred stable slot (A1+); None -> random each cycle (A0)
    replace: bool                    # T4 A2+: overwrite the real object carrying this slot, in place
    per_cycle: dict[int, Fields | None]  # cycle index -> fields, or None to emit random bytes (A0)


@dataclass
class Instance:
    attack_id: int
    atype: str                       # "T1".."T4"
    level: str                       # "A0".."A4"
    c0: int
    c1: int                          # last active cycle (inclusive)
    objects: list[ObjPlan] = field(default_factory=list)
    note: str = ""


@dataclass
class GenContext:
    cfg: dict
    baseline: dict
    level: Level
    rng: np.random.Generator
    known: Pools                     # recordings available to the generator (train by default)
    unseen: Pools | None             # recordings the detector never saw (for the strongest T3 copy)
    dt_cycle: float                  # nominal seconds per cycle
    rr_scale: float
    slot_max: int
    slot_p: np.ndarray               # sampling weights over slot ids (real usage)
    arrival: tuple[int, int]
    period: int
    roi: float
    az: tuple[float, float]
    rcs_grid: bool
    rcs_lo: float
    rcs_hi: float

    # ---- samplers (all from real data) ----
    def sample_offset_window(self) -> int:
        return int(self.rng.integers(self.arrival[0], self.arrival[1] + 1))

    def sample_offset_any(self) -> int:
        return int(self.rng.integers(2, max(3, self.period - 1)))

    def sample_slot(self) -> int:
        return int(self.rng.choice(len(self.slot_p), p=self.slot_p))

    def sample_rcs(self) -> float:
        r = float(self.known.rcs_roi[self.rng.integers(len(self.known.rcs_roi))])
        return round(r) if self.rcs_grid else r

    def sample_rcs_cond(self, rng_m: float) -> float:
        """RCS from real objects within ~1 range unit of rng_m (A4)."""
        sel = self.known.rcs_range_roi
        m = np.abs(sel[:, 0] - rng_m) <= 1.0
        pool = sel[m, 1] if m.sum() >= 20 else self.known.rcs_roi
        r = float(pool[self.rng.integers(len(pool))])
        return round(r) if self.rcs_grid else r

    def sample_pos_roi(self) -> tuple[float, float]:
        return tuple(self.known.pos_roi[self.rng.integers(len(self.known.pos_roi))])

    def sample_pos_moving(self) -> tuple[float, float]:
        return tuple(self.known.pos_moving[self.rng.integers(len(self.known.pos_moving))])

    def sample_velocity(self) -> tuple[float, float]:
        return tuple(self.known.vel_moving[self.rng.integers(len(self.known.vel_moving))])

    def in_scene(self, x: float, y: float, margin: float = 0.3) -> bool:
        if x <= 0.2 or math.hypot(x, y) > self.roi - margin:
            return False
        a = math.degrees(math.atan2(y, x))
        return self.az[0] <= a <= self.az[1]


def make_context(cfg: dict, baseline: dict, level_name: str, rng: np.random.Generator, known: Pools,
                 unseen: Pools | None) -> GenContext:
    use = np.asarray(bval(baseline, "slot_use_hist"), dtype=float)
    use = use / use.sum()
    ff = bval(baseline, "fixed_fields")
    return GenContext(
        cfg=cfg, baseline=baseline, level=LEVELS[level_name], rng=rng, known=known, unseen=unseen,
        dt_cycle=cfg["units"]["tick_seconds"] * 332, rr_scale=bval(baseline, "rr_scale"),
        slot_max=bval(baseline, "slot_max"), slot_p=use,
        arrival=(bval(baseline, "arrival_lo"), bval(baseline, "arrival_hi")), period=332,
        roi=cfg["roi"]["max_range"], az=tuple(known.azimuth_range),
        rcs_grid=bool(bval(baseline, "rcs_integer")), rcs_lo=bval(baseline, "rcs_lo"), rcs_hi=bval(baseline, "rcs_hi"))


def _q(field_name: str, v: float) -> float:
    return FIELDS[field_name].quantise(v)


def _clip_rcs(ctx: GenContext, r: float) -> float:
    return float(min(max(r, ctx.rcs_lo), ctx.rcs_hi))


# --------------------------------------------------------------------------- trajectories


def _moving_trajectory(ctx: GenContext, c0: int, life: int) -> dict[int, Fields]:
    """A smooth constant-velocity path (A3+): position advances at rr_scale * reported velocity."""
    x, y = ctx.sample_pos_moving()
    vx, vy = ctx.sample_velocity()
    # one stable RCS for the whole track (real moving tracks hold RCS to ~0.3 dB std)
    rcs = ctx.sample_rcs_cond(math.hypot(x, y)) if ctx.level.data_aware else ctx.sample_rcs()
    rcs = _clip_rcs(ctx, rcs)
    out: dict[int, Fields] = {}
    for k in range(life):
        c = c0 + k
        nx = x + ctx.rr_scale * vx * ctx.dt_cycle * k
        ny = y + ctx.rr_scale * vy * ctx.dt_cycle * k
        if not ctx.in_scene(nx, ny):
            break
        out[c] = (_q("x", nx), _q("y", ny), _q("vx", vx), _q("vy", vy), rcs)
    return out


def _static_trajectory(ctx: GenContext, c0: int, life: int) -> dict[int, Fields]:
    x, y = ctx.sample_pos_roi()
    rcs = ctx.sample_rcs_cond(math.hypot(x, y)) if ctx.level.data_aware else ctx.sample_rcs()
    f = (_q("x", x), _q("y", y), 0.0, 0.0, _clip_rcs(ctx, rcs))
    return {c0 + k: f for k in range(life)}


def _naive_trajectory(ctx: GenContext, c0: int, life: int, jump: bool) -> dict[int, Fields | None]:
    """A1/A2 kinematics: positions in ROI but jumping; velocity unrelated to motion.

    A0 (no level fields set) emits None -> random raw bytes, handled by the injector.
    """
    out: dict[int, Fields | None] = {}
    if not ctx.level.timing_in_window and not ctx.level.physics:  # A0: fully random bytes
        return {c0 + k: None for k in range(life)}
    x, y = ctx.sample_pos_roi()
    for k in range(life):
        if jump or k == 0:
            x, y = ctx.sample_pos_roi()
        vx = float(ctx.rng.uniform(-6, 6))
        vy = float(ctx.rng.uniform(-4, 4))
        rcs = ctx.sample_rcs()
        out[c0 + k] = (_q("x", x), _q("y", y), _q("vx", vx), _q("vy", vy), _clip_rcs(ctx, rcs))
    return out


def _object_trajectory(ctx: GenContext, c0: int, life: int, moving: bool) -> dict[int, Fields | None]:
    if ctx.level.physics:                       # A3/A4
        return _moving_trajectory(ctx, c0, life) if moving else _static_trajectory(ctx, c0, life)
    return _naive_trajectory(ctx, c0, life, jump=True)  # A0/A1/A2


# --------------------------------------------------------------------------- scenario planners


def _pick_real_segment(ctx: GenContext, pool: Pools, min_len: int, max_len: int) -> np.ndarray | None:
    moving = [t for t in pool.moving_tracks if len(t) >= min_len]
    if not moving:
        return None
    t = moving[ctx.rng.integers(len(moving))]
    n = int(ctx.rng.integers(min_len, min(max_len, len(t)) + 1))
    s = int(ctx.rng.integers(0, len(t) - n + 1))
    return t[s:s + n]


def _segment_to_fields(ctx: GenContext, seg: np.ndarray, c0: int, dx: float, dy: float) -> dict[int, Fields]:
    out: dict[int, Fields] = {}
    for k in range(len(seg)):
        p = seg[k]
        x, y = p[P_X] + dx, p[P_Y] + dy
        if not ctx.in_scene(x, y):
            break
        out[c0 + k] = (_q("x", x), _q("y", y), _q("vx", p[P_VX]), _q("vy", p[P_VY]), _clip_rcs(ctx, float(p[P_RCS])))
    return out


def plan_instance(ctx: GenContext, atype: str, attack_id: int, c0: int, base_cycles, lo: int) -> Instance | None:
    """Build one instance of scenario ``atype`` starting at global cycle index ``c0``."""
    ac = ctx.cfg["attack"]
    L = ctx.level
    if atype == "T1":
        n = int(ctx.rng.integers(ac["T1"]["count"][0], ac["T1"]["count"][1] + 1))
        life = int(ctx.rng.integers(ac["T1"]["life"][0], ac["T1"]["life"][1] + 1))
        moving = bool(ctx.rng.random() < 0.5)
        objs = []
        for _ in range(n):
            traj = _object_trajectory(ctx, c0, life, moving)
            if traj:
                objs.append(ObjPlan(_pref_slot(ctx), False, traj))
        return Instance(attack_id, atype, L.name, c0, c0 + life - 1, objs, f"{'moving' if moving else 'static'} x{n}") if objs else None
    if atype == "T2":
        n = int(ctx.rng.integers(ac["T2"]["count"][0], ac["T2"]["count"][1] + 1))
        life = int(ctx.rng.integers(ac["T2"]["life"][0], ac["T2"]["life"][1] + 1))
        objs = []
        for _ in range(n):
            start = c0 + int(ctx.rng.integers(0, max(1, life // 2)))
            ln = int(ctx.rng.integers(max(2, life // 4), life + 1))
            traj = _object_trajectory(ctx, start, ln, moving=bool(ctx.rng.random() < 0.3))
            if traj:
                objs.append(ObjPlan(_pref_slot(ctx), False, traj))
        return Instance(attack_id, atype, L.name, c0, c0 + life - 1, objs, f"flood x{n}") if objs else None
    if atype == "T3":
        pool = ctx.unseen if (L.data_aware and ctx.unseen is not None) else ctx.known
        seg = _pick_real_segment(ctx, pool, ac["T3"]["min_len"], ac["T3"]["max_len"])
        if seg is None:
            return None
        translate = bool(ctx.rng.random() < 0.5)
        dx = dy = 0.0
        if translate:  # shift while keeping the copy inside the scene
            for _ in range(20):
                dx, dy = float(ctx.rng.uniform(-4, 4)), float(ctx.rng.uniform(-4, 4))
                if ctx.in_scene(seg[0, P_X] + dx, seg[0, P_Y] + dy) and ctx.in_scene(seg[-1, P_X] + dx, seg[-1, P_Y] + dy):
                    break
            else:
                dx = dy = 0.0
        traj = _segment_to_fields(ctx, seg, c0, dx, dy)
        if not traj:
            return None
        return Instance(attack_id, atype, L.name, c0, max(traj), [ObjPlan(_pref_slot(ctx), False, traj)],
                        f"replay {'translated' if (dx or dy) else 'exact'} len{len(seg)}")
    if atype == "T4":
        picked = _pick_live_moving_track(base_cycles, lo, c0, ctx.cfg)
        if picked is None:
            return None
        slot, start_c, seg = picked
        life = min(len(seg), int(ctx.rng.integers(ac["T4"]["life"][0], ac["T4"]["life"][1] + 1)))
        drift = float(ctx.rng.uniform(ac["T4"]["drift_per_cycle"][0], ac["T4"]["drift_per_cycle"][1]))
        ang = float(ctx.rng.uniform(0, 2 * math.pi))
        traj: dict[int, Fields] = {}
        for k in range(life):
            x, y, vx, vy, rcs = seg[k]
            nx, ny = x + drift * k * math.cos(ang), y + drift * k * math.sin(ang)
            if not ctx.in_scene(nx, ny):
                break
            traj[start_c + k] = (_q("x", nx), _q("y", ny), _q("vx", vx), _q("vy", vy), _clip_rcs(ctx, rcs))
        if not traj:
            return None
        replace = L.fix_header  # A2+: overwrite the real frame in place; A0/A1: add alongside (dup slot)
        return Instance(attack_id, atype, L.name, min(traj), max(traj),
                        [ObjPlan(slot, replace, traj)], f"drift {drift:.3f}/cyc slot0x{slot:02x}")
    raise ValueError(atype)


def _pref_slot(ctx: GenContext) -> int | None:
    if not ctx.level.free_slot:          # A0: random slot resolved per cycle in the injector
        return None
    return ctx.sample_slot()


def _pick_live_moving_track(base_cycles, lo: int, c0: int, cfg: dict):
    """Find a real moving slot in the base stream near cycle c0 (for T4 shift/overwrite).

    Returns (slot, start_cycle_index, seg) where seg is a list of (x,y,vx,vy,rcs) read from the real
    frames, so the drift is applied to a genuine recorded trajectory.
    """
    from phantomguard.frames import decode_object

    thr = cfg["motion"]["moving_threshold_mps"]
    roi = cfg["roi"]["max_range"]
    # collect slot -> list[(cycle_index, fields)] over a window after c0
    window = range(c0, min(len(base_cycles), c0 + 200))
    tracks: dict[int, list] = {}
    prev_seen: dict[int, int] = {}
    for ci in window:
        rc: RecordedCycle = base_cycles[ci]
        for ts, raw in rc.objects:
            if len(raw) != 8:
                continue
            o = decode_object(raw)
            if o.range > roi:
                continue
            key = o.slot
            if key in tracks and ci - prev_seen[key] > 2:
                continue  # broken; keep the first contiguous run
            tracks.setdefault(key, []).append((ci, (o.x, o.y, o.vx, o.vy, o.rcs), o.speed))
            prev_seen[key] = ci
    for slot, pts in tracks.items():
        moving = sum(1 for _, _, s in pts if s >= thr)
        if moving >= 8 and len(pts) >= 12:
            start_c = pts[0][0]
            seg = [f for _, f, _ in pts]
            return slot, start_c, seg
    return None


def plan_run(ctx: GenContext, atype: str, base_cycles, lo: int, hi: int) -> list[Instance]:
    """Schedule several spaced instances of one scenario across cycle window [lo, hi)."""
    ac = ctx.cfg["attack"]
    gap = ac["min_gap_cycles"]
    n = ac["instances_per_run"]
    span = hi - lo
    instances: list[Instance] = []
    if span < gap + 60:
        return instances
    step = span // (n + 1)
    aid = 0
    for i in range(n):
        c0 = lo + step * (i + 1) + int(ctx.rng.integers(-gap // 2, gap // 2 + 1))
        c0 = max(lo + gap, min(c0, hi - gap))
        inst = plan_instance(ctx, atype, aid, c0, base_cycles, lo)
        if inst is not None:
            instances.append(inst)
            aid += 1
    return instances
