"""Per-slot track linking. The slot number is used only as a link key, never as a feature."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from phantomguard.cycles import Cycle, ObjObs


@dataclass
class TrackPoint:
    cycle_index: int
    t_s: float  # cycle time in seconds (header time, else arrival time)
    x: float
    y: float
    vx: float
    vy: float
    rcs: float
    rng: float
    vr: float  # reported radial velocity
    frame_index: int
    # Running integrals over the track's own past (observed dt), so window sums are O(1) differences:
    # civx/civy = sum_{j<i} v_j dt_j, cspd = sum_{j<i} |v_j|. Used by the drift evidence (profile v2).
    civx: float = 0.0
    civy: float = 0.0
    cspd: float = 0.0


@dataclass
class Track:
    track_id: int
    slot: int
    born_cycle: int
    born_by_jump: bool  # slot was reused with a position jump (reassignment)
    points: deque = field(default_factory=deque)
    total_points: int = 0
    moving_points: int = 0
    # How the latest observation was associated (diagnostic and evidence only, never a feature):
    # born, born_by_jump, duplicate_slot, continued, gap_bridged, ambiguous_continued, ambiguous_reset.
    assoc: str = "born"
    predecessor: int | None = None   # track this one replaced after a reset (evidence lineage only)
    gate_distance: float | None = None

    @property
    def last(self) -> TrackPoint:
        return self.points[-1]

    @property
    def age(self) -> int:
        return self.total_points


class TrackManager:
    """Links objects to tracks by slot across consecutive cycles.

    A slot continues its track if it was seen within the last ``max_gap_cycles + 1`` cycles (the
    sensor often drops a slot for one cycle) and its position moved by at most ``reassign_jump``; otherwise a new track is born (``born_by_jump`` when the slot was
    present but jumped).

    ``predictive_gate`` (config ``tracks.predictive_gate``, default off) measures the jump against the
    nearer of the last position and the position predicted from the last reported velocity over the
    elapsed time. It is conservative: it can only keep a link the plain gate would cut (a fast mover
    across a dropped cycle), never cut one it keeps. On the four recordings it changes 4 of ~433k links,
    so it is off by default; training, attacker pools and the detector must use the same setting.
    Links within ``ambiguity`` (fractions of the jump threshold) of the gate are marked ambiguous so a
    reset or continuation that could have gone the other way is visible in evidence.
    """

    def __init__(self, reassign_jump: float, tick_seconds: float, moving_threshold: float, history: int = 64,
                 max_gap_cycles: int = 1, *, predictive_gate: bool = False, ambiguity: tuple[float, float] = (0.7, 1.3)):
        self.predictive_gate = predictive_gate
        self.ambiguity = ambiguity
        self.reassign_jump = reassign_jump
        self.max_gap_cycles = max_gap_cycles
        self.tick_seconds = tick_seconds
        self.moving_threshold = moving_threshold
        self.history = history
        self.active: dict[int, Track] = {}
        self._last_seen: dict[int, int] = {}  # slot -> cycle index of its track's last point
        self._next_id = 0

    def update(self, cycle: Cycle) -> list[tuple[ObjObs, Track]]:
        out: list[tuple[ObjObs, Track]] = []
        # Tracks whose slot was seen within the last max_gap_cycles+1 cycles can continue.
        prev = {s: tr for s, tr in self.active.items()
                if cycle.index - self._last_seen[s] <= self.max_gap_cycles + 1}
        new_active: dict[int, Track] = {}
        t_cycle = cycle.header_t
        for ob in cycle.objects:
            o = ob.obj
            slot = o.slot
            t = (t_cycle if t_cycle is not None else ob.t) * self.tick_seconds
            if slot in new_active:  # duplicate slot in this cycle: give it its own track
                tr = self._new(slot, cycle.index, False)
                tr.assoc = "duplicate_slot"
            else:
                tr = prev.get(slot)
                if tr is not None:
                    d = self._gate_distance(tr.last, o, t)
                    lo, hi = self.ambiguity
                    if d > self.reassign_jump:
                        old = tr
                        tr = self._new(slot, cycle.index, True)
                        tr.predecessor = old.track_id
                        tr.assoc = "ambiguous_reset" if d <= hi * self.reassign_jump else "born_by_jump"
                    else:
                        tr.assoc = ("ambiguous_continued" if d > lo * self.reassign_jump else
                                    "gap_bridged" if cycle.index - tr.last.cycle_index > 1 else "continued")
                    tr.gate_distance = d
                else:
                    tr = self._new(slot, cycle.index, False)
                new_active[slot] = tr
            civx = civy = cspd = 0.0
            if tr.points:
                q = tr.points[-1]
                civx, civy = q.civx + q.vx * (t - q.t_s), q.civy + q.vy * (t - q.t_s)
                cspd = q.cspd + math.hypot(q.vx, q.vy)
            tr.points.append(TrackPoint(cycle.index, t, o.x, o.y, o.vx, o.vy, o.rcs, o.range, o.radial_velocity,
                                        ob.frame_index, civx, civy, cspd))
            if len(tr.points) > self.history:
                tr.points.popleft()
            tr.total_points += 1
            tr.moving_points += o.speed >= self.moving_threshold
            out.append((ob, tr))
        for s, tr in prev.items():  # keep briefly-missing tracks for gap bridging
            if s not in new_active:
                new_active[s] = tr
        for ob in cycle.objects:
            self._last_seen[ob.obj.slot] = cycle.index
        self.active = new_active
        return out

    def _gate_distance(self, q: TrackPoint, o, t: float) -> float:
        d = math.hypot(o.x - q.x, o.y - q.y)
        if self.predictive_gate and t > q.t_s:
            dt = t - q.t_s
            d = min(d, math.hypot(o.x - (q.x + q.vx * dt), o.y - (q.y + q.vy * dt)))
        return d

    def _new(self, slot: int, cycle_index: int, by_jump: bool) -> Track:
        tr = Track(self._next_id, slot, cycle_index, by_jump)
        self._next_id += 1
        return tr
