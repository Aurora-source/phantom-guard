"""Layer 1: protocol integrity. Per cycle; uses only this cycle and the previous header."""

from __future__ import annotations

from phantomguard.config import bval
from phantomguard.cycles import Cycle
from phantomguard.detect.common import ObjVerdict, contract_of, rule_thresholds_of
from phantomguard.detect.evidence import make_evidence, support_record


class ProtocolChecker:
    def __init__(self, cfg: dict, baseline: dict, *, capture_z: bool = False):
        g = lambda k: bval(baseline, k)
        self.capture_z = capture_z
        contract = contract_of(baseline)
        self.v2 = contract is not None
        self.off = set(contract.get("off", ())) if contract else set()
        self.c = rule_thresholds_of(baseline)
        self.arrival_pos = bval(baseline, "arrival_pos") if self.v2 and "arrival_pos" in baseline else None
        self.warmup = cfg["protocol"]["cadence_warmup_cycles"]
        self.cadence = (g("cadence_lo"), g("cadence_hi"))
        self.counter_step = g("counter_step")
        self.arrival = (g("arrival_lo"), g("arrival_hi"))
        self.first_arrival_hi = g("first_arrival_hi")
        self.burst = (g("burst_gap_lo"), g("burst_gap_hi"))
        self.order_tol = g("range_order_tol")
        self.slot_max = g("slot_max")
        m = cfg["protocol"].get("count_margin", 0)
        self.count_lo = max(0, g("objs_per_cycle_lo") - m)
        self.count_hi = g("objs_per_cycle_hi") + m
        ff = g("fixed_fields")
        self.ok_dyn, self.ok_res, self.ok_status = set(ff["dyn_prop"]), set(ff["reserved"]), set(ff["status"])
        self._prev_t: int | None = None
        self._prev_counter: int | None = None
        self._prev_header_frame: int | None = None
        self._prev_cycle_index: int | None = None
        self._headers = 0
        self.cycle_evidence: list[dict] = []  # evidence for the cycle reasons of the latest check()

    def check(self, cycle: Cycle, verdicts: list[ObjVerdict]) -> list[str]:
        """Adds object-level reasons to ``verdicts`` (aligned with cycle.objects); returns cycle reasons.

        Cycle-level evidence for the returned reasons is left in ``self.cycle_evidence``.
        """
        cr: list[str] = []
        ev: list[dict] = []
        self.cycle_evidence = ev
        h = cycle.header
        hdr = [cycle.header_frame_index] if cycle.header_frame_index is not None else []
        span = (cycle.index, cycle.index)

        def cyc_note(code, **fields):
            cr.append(code)
            ev.append(make_evidence(code, scope="cycle", cycles=span, frames=hdr, suspect_frames=hdr,
                                    suspect_basis="header_or_unknown_object", **fields))

        if h is None:
            if cycle.objects or cycle.malformed_objects:
                cyc_note("NO_HEADER", observed=cycle.n_received, note="objects arrived without a parsable header")
        else:
            if h.count != cycle.n_received:
                cyc_note("COUNT_MISMATCH", observed=cycle.n_received, expected=h.count,
                         note="header count differs from frames received; the responsible frame is not identifiable")
            if not self.count_lo <= cycle.n_received <= self.count_hi:
                cyc_note("COUNT_RANGE", observed=cycle.n_received, lo=self.count_lo, hi=self.count_hi,
                         support=support_record(None, "supported", rule="train min/max objects per cycle +- margin"))
            if h.status not in self.ok_status:
                cyc_note("STATUS", observed=h.status, note=f"status outside {sorted(self.ok_status)}")
            if self._prev_counter is not None and (h.meas_counter - self._prev_counter) % 65536 != self.counter_step:
                cyc_note("COUNTER", observed=(h.meas_counter - self._prev_counter) % 65536, expected=self.counter_step)
        if cycle.header_t is not None:
            # _headers counts preceding headers: gaps 1..warmup are skipped exactly.
            if self._prev_t is not None and self._headers > self.warmup:
                gap = cycle.header_t - self._prev_t
                if not self.cadence[0] <= gap <= self.cadence[1]:
                    cr.append("CADENCE")
                    prev = [self._prev_header_frame] if self._prev_header_frame is not None else []
                    first = self._prev_cycle_index if self._prev_cycle_index is not None else cycle.index
                    ev.append(make_evidence("CADENCE", scope="cycle", frames=prev + hdr, cycles=(first, cycle.index),
                                            observed=gap, lo=self.cadence[0], hi=self.cadence[1], suspect_frames=hdr,
                                            suspect_basis="header_or_unknown_object"))
            self._prev_t = cycle.header_t
            self._prev_header_frame = cycle.header_frame_index
            self._prev_cycle_index = cycle.index
            # A malformed header has an unknown counter, so the next counter cannot be
            # compared against it. The malformed header already raises its own hard reason.
            self._prev_counter = h.meas_counter if h is not None else None
            self._headers += 1
        for o in cycle.other:
            cr.append(o.reason)
            ev.append(make_evidence(o.reason, scope="cycle", frames=[o.frame_index], cycles=span,
                                    suspect_frames=[o.frame_index], observed=len(o.frame.data),
                                    note=f"CAN id 0x{o.frame.can_id:X}"))
        if cycle.malformed_objects:
            cr.append("FRAME_LEN")
            bad = [o.frame_index for o in cycle.malformed_objects]
            ev.append(make_evidence("FRAME_LEN", scope="cycle", frames=bad, cycles=span, suspect_frames=bad,
                                    observed=len(cycle.malformed_objects[0].data), expected=8))
        # object level
        by_frame = {v.frame_index: v for v in verdicts}
        seen: dict[int, ObjVerdict] = {}
        prev_t = None
        prev_range = None
        prev_frame = None
        observations = sorted(cycle.objects + cycle.malformed_objects, key=lambda ob: ob.position)
        for i, ob in enumerate(observations):
            v = by_frame[ob.frame_index]
            o = ob.obj
            fi = ob.frame_index
            if ob.offset is not None:
                z = max(ob.offset - self.arrival[1], self.arrival[0] - ob.offset)
                if self.capture_z:
                    v.scores["z:ARRIVAL"] = float(z)
                if "ARRIVAL" not in self.off and z > self.c.get("ARRIVAL", 0.0):
                    v.note("ARRIVAL", frames=[fi], cycles=span, observed=ob.offset, lo=self.arrival[0], hi=self.arrival[1],
                           suspect_frames=[fi])
                ap = self.arrival_pos
                if ap is not None and "ARRIVAL_POS" not in self.off:
                    expected = ap["a"] + ap["b"] * ob.position
                    zp = ob.offset - expected - ap["base_bound"]      # ticks later than the burst position predicts
                    if self.capture_z:
                        v.scores["z:ARRIVAL_POS"] = float(zp)
                    if zp > self.c.get("ARRIVAL_POS", 0.0):
                        v.note("ARRIVAL_POS", frames=[fi], cycles=span, observed=ob.offset, expected=expected,
                               hi=expected + ap["base_bound"] + self.c.get("ARRIVAL_POS", 0.0), normalized=zp,
                               suspect_frames=[fi],
                               support=support_record(ap["n"], "supported", quantisation_floor=ap["quantisation_floor_ticks"]),
                               note=f"arrived later than burst position {ob.position} predicts (late arrivals only)")
            if i == 0:
                if ob.offset is not None and ob.offset > self.first_arrival_hi:
                    v.note("BURST_GAP", frames=[fi], cycles=span, observed=ob.offset, hi=self.first_arrival_hi,
                           suspect_frames=[fi], note="first object arrived late after its header")
            elif not self.burst[0] <= ob.t - prev_t <= self.burst[1]:
                v.note("BURST_GAP", frames=[prev_frame, fi], cycles=span, observed=ob.t - prev_t, lo=self.burst[0],
                       hi=self.burst[1], suspect_frames=[fi],
                       note="gap to the previous object frame; that frame is the other candidate")
            prev_t = ob.t
            prev_frame = fi
            if o is not None and prev_range is not None and prev_range - o.range > self.order_tol:
                v.note("RANGE_ORDER", frames=[fi], cycles=span, observed=prev_range - o.range, hi=self.order_tol,
                       suspect_frames=[fi])
            prev_range = o.range if o is not None else None
            slot = o.slot if o is not None else (ob.data[0] if ob.data else None)
            if slot is None:
                continue
            if slot in seen:
                other = seen[slot]
                both = [other.frame_index, fi]
                for verdict in (v, other):
                    verdict.note("DUP_SLOT", frames=both, cycles=span, observed=slot, suspect_frames=both,
                                 suspect_basis="duplicate_slot_both")
            seen[slot] = v
            if self.capture_z:
                v.scores["z:SLOT_RANGE"] = float(slot - self.slot_max)
            if slot > self.slot_max + self.c.get("SLOT_RANGE", 0.0):
                v.note("SLOT_RANGE", frames=[fi], cycles=span, observed=slot, hi=self.slot_max, suspect_frames=[fi],
                       note="slot above the largest slot seen in clean training")
            if o is not None and (o.dyn_prop not in self.ok_dyn or o.reserved not in self.ok_res):
                v.note("FIXED_FIELD", frames=[fi], cycles=span, observed=o.dyn_prop, suspect_frames=[fi])
        return list(dict.fromkeys(cr))
