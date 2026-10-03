"""ReplaySource: rebuild the CAN frame stream from a decoder v2 CSV.

Per cycle it emits a 0x60A header rebuilt from meas_counter, obj_count_header and sync_status
(byte3 = 0x00, see frames.build_header), followed by the 0x60B frames from raw_hex with their
obj_timestamp. Nothing is dropped silently: every skipped or odd row is counted in ``report``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header


@dataclass(frozen=True)
class RecordedCycle:
    cycle_num: int
    meas_counter: int | None
    obj_count_header: int | None
    sync_status: int | None
    sync_timestamp: int | None
    objects: tuple[tuple[int, bytes], ...]  # (obj_timestamp, raw bytes)


@dataclass
class LoadReport:
    rows: int = 0
    cycles: int = 0
    malformed_rows: int = 0  # unparseable hex / timestamp
    short_frames: int = 0  # raw_len < 8 (kept, flagged downstream)
    raw_len_mismatch: int = 0  # raw_len column disagrees with raw_hex
    duplicate_rows: int = 0  # identical (cycle, slot, timestamp, bytes) rows (kept)
    cycles_without_header: int = 0
    header_count_mismatch: int = 0
    non_monotonic_timestamps: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _int(s: str | None, base: int = 10) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s, base)
    except ValueError:
        return None


@lru_cache(maxsize=8)
def load_recorded_cycles(csv_path: str) -> tuple[tuple[RecordedCycle, ...], LoadReport]:
    """Parse a CSV once (cached). Cycles are returned in file order."""
    rep = LoadReport()
    cycles: list[RecordedCycle] = []
    cur_key = None
    cur_hdr: tuple | None = None
    cur_objs: list[tuple[int, bytes]] = []
    seen: set = set()

    def close():
        if cur_key is None:
            return
        cnum, meas, cnt, st, sts = cur_hdr
        if cnt is None or meas is None or sts is None:
            rep.cycles_without_header += 1
        elif cnt != len(cur_objs):
            rep.header_count_mismatch += 1
        cycles.append(RecordedCycle(cnum, meas, cnt, st, sts, tuple(cur_objs)))

    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            rep.rows += 1
            cnum = _int(r.get("cycle_num"))
            if cnum != cur_key:
                close()
                cur_key = cnum
                cur_hdr = (
                    cnum,
                    _int(r.get("meas_counter")),
                    _int(r.get("obj_count_header")),
                    _int(r.get("sync_status"), 16),
                    _int(r.get("sync_timestamp")),
                )
                cur_objs = []
            try:
                raw = bytes(int(t, 16) for t in r["raw_hex"].split())
            except (ValueError, AttributeError):
                rep.malformed_rows += 1
                continue
            ts = _int(r.get("obj_timestamp"))
            if ts is None or not raw:
                rep.malformed_rows += 1
                continue
            if len(raw) < 8:
                rep.short_frames += 1
            if _int(r.get("raw_len")) != len(raw):
                rep.raw_len_mismatch += 1
            key = (cnum, raw, ts)
            if key in seen:
                rep.duplicate_rows += 1
            seen.add(key)
            cur_objs.append((ts, raw))
    close()
    rep.cycles = len(cycles)
    prev = None
    for c in cycles:
        times = ([c.sync_timestamp] if c.sync_timestamp is not None else []) + [t for t, _ in c.objects]
        for t in times:
            if prev is not None and t < prev:
                rep.non_monotonic_timestamps += 1
            prev = t
    return tuple(cycles), rep


class ReplaySource:
    """FrameSource over one recorded CSV, optionally restricted to a range of cycle indices."""

    def __init__(self, csv_path: str | Path, cycle_range: tuple[int, int] | None = None, name: str | None = None):
        self.path = Path(csv_path)
        self.cycles, self.report = load_recorded_cycles(str(self.path))
        lo, hi = cycle_range if cycle_range is not None else (0, len(self.cycles))
        self.cycle_range = (max(0, lo), min(len(self.cycles), hi))
        self.name = name or f"{self.path.stem}[{self.cycle_range[0]}:{self.cycle_range[1]}]"

    def selected(self) -> tuple[RecordedCycle, ...]:
        lo, hi = self.cycle_range
        return self.cycles[lo:hi]

    def __iter__(self) -> Iterator[Frame]:
        for c in self.selected():
            if c.obj_count_header is not None and c.meas_counter is not None and c.sync_timestamp is not None:
                status = c.sync_status if c.sync_status is not None else 0
                yield Frame(CAN_ID_HEADER, build_header(c.obj_count_header, c.meas_counter, status), c.sync_timestamp)
            for ts, raw in c.objects:
                yield Frame(CAN_ID_OBJECT, raw, ts)
