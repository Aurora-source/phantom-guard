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
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from phantomguard.attack.scenarios import GenContext, Instance
from phantomguard.config import bval
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header, decode_object, encode_object
from phantomguard.io.replay import RecordedCycle, ReplaySource

LABEL_FIELDS = ["frame_index", "is_attack", "attack_id", "attack_type", "level"]
# Evaluator-only join between final emitted frames and the recorded source frames. Injection shifts
# final frame indices, so a clean control stream and an attacked stream are matched through this
# lineage (source cycle + source object index), never through equal indices or numeric slots.
LINEAGE_FIELDS = ["frame_index", "kind", "source_cycle", "source_obj", "source_slot", "attack_id"]
LINEAGE_VERSION = 1


@dataclass
class Label:
    frame_index: int
    is_attack: int
    attack_id: int
    attack_type: str
    level: str


@dataclass
class Lineage:
    """kind: header | object | other (recorded, source_cycle/obj set), forged (no counterpart) or
    replacement (a forged frame that overwrote the recorded object at source_cycle/source_obj)."""

    frame_index: int
    kind: str
    source_cycle: int | None
    source_obj: int | None
    source_slot: int | None
    attack_id: int | str = ""


class MixedSource:
    """FrameSource over a recorded file with fabricated frames mixed in at planned cycles."""

    def __init__(self, base: ReplaySource, ctx: GenContext, instances: list[Instance], tag: str = "",
                 complete_labels: bool = False, on_slot_exhausted: str = "raise"):
        if on_slot_exhausted not in {"raise", "drop"}:
            raise ValueError("on_slot_exhausted must be 'raise' or 'drop'")
        self.base = base
        self.ctx = ctx
        self.name = f"{base.name}+{tag}" if tag else base.name
        self.labels: list[Label] = []
        self.lineage: list[Lineage] = []
        self.complete_labels = complete_labels
        self.on_slot_exhausted = on_slot_exhausted
        self.instances = list(instances)
        # Instances (or objects) that could not get a stable valid slot. Version-1 behaviour was to fail the
        # whole run; 'drop' keeps every other instance and records the capacity exclusion explicitly.
        self.dropped: list[dict] = []
        self._reserve_stable_slots(base)
        # index instances' objects by the cycles they are active in (after any capacity drops)
        self._by_cycle: dict[int, list[tuple[Instance, int]]] = {}
        for inst in self.instances:
            for oi, obj in enumerate(inst.objects):
                for c in obj.per_cycle:
                    self._by_cycle.setdefault(c, []).append((inst, oi))

    def _min_objects(self, inst: Instance) -> int:
        cfg = getattr(self.ctx, "cfg", None) or {}
        if inst.atype == "T2":
            return int(cfg.get("attack", {}).get("T2", {}).get("count", [1])[0])
        return 1

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
        for inst in list(self.instances):
            added: list[tuple[int, int]] = []
            kept, failed = [], 0
            for obj in inst.objects:
                if obj.slot_pref is None or obj.replace or inst.atype == "T4":
                    kept.append(obj)
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
                    if self.on_slot_exhausted == "raise":
                        from phantomguard.eval.attack_adapter import UnsupportedAttack
                        raise UnsupportedAttack("no stable free slot across requested attack lifetime; "
                                                "cannot preserve slot uniqueness without a generation artefact")
                    failed += 1
                    continue
                obj.slot_pref = slot
                for c in active:
                    reserved_by_cycle.setdefault(c, set()).add(slot)
                    added.append((c, slot))
                kept.append(obj)
            if not failed:
                continue
            if len(kept) < self._min_objects(inst):
                for c, slot in added:  # release this instance's reservations for later instances
                    reserved_by_cycle.get(c, set()).discard(slot)
                self.instances.remove(inst)
                self.dropped.append({"attack_id": inst.attack_id, "reason": "slot_capacity", "level": inst.level,
                                     "attack_type": inst.atype, "objects_requested": len(inst.objects),
                                     "objects_allocated": len(kept), "scheduled_first_cycle": inst.c0,
                                     "scheduled_last_cycle": inst.c1, "note": inst.note})
            else:
                inst.objects = kept
                self.dropped.append({"attack_id": inst.attack_id, "reason": "slot_capacity_partial",
                                     "level": inst.level, "attack_type": inst.atype, "objects_requested":
                                     len(kept) + failed, "objects_allocated": len(kept),
                                     "scheduled_first_cycle": inst.c0, "scheduled_last_cycle": inst.c1,
                                     "note": inst.note})

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
        if obj.replace or inst.atype == "T4":              # T4 A0/A1 must append with the SAME slot
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
        self.labels.clear()
        self.lineage.clear()
        fi = 0
        base_cycles = self.base.cycles
        lo, hi = self.base.cycle_range
        ctx = self.ctx
        last_emitted_t = None
        for cidx in range(lo, hi):
            rc: RecordedCycle = base_cycles[cidx]
            header_t = rc.sync_timestamp
            shift = 0
            if self.instances and header_t is not None and last_emitted_t is not None:
                shift = max(0, last_emitted_t + int(bval(ctx.baseline, "burst_gap_lo")) - header_t)
                header_t += shift
            have_header = rc.obj_count_header is not None and rc.meas_counter is not None and header_t is not None
            # real object frames as (ts, raw, slot)
            reals = []
            real_slots = set()
            src_of: dict[int, int] = {}  # id(raw bytes object) -> object index in the recorded cycle
            for j, (ts, raw) in enumerate(rc.objects):
                slot = raw[0] if raw else 0
                reals.append([ts + shift, raw, slot, False, None])
                real_slots.add(slot)
                src_of[id(raw)] = j
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
            replaced_src: dict[int, int] = {}  # id(forged raw) -> recorded object index it overwrote
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
                        replaced_src[id(f[1])] = src_of.get(id(real_by_slot[rs][1]), -1)

            emit = self._assemble(rc, header_t, have_header, reals, fabs)
            # Every rewritten count header is a forged frame, separate from object identification.
            original_header = getattr(self.base, "headers", {}).get(cidx)
            if have_header:
                count = len(emit) if ctx.level.fix_header else rc.obj_count_header
                status = rc.sync_status if rc.sync_status is not None else 1
                active = self._by_cycle.get(cidx, [])
                changed = count != rc.obj_count_header
                self.lineage.append(Lineage(fi, "header", cidx, -1, None,
                                            active[0][0].attack_id if changed and active else ""))
                if self.complete_labels:
                    inst = active[0][0] if changed and active else None
                    self.labels.append(Label(fi, int(inst is not None), inst.attack_id if inst else "",
                                             inst.atype if inst else "", inst.level if inst else ""))
                data = build_header(count, rc.meas_counter, status)
                if original_header and not changed:
                    data = original_header.data
                yield Frame(CAN_ID_HEADER, data, header_t)
                last_emitted_t = header_t
                fi += 1
            elif original_header is not None:
                self.lineage.append(Lineage(fi, "other", cidx, None, None))
                if self.complete_labels:
                    self.labels.append(Label(fi, 0, "", "", ""))
                yield Frame(original_header.can_id, original_header.data, original_header.timestamp_ticks + shift)
                last_emitted_t = original_header.timestamp_ticks + shift
                fi += 1
            outgoing = [(Frame(CAN_ID_OBJECT, raw, ts), is_fab, meta, raw) for ts, raw, slot, is_fab, meta in emit]
            outgoing += [(Frame(frame.can_id, frame.data, frame.timestamp_ticks + shift), False, None, None)
                         for frame in getattr(self.base, "others", {}).get(cidx, [])]
            outgoing.sort(key=lambda item: item[0].timestamp_ticks)
            for frame, is_fab, meta, raw in outgoing:
                if is_fab and meta is not None:
                    aid, atype, lvl = meta
                    self.labels.append(Label(fi, 1, aid, atype, lvl))
                    if id(raw) in replaced_src:
                        j = replaced_src[id(raw)]
                        self.lineage.append(Lineage(fi, "replacement", cidx, j if j >= 0 else None,
                                                    rc.objects[j][1][0] if j >= 0 else None, aid))
                    else:
                        self.lineage.append(Lineage(fi, "forged", None, None, None, aid))
                else:
                    if raw is None:
                        self.lineage.append(Lineage(fi, "other", cidx, None, None))
                    else:
                        j = src_of.get(id(raw))
                        self.lineage.append(Lineage(fi, "object", cidx, j, raw[0] if raw else None))
                    if self.complete_labels:
                        self.labels.append(Label(fi, 0, "", "", ""))
                yield frame
                last_emitted_t = frame.timestamp_ticks
                fi += 1

    def _assemble(self, rc, header_t, have_header, reals, fabs):
        """Order the cycle's object frames and assign fabricated timestamps per the level."""
        ctx = self.ctx
        spacing = int(bval(ctx.baseline, "burst_gap_lo"))
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
            first = max(spacing, int(first))
            for i, (raw, slot, is_fab, meta) in enumerate(items):
                out.append((header_t + first + spacing * i, raw, slot, is_fab, meta))
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
        # Even a timing-naive sender serialises on classic CAN. Inserting a frame
        # delays frames behind it; coincident/one-tick arrivals are simulation
        # artefacts, not attacker capability levels. Preserve wider A0/A1/A2 gaps.
        serialised = []
        previous = header_t if have_header else None
        for ts, raw, slot, is_fab, meta in out:
            ts = max(ts, previous + spacing) if previous is not None else ts
            serialised.append((ts, raw, slot, is_fab, meta))
            previous = ts
        return serialised

    def write_lineage(self, path: str | Path) -> None:
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LINEAGE_FIELDS)
            w.writeheader()
            for ln in self.lineage:
                w.writerow({"frame_index": ln.frame_index, "kind": ln.kind,
                            "source_cycle": "" if ln.source_cycle is None else ln.source_cycle,
                            "source_obj": "" if ln.source_obj is None else ln.source_obj,
                            "source_slot": "" if ln.source_slot is None else ln.source_slot,
                            "attack_id": ln.attack_id})

    def write_labels(self, path: str | Path) -> None:
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LABEL_FIELDS)
            w.writeheader()
            for lb in self.labels:
                w.writerow({"frame_index": lb.frame_index, "is_attack": lb.is_attack, "attack_id": lb.attack_id,
                            "attack_type": lb.attack_type, "level": lb.level})


def scenarios_module():
    from phantomguard.attack import scenarios
    return scenarios


def label_map(src: MixedSource) -> dict[int, Label]:
    return {lb.frame_index: lb for lb in src.labels if lb.is_attack}


class _BufferedSource:
    """Offline adapter for an ordinary FrameSource, preserving anomalous input frames."""
    def __init__(self, source):
        from phantomguard.cycles import iter_cycles
        self.name = getattr(source, "name", "frame-source")
        self.cycles, self.headers, self.others = [], {}, {}
        for c in iter_cycles(source):
            h = c.header
            objects = sorted(c.objects + c.malformed_objects, key=lambda o: o.frame_index)
            self.cycles.append(RecordedCycle(c.index, h.meas_counter if h else None,
                                             h.count if h else None, h.status if h else None, c.header_t,
                                             tuple((o.t, o.data) for o in objects)))
            if c.header_frame is not None:
                self.headers[c.index] = c.header_frame
            self.others[c.index] = [o.frame for o in c.other if o.frame_index != c.header_frame_index]
        self.cycle_range = (0, len(self.cycles))


class AttackedSource(MixedSource):
    """Documented FrameSource wrapper; labels/lifecycle stay in separate sidecars.

    This is an offline generator and buffers a generic source for scenario scheduling.
    Earlier-stream replay planners may read only the prefix preceding their onset.
    """
    def __init__(self, source, attacker, labels_path=None):
        base = source if isinstance(source, ReplaySource) else _BufferedSource(source)
        instances = attacker.plan(base)
        super().__init__(base, attacker.context, instances, complete_labels=True, on_slot_exhausted="drop")
        self.labels_path = Path(labels_path) if labels_path else None
        self.seed, self.run_index = attacker.seed, attacker.run_index

    def __iter__(self):
        yield from super().__iter__()
        if self.labels_path:
            self.labels_path.parent.mkdir(parents=True, exist_ok=True)
            self.write_labels(self.labels_path)
            self.write_lineage(self.labels_path.with_suffix(".lineage.csv"))
            emitted = {str(l.attack_id) for l in self.labels if l.is_attack}
            lo, hi = self.base.cycle_range
            lifecycle = [{"attack_id": i.attack_id, "attack_type": i.atype, "level": i.level,
                          "scheduled_first_cycle": i.c0-lo, "scheduled_last_cycle": i.c1-lo,
                          "emitted": str(i.attack_id) in emitted, "note": i.note,
                          "truncated_by_eof": any(c >= hi for o in i.objects for c in o.per_cycle)}
                         for i in self.instances]
            plan_log = list(getattr(self.ctx, "plan_log", []) or [])
            self.labels_path.with_suffix(".instances.json").write_text(
                json.dumps({"seed": self.seed, "run_index": self.run_index,
                            "requested_instances": self.ctx.cfg["attack"]["instances_per_run"],
                            "planned_instances": lifecycle, "lineage_version": LINEAGE_VERSION,
                            "planner_version": getattr(scenarios_module(), "PLANNER_VERSION", 1),
                            "planner_retries": int(self.ctx.cfg["attack"].get("planner_retries", 0)),
                            "plan_log": plan_log, "dropped_slot_capacity": list(self.dropped),
                            "planning_seconds": float(getattr(self.ctx, "plan_seconds", 0.0) or 0.0),
                            "planner_note": getattr(self.ctx, "plan_note", None)}, indent=2) + "\n",
                encoding="utf-8")
