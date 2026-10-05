"""Layer 5: fusion and alerting.

A hard reason alerts immediately. Soft reasons flag the object-cycle; a track alerts when at least
M of its last N cycles are flagged (M/N calibrated on clean validation, see scripts/calibrate.py).
Cycle-level protocol reasons alert the cycle.

With ``fusion.learned_alone: false`` the learned layer is corroborating evidence: an object-cycle
whose only reasons are learned-layer reasons is not flagged. The reason code and score stay on the
verdict for display; they just do not count toward an alert on their own.

Policy extensions (all optional; absent keys reproduce the original behaviour exactly):

* ``fusion.hard_codes``  - the reason codes that alert immediately. Structural/format violations belong
  here; empirical tails of continuous distributions are routed through soft persistence instead.
* ``fusion.cycle_m/cycle_n`` - soft cycle-level reasons vote over cycles (M of the last N cycles).
* ``fusion.learned_route`` - ``{"mode": "independent"|"joint", "m", "n", "weight"}``. The learned layer
  is kept out of the plain physics vote and instead reaches a decision only through this route:
  ``independent``: LEARNED flagged in ``m`` of the last ``n`` cycles alerts on its own;
  ``joint``: each cycle contributes ``1`` if a physics reason flagged, else ``weight`` if LEARNED
  flagged, and the track alerts when the sum over the last N cycles reaches M. The joint sum is
  monotone: it can only add alerts relative to the physics-only rule. Learned and physics residuals
  are correlated, so a joint alert is not independent proof.
"""

from __future__ import annotations

from collections import deque

from phantomguard.detect.common import CycleResult, is_hard, layer_of
from phantomguard.detect.evidence import STATUS_PERSISTENT, make_evidence


class Fusion:
    def __init__(self, cfg: dict, layers: tuple[str, ...], *, emit_evidence: bool = False):
        f = cfg["fusion"]
        self.emit_evidence = emit_evidence
        self.votes: dict[int, deque] = {}  # track id -> (cycle, frame, codes) of flagged cycles (evidence only)
        self.m, self.n = f["m"], f["n"]
        if not 1 <= self.m <= self.n:
            raise ValueError("fusion requires 1 <= m <= n")
        self.learned_alone = f.get("learned_alone", True)
        self.layers = set(layers)
        self.hard_codes = frozenset(f["hard_codes"]) if f.get("hard_codes") is not None else None
        self.ignore = frozenset(f.get("ignore_codes", ()))   # evaluation-only rule ablation; never a deployed policy
        self.cycle_m, self.cycle_n = f.get("cycle_m"), f.get("cycle_n")
        if self.cycle_m is not None and not 1 <= self.cycle_m <= self.cycle_n:
            raise ValueError("fusion requires 1 <= cycle_m <= cycle_n")
        route = f.get("learned_route") or None
        if route is not None and route.get("mode") not in {"independent", "joint"}:
            raise ValueError("fusion.learned_route.mode must be 'independent' or 'joint'")
        if route is not None and route["mode"] == "joint" and not 0 < route.get("weight", 0) <= 1:
            raise ValueError("joint learned route needs 0 < weight <= 1")
        if route is not None and route["mode"] == "independent" and not 1 <= route["m"] <= route["n"]:
            raise ValueError("independent learned route requires 1 <= m <= n")
        self.route = route if (route is not None and "learned" in self.layers) else None
        self.hist: dict[int, deque] = {}
        self.route_hist: dict[int, deque] = {}
        self.cycle_hist: deque | None = deque(maxlen=self.cycle_n) if self.cycle_m is not None else None
        self._last_cycle: int | None = None

    def hard(self, code: str) -> bool:
        return is_hard(code) if self.hard_codes is None else code in self.hard_codes

    def active(self, codes: list[str]) -> list[str]:
        return [c for c in codes if layer_of(c) in self.layers and c not in self.ignore]

    def counted(self, codes: list[str]) -> list[str]:
        act = self.active(codes)
        keep_out = (not self.learned_alone) or self.route is not None
        if keep_out and act and all(layer_of(c) == "learned" for c in act):
            return []
        return act

    def _route_len(self) -> int:
        return self.route["n"] if self.route and self.route["mode"] == "independent" else self.n

    def apply(self, res: CycleResult, active_track_ids: set[int] | None = None) -> None:
        """Missing observations count as unflagged scan cycles, never as extra evidence.

        Pipeline passes active IDs so ended/reassigned tracks retire immediately. Standalone
        scoring retires a history after N unobserved cycles, when no old vote can survive.
        """
        skipped = max(0, res.index - self._last_cycle - 1) if self._last_cycle is not None else 0
        for history in self.hist.values():
            history.extend([False] * min(skipped, self.n))
        for history in self.route_hist.values():
            history.extend([0.0] * min(skipped, history.maxlen))
        if self.cycle_hist is not None and skipped:
            self.cycle_hist.extend([False] * min(skipped, self.cycle_n))
        self._last_cycle = res.index
        cyc = self.active(res.cycle_reasons)
        hard_cycle = any(self.hard(c) for c in cyc)
        if self.cycle_hist is not None:
            self.cycle_hist.append(any(not self.hard(c) for c in cyc))
            res.cycle_alert = hard_cycle or sum(self.cycle_hist) >= self.cycle_m
        else:
            res.cycle_alert = hard_cycle
        live = set()
        grouped: dict[int, list[tuple[object, bool]]] = {}
        for v in res.objects:
            codes = self.counted(v.reasons)
            v.flagged = bool(codes)
            hard = any(self.hard(c) for c in codes)
            if v.track_id is None:
                v.alert = hard
                continue
            live.add(v.track_id)
            grouped.setdefault(v.track_id, []).append((v, hard))
        for tid, group in grouped.items():
            h = self.hist.setdefault(tid, deque(maxlen=self.n))
            # A track casts at most one vote per scan even if an external evaluation
            # record associates multiple duplicate-slot verdicts with that same ID.
            physics = any(v.flagged for v, _ in group)
            h.append(physics)
            persistent = sum(h) >= self.m
            route_alert, route_note = False, None
            if self.route is not None:
                learned = any("LEARNED" in v.reasons for v, _ in group)
                rh = self.route_hist.setdefault(tid, deque(maxlen=self._route_len()))
                if self.route["mode"] == "joint":
                    rh.append(1.0 if physics else (self.route["weight"] if learned else 0.0))
                    route_alert = sum(rh) >= self.m - 1e-9
                    route_note = f"joint score {sum(rh):.2f} over the last {self.n} cycles (learned weight {self.route['weight']})"
                else:
                    rh.append(1.0 if learned else 0.0)
                    route_alert = sum(rh) >= self.route["m"]
                    route_note = f"learned flagged in {int(sum(rh))} of the last {self.route['n']} cycles"
            if self.emit_evidence:
                vv = self.votes.setdefault(tid, deque(maxlen=self.n))
                flagged = [v for v, _ in group if v.flagged]
                if flagged:
                    vv.append((res.index, flagged[0].frame_index, tuple(dict.fromkeys(c for v in flagged for c in v.reasons))))
                else:
                    vv.append(None)
            for v, hard in group:
                v.alert = hard or persistent or route_alert
                if self.emit_evidence and (persistent or route_alert) and not hard:
                    flagged_votes = [x for x in self.votes[tid] if x is not None]
                    note = (f"{len(flagged_votes)} of the last {self.n} cycles flagged: "
                            + ", ".join(sorted({c for x in flagged_votes for c in x[2]}))) if persistent else route_note
                    first, last = (flagged_votes[0][0], flagged_votes[-1][0]) if flagged_votes else (res.index, res.index)
                    v.evidence.append(make_evidence(
                        "PERSISTENCE", scope="track", status=STATUS_PERSISTENT, rule_class="fusion",
                        frames=[x[1] for x in flagged_votes] or [v.frame_index], cycles=(first, last),
                        observed=len(flagged_votes), expected=self.m, hi=self.n, suspect_frames=[v.frame_index],
                        suspect_track=tid, note=note))
        for tid in [t for t in self.hist if t not in live]:
            self.hist[tid].append(False)
            if tid in self.route_hist:
                self.route_hist[tid].append(0.0)
            if self.emit_evidence:
                self.votes.setdefault(tid, deque(maxlen=self.n)).append(None)
            ended = active_track_ids is not None and tid not in active_track_ids
            expired = active_track_ids is None and len(self.hist[tid]) == self.n and not any(self.hist[tid]) \
                and not any(self.route_hist.get(tid, ()))
            if ended or expired:
                del self.hist[tid]
                self.route_hist.pop(tid, None)
                self.votes.pop(tid, None)
