"""Clean-data metrics: false positives per object-cycle and persistent alerts per minute.

Fusion is re-applied offline to stored reason codes so every layer subset (ablation) is scored
from one detector run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from phantomguard.detect.common import CycleResult, ObjVerdict
from phantomguard.detect.fusion import Fusion


@dataclass
class LiteVerdict:
    track_id: int | None
    in_roi: bool
    moving: bool
    reasons: list[str]
    flagged: bool = False
    alert: bool = False


@dataclass
class LiteCycle:
    header_t: int | None
    cycle_reasons: list[str]
    objects: list[LiteVerdict]
    cycle_alert: bool = False
    latency_ms: float = 0.0


def lite(res: CycleResult) -> LiteCycle:
    return LiteCycle(res.header_t, list(res.cycle_reasons),
                     [LiteVerdict(v.track_id, v.in_roi, v.moving, list(v.reasons)) for v in res.objects],
                     latency_ms=res.latency_ms)


@dataclass
class CleanMetrics:
    cycles: int = 0
    minutes: float = 0.0
    obj_cycles: int = 0
    roi_obj_cycles: int = 0
    roi_moving_obj_cycles: int = 0
    flagged: int = 0
    flagged_roi_moving: int = 0
    flagged_roi_static: int = 0
    alerting: int = 0
    track_alert_events: int = 0
    cycle_alert_events: int = 0
    reasons: Counter = field(default_factory=Counter)
    cycle_reasons: Counter = field(default_factory=Counter)

    @property
    def alert_events(self) -> int:
        return self.track_alert_events + self.cycle_alert_events

    @property
    def alerts_per_minute(self) -> float:
        return self.alert_events / self.minutes if self.minutes else float("nan")

    def row(self) -> dict:
        roi_static = self.roi_obj_cycles - self.roi_moving_obj_cycles
        return {
            "cycles": self.cycles, "minutes": round(self.minutes, 3), "object_cycles": self.obj_cycles,
            "fp_rate_flagged": self.flagged / self.obj_cycles if self.obj_cycles else 0.0,
            "fp_rate_flagged_roi_static": self.flagged_roi_static / roi_static if roi_static else 0.0,
            "fp_rate_flagged_roi_moving": (self.flagged_roi_moving / self.roi_moving_obj_cycles
                                           if self.roi_moving_obj_cycles else 0.0),
            "fp_rate_alerting": self.alerting / self.obj_cycles if self.obj_cycles else 0.0,
            "alert_events": self.alert_events, "alerts_per_minute": self.alerts_per_minute,
        }


def score(cycles: list[LiteCycle], cfg: dict, layers: tuple[str, ...]) -> CleanMetrics:
    fus = Fusion(cfg, layers)
    m = CleanMetrics()
    tick = cfg["units"]["tick_seconds"]
    ts = [c.header_t for c in cycles if c.header_t is not None]
    m.minutes = (ts[-1] - ts[0]) * tick / 60.0 if len(ts) > 1 else 0.0
    alerting_tracks: set = set()
    prev_cycle_alert = False
    for c in cycles:
        fus.apply(c)  # duck-typed: LiteCycle has the fields Fusion uses
        m.cycles += 1
        act = fus.active(c.cycle_reasons)
        m.cycle_reasons.update(act)
        if c.cycle_alert and not prev_cycle_alert:
            m.cycle_alert_events += 1
        prev_cycle_alert = c.cycle_alert
        now_alerting = set()
        for v in c.objects:
            m.obj_cycles += 1
            m.reasons.update(fus.active(v.reasons))
            if v.in_roi:
                m.roi_obj_cycles += 1
                m.roi_moving_obj_cycles += v.moving
            if v.flagged:
                m.flagged += 1
                if v.in_roi:
                    if v.moving:
                        m.flagged_roi_moving += 1
                    else:
                        m.flagged_roi_static += 1
            if v.alert:
                m.alerting += 1
                key = v.track_id
                now_alerting.add(key)
                if key not in alerting_tracks:
                    m.track_alert_events += 1
        alerting_tracks = now_alerting
    return m


def latency_stats(cycles: list[LiteCycle]) -> dict:
    lat = np.array([c.latency_ms for c in cycles])
    return {"p50_ms": float(np.percentile(lat, 50)), "p99_ms": float(np.percentile(lat, 99)),
            "max_ms": float(lat.max()), "n": int(len(lat))}
