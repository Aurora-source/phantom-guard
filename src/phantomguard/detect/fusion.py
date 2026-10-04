"""Layer 5: fusion and alerting.

A hard reason alerts immediately. Soft reasons flag the object-cycle; a track alerts when at least
M of its last N cycles are flagged (M/N calibrated on clean validation, see scripts/calibrate.py).
Cycle-level protocol reasons alert the cycle.

With ``fusion.learned_alone: false`` the learned layer is corroborating evidence: an object-cycle
whose only reasons are learned-layer reasons is not flagged. The reason code and score stay on the
verdict for display; they just do not count toward an alert on their own.
"""

from __future__ import annotations

from collections import deque

from phantomguard.detect.common import CycleResult, is_hard, layer_of


class Fusion:
    def __init__(self, cfg: dict, layers: tuple[str, ...]):
        self.m, self.n = cfg["fusion"]["m"], cfg["fusion"]["n"]
        self.learned_alone = cfg["fusion"].get("learned_alone", True)
        self.layers = set(layers)
        self.hist: dict[int, deque] = {}

    def active(self, codes: list[str]) -> list[str]:
        return [c for c in codes if layer_of(c) in self.layers]

    def counted(self, codes: list[str]) -> list[str]:
        """Active codes that count toward flagging (learned-only object-cycles drop out if corroborating)."""
        act = self.active(codes)
        if not self.learned_alone and act and all(layer_of(c) == "learned" for c in act):
            return []
        return act

    def apply(self, res: CycleResult) -> None:
        cyc = self.active(res.cycle_reasons)
        res.cycle_alert = any(is_hard(c) for c in cyc)
        live = set()
        for v in res.objects:
            codes = self.counted(v.reasons)
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
