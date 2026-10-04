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
        if not 1 <= self.m <= self.n:
            raise ValueError("fusion requires 1 <= m <= n")
        self.layers = set(layers)
        self.hist: dict[int, deque] = {}
        self._last_cycle: int | None = None

    def active(self, codes: list[str]) -> list[str]:
        return [c for c in codes if layer_of(c) in self.layers]

    def apply(self, res: CycleResult, active_track_ids: set[int] | None = None) -> None:
        """Missing observations count as unflagged scan cycles, never as extra evidence.

        Pipeline passes active IDs so ended/reassigned tracks retire immediately. Standalone
        scoring retires a history after N unobserved cycles, when no old vote can survive.
        """
        skipped = max(0, res.index - self._last_cycle - 1) if self._last_cycle is not None else 0
        for history in self.hist.values():
            history.extend([False] * min(skipped, self.n))
        self._last_cycle = res.index
        cyc = self.active(res.cycle_reasons)
        res.cycle_alert = any(is_hard(c) for c in cyc)
        live = set()
        grouped: dict[int, list[tuple[object, bool]]] = {}
        for v in res.objects:
            codes = self.active(v.reasons)
            v.flagged = bool(codes)
            hard = any(is_hard(c) for c in codes)
            if v.track_id is None:
                v.alert = hard
                continue
            live.add(v.track_id)
            grouped.setdefault(v.track_id, []).append((v, hard))
        for tid, group in grouped.items():
            h = self.hist.setdefault(tid, deque(maxlen=self.n))
            # A track casts at most one vote per scan even if an external evaluation
            # record associates multiple duplicate-slot verdicts with that same ID.
            h.append(any(v.flagged for v, _ in group))
            persistent = sum(h) >= self.m
            for v, hard in group:
                v.alert = hard or persistent
        for tid in [t for t in self.hist if t not in live]:
            self.hist[tid].append(False)
            ended = active_track_ids is not None and tid not in active_track_ids
            expired = active_track_ids is None and len(self.hist[tid]) == self.n and not any(self.hist[tid])
            if ended or expired:
                del self.hist[tid]
