"""Layer 1: protocol integrity. Per cycle; uses only this cycle and the previous header."""

from __future__ import annotations

from phantomguard.config import bval
from phantomguard.cycles import Cycle
from phantomguard.detect.common import ObjVerdict


class ProtocolChecker:
    def __init__(self, cfg: dict, baseline: dict):
        g = lambda k: bval(baseline, k)
        self.warmup = cfg["protocol"]["cadence_warmup_cycles"]
        self.cadence = (g("cadence_lo"), g("cadence_hi"))
        self.counter_step = g("counter_step")
        self.arrival = (g("arrival_lo"), g("arrival_hi"))
        self.first_arrival_hi = g("first_arrival_hi")
        self.burst = (g("burst_gap_lo"), g("burst_gap_hi"))
        self.order_tol = g("range_order_tol")
        self.slot_max = g("slot_max")
        ff = g("fixed_fields")
        self.ok_dyn, self.ok_res, self.ok_status = set(ff["dyn_prop"]), set(ff["reserved"]), set(ff["status"])
        self._prev_t: int | None = None
        self._prev_counter: int | None = None
        self._headers = 0

    def check(self, cycle: Cycle, verdicts: list[ObjVerdict]) -> list[str]:
        """Adds object-level reasons to ``verdicts`` (aligned with cycle.objects); returns cycle reasons."""
        cr: list[str] = []
        h = cycle.header
        if h is None:
            if cycle.objects or cycle.malformed_objects:
                cr.append("NO_HEADER")
        else:
            if h.count != cycle.n_received:
                cr.append("COUNT_MISMATCH")
            if h.status not in self.ok_status:
                cr.append("STATUS")
            if self._prev_counter is not None and (h.meas_counter - self._prev_counter) % 65536 != self.counter_step:
                cr.append("COUNTER")
            if self._prev_t is not None and self._headers >= self.warmup:
                gap = cycle.header_t - self._prev_t
                if not self.cadence[0] <= gap <= self.cadence[1]:
                    cr.append("CADENCE")
            self._prev_t, self._prev_counter = cycle.header_t, h.meas_counter
            self._headers += 1
        for o in cycle.other:
            cr.append(o.reason)
        if cycle.malformed_objects:
            cr.append("FRAME_LEN")
        # object level
        seen: dict[int, int] = {}
        prev_t = None
        prev_range = None
        for i, (ob, v) in enumerate(zip(cycle.objects, verdicts)):
            o = ob.obj
            if ob.offset is not None and not self.arrival[0] <= ob.offset <= self.arrival[1]:
                v.add("ARRIVAL")
            if i == 0:
                if ob.offset is not None and ob.offset > self.first_arrival_hi:
                    v.add("BURST_GAP")
            elif not self.burst[0] <= ob.t - prev_t <= self.burst[1]:
                v.add("BURST_GAP")
            prev_t = ob.t
            if prev_range is not None and prev_range - o.range > self.order_tol:
                v.add("RANGE_ORDER")
            prev_range = o.range
            if o.slot in seen:
                v.add("DUP_SLOT")
                verdicts[seen[o.slot]].add("DUP_SLOT")
            seen[o.slot] = i
            if o.slot > self.slot_max:
                v.add("SLOT_RANGE")
            if o.dyn_prop not in self.ok_dyn or o.reserved not in self.ok_res:
                v.add("FIXED_FIELD")
        return list(dict.fromkeys(cr))
