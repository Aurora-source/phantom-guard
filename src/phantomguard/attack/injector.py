"""Assemble a mixed frame stream (real + fabricated) and record which frames are fabricated.

``MixedSource`` is a ``FrameSource``: it replays a recorded CSV and, at the cycles chosen by the
planned ``Instance`` list, inserts fabricated 0x60B object frames (and, for A2+, rewrites the 0x60A
header count so the cycle stays internally consistent). It assigns every emitted frame an index in
arrival order and records, for each fabricated object frame, a label row
``(frame_index, is_attack, attack_id, attack_type, level)``. The labels are written to a side file
and kept in memory; they never enter the detector (CLAUDE.md hard rule 2).

Placement realism follows the capability level (see scenarios.py): A0 may land outside the arrival
window; A1/A2 land inside it with a free/unique slot; A3+ are inserted at the range-sorted position
and the whole cycle burst is re-spaced back-to-back, matching the sensor's observed transmit order.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from phantomguard.attack.scenarios import GenContext, Instance
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header, decode_object, encode_object
from phantomguard.io.replay import RecordedCycle, ReplaySource

LABEL_FIELDS = ["frame_index", "is_attack", "attack_id", "attack_type", "level"]


@dataclass
class Label:
    frame_index: int
    is_attack: int
    attack_id: int
    attack_type: str
    level: str


class MixedSource:
    """FrameSource over a recorded file with fabricated frames mixed in at planned cycles."""

    def __init__(self, base: ReplaySource, ctx: GenContext, instances: list[Instance], tag: str = ""):
        self.base = base
        self.ctx = ctx
        self.name = f"{base.name}+{tag}" if tag else base.name
        self.labels: list[Label] = []
        # index instances' objects by the cycles they are active in
        self._by_cycle: dict[int, list[tuple[Instance, int]]] = {}
        for inst in instances:
            for oi, obj in enumerate(inst.objects):
                for c in obj.per_cycle:
                    self._by_cycle.setdefault(c, []).append((inst, oi))
        self.instances = instances
        self._reserve_stable_slots(base)

    def _reserve_stable_slots(self, base: ReplaySource) -> None:
        """Give each A1+ fabricated object one slot, free across its whole life (no per-cycle reshuffle).

        Without this a fabricated track would borrow a different free slot whenever its preferred slot
        clashed with a real object, which fakes a per-cycle position jump (a generation artefact that
        would unfairly help the detector; CLAUDE.md rule 3). A0 keeps random slots; T4-replace keeps the
        real slot it overwrites.
        """
        cycles = base.cycles
        n = len(cycles)
        reserved_by_cycle: dict[int, set[int]] = {}
        for inst in self.instances:
            for obj in inst.objects:
                if obj.slot_pref is None or obj.replace:
                    continue
                active = [c for c in obj.per_cycle if 0 <= c < n]
                blocked: set[int] = set()
                for c in active:
                    blocked |= {raw[0] for _, raw in cycles[c].objects if raw}
                    blocked |= reserved_by_cycle.get(c, set())
                order = list(np.argsort(-self.ctx.slot_p))
                if obj.slot_pref not in blocked and obj.slot_pref <= self.ctx.slot_max:
                    slot = obj.slot_pref
                else:
                    slot = next((int(x) for x in order if int(x) not in blocked and int(x) <= self.ctx.slot_max),
                                None)
                    if slot is None:
                        slot = next((k for k in range(self.ctx.slot_max + 1) if k not in blocked), 0)
                obj.slot_pref = slot
                for c in active:
                    reserved_by_cycle.setdefault(c, set()).add(slot)

    # ------------------------------------------------------------------ helpers
    def _free_slot(self, used: set[int]) -> int:
        order = np.argsort(-self.ctx.slot_p)  # most-common real slots first
        for s in order:
            if int(s) not in used and int(s) <= self.ctx.slot_max:
                return int(s)
        s = 0
        while s in used:
            s += 1
        return s

    def _resolve_fab(self, inst: Instance, oi: int, cidx: int, used: set[int], real_slots: set[int]):
        """Return (slot, raw, replace_slot_or_None, fields_or_None) for one fabricated object."""
        obj = inst.objects[oi]
        fields = obj.per_cycle.get(cidx)
        L = self.ctx.level
        if fields is None:  # A0: random 8 bytes
            raw = bytes(int(b) for b in self.ctx.rng.integers(0, 256, size=8))
            return raw[0], raw, None, None
        x, y, vx, vy, rcs = fields
        if obj.replace:                                   # T4 A2+: overwrite the real slot in place
            slot = obj.slot_pref
        elif obj.slot_pref is not None:                   # A1+: keep the stable slot if free, else a free one
            slot = obj.slot_pref
            if L.slot_unique and (slot in used or slot in real_slots):
                slot = self._free_slot(used | real_slots)
            elif slot in used:
                slot = self._free_slot(used)
        else:
            slot = int(self.ctx.rng.integers(0, self.ctx.slot_max + 1))
        raw = encode_object(slot, x, y, vx, vy, rcs)
        return slot, raw, (obj.slot_pref if obj.replace else None), fields

    # ------------------------------------------------------------------ iteration
    def __iter__(self) -> Iterator[Frame]:
        fi = 0
        base_cycles = self.base.cycles
        lo, hi = self.base.cycle_range
        ctx = self.ctx
        for cidx in range(lo, hi):
            rc: RecordedCycle = base_cycles[cidx]
            header_t = rc.sync_timestamp
            have_header = rc.obj_count_header is not None and rc.meas_counter is not None and header_t is not None
            # real object frames as (ts, raw, slot)
            reals = []
            real_slots = set()
            for ts, raw in rc.objects:
                slot = raw[0] if raw else 0
                reals.append([ts, raw, slot, False, None])
                real_slots.add(slot)
            # build fabricated objects for this cycle
            fabs = []  # [ts|None, raw, slot, True, meta]
            used: set[int] = set()
            replaced_slots: set[int] = set()
            for inst, oi in self._by_cycle.get(cidx, []):
                slot, raw, replace_slot, fields = self._resolve_fab(inst, oi, cidx, used, real_slots)
                used.add(slot)
                meta = (inst.attack_id, inst.atype, inst.level)
                if replace_slot is not None:
                    replaced_slots.add(replace_slot)
                fabs.append([None, raw, slot, True, meta, replace_slot, fields])
            # apply in-place replacements: drop the matching real frame, fab inherits its ts
            if replaced_slots:
                kept = []
                real_by_slot = {r[2]: r for r in reals}
                for r in reals:
                    if r[2] in replaced_slots:
                        continue
                    kept.append(r)
                reals = kept
                real_slots = {r[2] for r in reals}
                for f in fabs:
                    rs = f[5]
                    if rs is not None and rs in real_by_slot:
                        f[0] = real_by_slot[rs][0]  # inherit the real frame's timestamp

            emit = self._assemble(rc, header_t, have_header, reals, fabs)
            # header
            if have_header:
                count = len(emit) if ctx.level.fix_header else rc.obj_count_header
                status = rc.sync_status if rc.sync_status is not None else 1
                yield Frame(CAN_ID_HEADER, build_header(count, rc.meas_counter, status), header_t)
                fi += 1
            for ts, raw, slot, is_fab, meta in emit:
                if is_fab and meta is not None:
                    aid, atype, lvl = meta
                    self.labels.append(Label(fi, 1, aid, atype, lvl))
                yield Frame(CAN_ID_OBJECT, raw, ts)
                fi += 1

    def _assemble(self, rc, header_t, have_header, reals, fabs):
        """Order the cycle's object frames and assign fabricated timestamps per the level."""
        ctx = self.ctx
        out = []
        if ctx.level.order_aware and have_header:
            # re-space the whole burst back-to-back in ascending range (observed sensor order)
            items = [(r[1], r[2], False, None) for r in reals] + [(f[1], f[2], True, f[4]) for f in fabs]
            def rng_of(raw):
                try:
                    o = decode_object(raw)
                    return o.range
                except Exception:
                    return 1e9
            items.sort(key=lambda it: rng_of(it[0]))
            first = min((r[0] - header_t for r in reals), default=3)
            first = max(2, int(first))
            for i, (raw, slot, is_fab, meta) in enumerate(items):
                out.append((header_t + first + 2 * i, raw, slot, is_fab, meta))
            return out
        # keep real timestamps; give each fab its own arrival offset
        for r in reals:
            out.append((r[0], r[1], r[2], False, None))
        for f in fabs:
            ts = f[0]
            if ts is None:
                off = ctx.sample_offset_window() if ctx.level.timing_in_window else ctx.sample_offset_any()
                ts = (header_t if header_t is not None else 0) + off
            out.append((ts, f[1], f[2], True, f[4]))
        out.sort(key=lambda it: it[0])
        return out

    def write_labels(self, path: str | Path) -> None:
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LABEL_FIELDS)
            w.writeheader()
            for lb in self.labels:
                w.writerow({"frame_index": lb.frame_index, "is_attack": lb.is_attack, "attack_id": lb.attack_id,
                            "attack_type": lb.attack_type, "level": lb.level})


def label_map(src: MixedSource) -> dict[int, Label]:
    return {lb.frame_index: lb for lb in src.labels}
