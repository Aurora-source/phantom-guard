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


@dataclass
class Track:
    track_id: int
    slot: int
    born_cycle: int
    born_by_jump: bool  # slot was reused with a position jump (reassignment)
    points: deque = field(default_factory=deque)
    total_points: int = 0
    moving_points: int = 0

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
    """

    def __init__(self, reassign_jump: float, tick_seconds: float, moving_threshold: float, history: int = 64,
                 max_gap_cycles: int = 1):
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
            if slot in new_active:  # duplicate slot in this cycle: give it its own track
                tr = self._new(slot, cycle.index, False)
            else:
                tr = prev.get(slot)
                if tr is not None and math.hypot(o.x - tr.last.x, o.y - tr.last.y) > self.reassign_jump:
                    tr = self._new(slot, cycle.index, True)
                elif tr is None:
                    tr = self._new(slot, cycle.index, False)
                new_active[slot] = tr
            t = (t_cycle if t_cycle is not None else ob.t) * self.tick_seconds
            tr.points.append(TrackPoint(cycle.index, t, o.x, o.y, o.vx, o.vy, o.rcs, o.range, o.radial_velocity,
                                        ob.frame_index))
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

    def _new(self, slot: int, cycle_index: int, by_jump: bool) -> Track:
        tr = Track(self._next_id, slot, cycle_index, by_jump)
        self._next_id += 1
        return tr
