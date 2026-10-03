"""Layer 5: fusion and alerting.

A hard reason alerts immediately. Soft reasons flag the object-cycle; a track alerts when at least
M of its last N cycles are flagged (default 3 of 5). Cycle-level protocol reasons alert the cycle.
"""

from __future__ import annotations

from collections import deque

from phantomguard.detect.common import CycleResult, is_hard, layer_of


class Fusion:
    def __init__(self, cfg: dict, layers: tuple[str, ...]):
        self.m, self.n = cfg["fusion"]["m"], cfg["fusion"]["n"]
        self.layers = set(layers)
        self.hist: dict[int, deque] = {}

    def active(self, codes: list[str]) -> list[str]:
        return [c for c in codes if layer_of(c) in self.layers]

    def apply(self, res: CycleResult) -> None:
        cyc = self.active(res.cycle_reasons)
        res.cycle_alert = any(is_hard(c) for c in cyc)
        live = set()
        for v in res.objects:
            codes = self.active(v.reasons)
            v.flagged = bool(codes)
            hard = any(is_hard(c) for c in codes)
            if v.track_id is None:
                v.alert = hard
                continue
            live.add(v.track_id)
            h = self.hist.setdefault(v.track_id, deque(maxlen=self.n))
            h.append(v.flagged)
            v.alert = hard or sum(h) >= self.m
        for tid in [t for t in self.hist if t not in live]:  # forget tracks that ended
            if len(self.hist) > 4096:
                del self.hist[tid]
