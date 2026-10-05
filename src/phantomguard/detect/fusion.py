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
from phantomguard.detect.evidence import STATUS_PERSISTENT, make_evidence


class Fusion:
    def __init__(self, cfg: dict, layers: tuple[str, ...], *, emit_evidence: bool = False):
        self.emit_evidence = emit_evidence
        self.votes: dict[int, deque] = {}  # track id -> (cycle, frame, codes) of flagged cycles (evidence only)
        self.m, self.n = cfg["fusion"]["m"], cfg["fusion"]["n"]
        if not 1 <= self.m <= self.n:
            raise ValueError("fusion requires 1 <= m <= n")
        self.learned_alone = cfg["fusion"].get("learned_alone", True)
        self.layers = set(layers)
        self.hist: dict[int, deque] = {}
        self._last_cycle: int | None = None

    def active(self, codes: list[str]) -> list[str]:
        return [c for c in codes if layer_of(c) in self.layers]

    def counted(self, codes: list[str]) -> list[str]:
        act = self.active(codes)
        if not self.learned_alone and act and all(layer_of(c) == "learned" for c in act):
            return []
        return act

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
            codes = self.counted(v.reasons)
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
            if self.emit_evidence:
                vv = self.votes.setdefault(tid, deque(maxlen=self.n))
                flagged = [v for v, _ in group if v.flagged]
                if flagged:
                    vv.append((res.index, flagged[0].frame_index, tuple(dict.fromkeys(c for v in flagged for c in v.reasons))))
                else:
                    vv.append(None)
            for v, hard in group:
                v.alert = hard or persistent
                if self.emit_evidence and persistent and not hard:
                    flagged_votes = [x for x in self.votes[tid] if x is not None]
                    v.evidence.append(make_evidence(
                        "PERSISTENCE", scope="track", status=STATUS_PERSISTENT, rule_class="fusion",
                        frames=[x[1] for x in flagged_votes], cycles=(flagged_votes[0][0], flagged_votes[-1][0]),
                        observed=len(flagged_votes), expected=self.m, hi=self.n, suspect_frames=[v.frame_index],
                        suspect_track=tid, note=f"{len(flagged_votes)} of the last {self.n} cycles flagged: "
                        + ", ".join(sorted({c for x in flagged_votes for c in x[2]}))))
        for tid in [t for t in self.hist if t not in live]:
            self.hist[tid].append(False)
            if self.emit_evidence:
                self.votes.setdefault(tid, deque(maxlen=self.n)).append(None)
            ended = active_track_ids is not None and tid not in active_track_ids
            expired = active_track_ids is None and len(self.hist[tid]) == self.n and not any(self.hist[tid])
            if ended or expired:
                del self.hist[tid]
                self.votes.pop(tid, None)
