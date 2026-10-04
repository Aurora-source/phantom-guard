#!/usr/bin/env python3
"""
SR75 CAN Radar Decoder  (v2: bit-packed ARS408-style object layout)
===================================================================
Decodes ZLG USBCAN-II captures from a Nanoradar SR75 running in object mode.

Inputs it accepts:
  * raw text logs printed by test.c        (e.g. static.txt)
  * CSVs written by this tool, old or new  (re-decoded from the stored bytes,
                                            no recapture needed)
  * live capture from the adapter          (--live)

Protocol
--------
The SR75 uses the 0x60A / 0x60B IDs of the Continental ARS408 protocol, and
the object frame is BIT-PACKED, not byte-aligned. The v1 decoder read it
byte-by-byte, which produced a meaningless "range_raw" (high byte stuck at
0x4E-0x51) and treated the top bit of the velocity field as a "valid" flag,
silently discarding every moving target.

  0x60A  Object list header (one per scan cycle)
         byte0      number of objects that follow
         byte1-2    measurement counter (16-bit, big-endian)
         byte4      status byte (0x01 observed while operating)

  0x60B  Object, one frame per detected target
         byte0                     object / slot ID
         13 bits from byte1-2      x  (longitudinal) = raw * 0.20 - 500.0   m
         11 bits from byte2-3      y  (lateral)      = raw * 0.20 - 204.6   m
         10 bits from byte4-5      vx (longitudinal) = raw * 0.25 - 128.0   m/s
          9 bits from byte5-6      vy (lateral)      = raw * 0.25 -  64.0   m/s
          3 bits from byte6        dynamic property  (see note below)
          8 bits byte7             RCS               = raw * 0.50 -  64.0   dBsm

  The layout is inferred from the ARS408 spec and confirmed only by
  plausibility (static clutter decodes to ~0 m/s, positions are realistic).
  Verify once in the lab: stand ~1.5 m straight ahead, expect x ~ 1.5, y ~ 0;
  walk toward the sensor, expect vx < 0.

  Dynamic property: static clutter reports 0, which in the ARS408 spec means
  "moving", so the SR75 appears not to populate this field meaningfully.
  It is kept as a raw column but is NOT used to decide what is moving.

Moving targets
--------------
Nothing is dropped by default. Every object frame becomes a row, with an
is_moving column computed from velocity:
    is_moving = sqrt(vx^2 + vy^2) >= --moving-threshold   (default 0.30 m/s)
Velocity is quantised in 0.25 m/s steps and static clutter often shows a
single step of jitter, so the default threshold sits just above one step.

Usage
-----
  python3 decode.py static.txt
  python3 decode.py --csv out.csv onePersonMovingSideToSide.csv
  python3 decode.py --max-range 3 --csv out.csv a.csv b.csv c.csv
  python3 decode.py --moving-only --cycles 20 a.csv
  python3 decode.py --live --csv live.csv
"""

import re
import sys
import csv
import math
import argparse
import statistics
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

CAN_ID_SYNC = 0x60A
CAN_ID_OBJECT = 0x60B

DEFAULT_MOVING_THRESHOLD = 0.30  # m/s, just above one 0.25 m/s quantisation step

LOG_RE = re.compile(
    r"\[(\d+)\]\s+(\d+)\s+ID:\s*0x([0-9a-fA-F]+)\s+\S+\s+Data:\s*((?:[0-9a-fA-F]{2}\s*)+)"
)

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def safe_byte(raw: list, idx: int) -> Optional[int]:
    """raw[idx], or None if the frame was shorter than idx+1 bytes.

    test.c prints exactly can[i].DataLen bytes, which classic CAN allows to
    be 0-8, so no field access may assume a byte is present.
    """
    return raw[idx] if idx < len(raw) else None


def to_int(s: str, base: int = 10) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s, base)
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class RadarObject:
    slot: int
    timestamp: Optional[int]
    raw: tuple  # the bytes exactly as received
    x: Optional[float] = None  # m, longitudinal (straight ahead)
    y: Optional[float] = None  # m, lateral
    vx: Optional[float] = None  # m/s, longitudinal (negative = approaching)
    vy: Optional[float] = None  # m/s, lateral
    dyn_prop: Optional[int] = None  # 3-bit, raw; not used for motion
    rcs: Optional[float] = None  # dBsm

    @property
    def raw_len(self) -> int:
        return len(self.raw)

    @property
    def is_short(self) -> bool:
        return self.raw_len < 8

    @property
    def raw_hex(self) -> str:
        return " ".join(f"{b:02x}" for b in self.raw)

    @property
    def range_m(self) -> Optional[float]:
        if self.x is None or self.y is None:
            return None
        return math.hypot(self.x, self.y)

    @property
    def azimuth_deg(self) -> Optional[float]:
        if self.x is None or self.y is None:
            return None
        return math.degrees(math.atan2(self.y, self.x))

    @property
    def speed(self) -> Optional[float]:
        if self.vx is None or self.vy is None:
            return None
        return math.hypot(self.vx, self.vy)

    def is_moving(self, threshold: float) -> bool:
        s = self.speed
        return s is not None and s >= threshold


@dataclass
class ScanCycle:
    cycle_num: int
    scan_counter: Optional[int] = None  # low byte of the measurement counter
    meas_counter: Optional[int] = None  # full 16-bit counter (logs/live only)
    obj_count_hdr: Optional[int] = None
    sync_status: Optional[int] = None
    sync_timestamp: Optional[int] = None
    objects: list = field(default_factory=list)

    @property
    def actual_count(self) -> int:
        return len(self.objects)


@dataclass
class ParseStats:
    total_sync_frames: int = 0
    short_sync_frames: int = 0  # < 5 bytes
    total_object_frames: int = 0
    short_object_frames: int = 0  # < 8 bytes
    count_mismatches: int = 0  # cycles where header count != frames received

    def merge(self, other: "ParseStats") -> None:
        self.total_sync_frames += other.total_sync_frames
        self.short_sync_frames += other.short_sync_frames
        self.total_object_frames += other.total_object_frames
        self.short_object_frames += other.short_object_frames
        self.count_mismatches += other.count_mismatches

    def print_report(self) -> None:
        print(f"{BOLD}  Frame integrity{RESET}")
        print(
            f"    Sync frames     : {self.total_sync_frames}  ({self.short_sync_frames} short)"
        )
        if self.total_object_frames:
            pct = 100 * self.short_object_frames / self.total_object_frames
            print(
                f"    Object frames   : {self.total_object_frames}  "
                f"({self.short_object_frames} short, {pct:.1f}%)"
            )
        flag = (
            f"  {YELLOW}⚠ check for dropped frames{RESET}"
            if self.count_mismatches
            else ""
        )
        print(f"    Count mismatches: {self.count_mismatches} cycle(s){flag}")


# ──────────────────────────────────────────────────────────────────────────────
# Frame decoding
# ──────────────────────────────────────────────────────────────────────────────


def decode_object(raw: list, ts: Optional[int]) -> RadarObject:
    """Decode one 0x60B frame. Fields whose bytes are missing come back None."""

    def have(*idx):
        return all(i < len(raw) for i in idx)

    obj = RadarObject(slot=raw[0], timestamp=ts, raw=tuple(raw))
    if have(1, 2):
        obj.x = round(((raw[1] << 5) | (raw[2] >> 3)) * 0.2 - 500.0, 2)
    if have(2, 3):
        obj.y = round((((raw[2] & 0x07) << 8) | raw[3]) * 0.2 - 204.6, 2)
    if have(4, 5):
        obj.vx = round(((raw[4] << 2) | (raw[5] >> 6)) * 0.25 - 128.0, 2)
    if have(5, 6):
        obj.vy = round((((raw[5] & 0x3F) << 3) | (raw[6] >> 5)) * 0.25 - 64.0, 2)
    if have(6):
        obj.dyn_prop = raw[6] & 0x07
    if have(7):
        obj.rcs = round(raw[7] * 0.5 - 64.0, 1)
    return obj


class CycleBuilder:
    """Groups frames into scan cycles, bounded by 0x60A headers.

    Objects that arrive before the first header form cycle 1 with no header
    info, which is the partial cycle at the start of every capture.
    """

    def __init__(self):
        self.stats = ParseStats()
        self.cycles: list[ScanCycle] = []
        self.current = ScanCycle(cycle_num=1)

    def _close_current(self) -> Optional[ScanCycle]:
        c = self.current
        if not c.objects and c.obj_count_hdr is None:
            return None
        if c.obj_count_hdr is not None and c.obj_count_hdr != c.actual_count:
            self.stats.count_mismatches += 1
        self.cycles.append(c)
        return c

    def start_cycle(self, **hdr) -> Optional[ScanCycle]:
        done = self._close_current()
        next_num = self.current.cycle_num + (1 if done else 0)
        self.current = ScanCycle(cycle_num=next_num, **hdr)
        return done

    def feed_frame(
        self, can_id: int, raw: list, ts: Optional[int]
    ) -> Optional[ScanCycle]:
        """Feed one raw CAN frame. Returns a cycle when one completes."""
        if not raw:
            return None
        if can_id == CAN_ID_SYNC:
            self.stats.total_sync_frames += 1
            if len(raw) < 5:
                self.stats.short_sync_frames += 1
            b1, b2 = safe_byte(raw, 1), safe_byte(raw, 2)
            return self.start_cycle(
                scan_counter=b2,
                meas_counter=(b1 << 8) | b2
                if b1 is not None and b2 is not None
                else None,
                obj_count_hdr=safe_byte(raw, 0),
                sync_status=safe_byte(raw, 4),
                sync_timestamp=ts,
            )
        if can_id == CAN_ID_OBJECT:
            self.stats.total_object_frames += 1
            if len(raw) < 8:
                self.stats.short_object_frames += 1
            self.current.objects.append(decode_object(raw, ts))
        return None

    def finish(self) -> list[ScanCycle]:
        self._close_current()
        self.current = ScanCycle(cycle_num=self.current.cycle_num + 1)
        return self.cycles


# ──────────────────────────────────────────────────────────────────────────────
# Input readers
# ──────────────────────────────────────────────────────────────────────────────


def parse_log_file(path: Path) -> tuple[list[ScanCycle], ParseStats]:
    """Raw text log as printed by test.c."""
    b = CycleBuilder()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LOG_RE.search(line)
            if not m:
                continue
            raw = [int(x, 16) for x in m.group(4).split()]
            b.feed_frame(int(m.group(3), 16), raw, int(m.group(1)))
    return b.finish(), b.stats


def _bytes_from_v1_row(r: dict) -> list:
    """Rebuild the original 8 bytes from a v1 CSV row.

    v1 stored them as: slot | range_raw (bytes 1-2) | lateral_raw (byte 3,
    signed) | status_hi | status_lo | reserved | rcs_raw. Stops at the first
    missing field so short frames stay short.
    """
    out = [to_int(r["slot"], 16)]
    rr = to_int(r["range_raw"])
    if rr is None:
        return out
    out += [(rr >> 8) & 0xFF, rr & 0xFF]
    for key, base in (
        ("lateral_raw", 10),
        ("status_hi", 16),
        ("status_lo", 16),
        ("reserved", 16),
        ("rcs_raw", 10),
    ):
        v = to_int(r[key], base)
        if v is None:
            return out
        out.append(v & 0xFF)
    return out


def parse_csv_file(path: Path) -> tuple[list[ScanCycle], ParseStats]:
    """Re-decode a CSV produced by this tool (v1 byte-aligned or v2 raw_hex).

    Cycle boundaries and header info come from the CSV's own columns, since
    the 0x60A frames themselves were not stored row by row.
    """
    b = CycleBuilder()
    b.current = None  # cycles are driven by cycle_num, not by sync frames
    cycles: list[ScanCycle] = []
    cur: Optional[ScanCycle] = None

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or [])
        is_v2 = "raw_hex" in cols
        if not is_v2 and "range_raw" not in cols:
            sys.exit(
                f"[ERROR] {path.name}: not a decoder CSV (no raw_hex or range_raw column)"
            )

        for r in reader:
            cnum = to_int(r.get("cycle_num"))
            if cur is None or cnum != cur.cycle_num:
                if cur is not None:
                    if (
                        cur.obj_count_hdr is not None
                        and cur.obj_count_hdr != cur.actual_count
                    ):
                        b.stats.count_mismatches += 1
                    cycles.append(cur)
                cur = ScanCycle(
                    cycle_num=cnum if cnum is not None else len(cycles) + 1,
                    scan_counter=to_int(r.get("scan_counter")),
                    meas_counter=to_int(r.get("meas_counter")) if is_v2 else None,
                    obj_count_hdr=to_int(r.get("obj_count_header")),
                    sync_status=to_int(
                        (r.get("sync_status") or r.get("cycle_status") or ""), 16
                    ),
                    sync_timestamp=to_int(r.get("sync_timestamp")),
                )
                if cur.obj_count_hdr is not None:
                    b.stats.total_sync_frames += 1

            raw = (
                [int(x, 16) for x in r["raw_hex"].split()]
                if is_v2
                else _bytes_from_v1_row(r)
            )
            if not raw or raw[0] is None:
                continue
            b.stats.total_object_frames += 1
            if len(raw) < 8:
                b.stats.short_object_frames += 1
            cur.objects.append(decode_object(raw, to_int(r.get("obj_timestamp"))))

    if cur is not None:
        if cur.obj_count_hdr is not None and cur.obj_count_hdr != cur.actual_count:
            b.stats.count_mismatches += 1
        cycles.append(cur)
    return cycles, b.stats


def parse_any(path: Path) -> tuple[list[ScanCycle], ParseStats]:
    if path.suffix.lower() == ".csv":
        return parse_csv_file(path)
    return parse_log_file(path)


# ──────────────────────────────────────────────────────────────────────────────
# Filtering (opt-in only; nothing is dropped by default)
# ──────────────────────────────────────────────────────────────────────────────


def filter_cycles(cycles: list[ScanCycle], args) -> list[ScanCycle]:
    """Apply --max-range / --moving-only / --slot.

    Cycles stay in the list even if they end up empty, and keep their
    original cycle_num, so gaps in time remain visible for windowed models.
    """
    for c in cycles:
        objs = c.objects
        if args.max_range is not None:
            objs = [
                o for o in objs if o.range_m is not None and o.range_m <= args.max_range
            ]
        if args.moving_only:
            objs = [o for o in objs if o.is_moving(args.moving_threshold)]
        if args.slot is not None:
            objs = [o for o in objs if o.slot == args.slot]
        c.objects = objs
    return cycles


# ──────────────────────────────────────────────────────────────────────────────
# Output: terminal
# ──────────────────────────────────────────────────────────────────────────────


def _stats_line(vals: list, unit: str) -> str:
    if not vals:
        return "n/a"
    return (
        f"min={min(vals):.2f}  max={max(vals):.2f}  "
        f"mean={statistics.mean(vals):.2f} {unit}"
    )


def print_summary(cycles: list[ScanCycle], label: str, thr: float) -> None:
    print(f"\n{BOLD}{'─' * 72}\n  SR75 decode summary  —  {label}{RESET}\n{'─' * 72}")
    print(f"  Scan cycles        : {CYAN}{len(cycles)}{RESET}")
    if not cycles:
        return

    objs = [o for c in cycles for o in c.objects]
    counts = [c.actual_count for c in cycles]
    print(f"  Objects            : {len(objs)}")
    print(
        f"  Objects / cycle    : min={min(counts)}  max={max(counts)}  "
        f"avg={statistics.mean(counts):.1f}"
    )

    ctrs = [c.scan_counter for c in cycles if c.scan_counter is not None]
    if len(ctrs) > 1:
        gaps = sum(1 for a, b in zip(ctrs, ctrs[1:]) if (b - a) % 256 != 1)
        print(f"  Counter gaps       : {gaps}")

    if not objs:
        return
    moving = [o for o in objs if o.is_moving(thr)]
    print(
        f"  Moving (≥{thr:.2f} m/s): {GREEN}{len(moving)}{RESET}  "
        f"({100 * len(moving) / len(objs):.1f}% of objects)"
    )

    print(
        f"\n  x (longitudinal)   : {_stats_line([o.x for o in objs if o.x is not None], 'm')}"
    )
    print(
        f"  y (lateral)        : {_stats_line([o.y for o in objs if o.y is not None], 'm')}"
    )
    print(
        f"  range              : {_stats_line([o.range_m for o in objs if o.range_m is not None], 'm')}"
    )
    print(f"  speed (moving only): {_stats_line([o.speed for o in moving], 'm/s')}")
    print(
        f"  RCS                : {_stats_line([o.rcs for o in objs if o.rcs is not None], 'dBsm')}"
    )

    bands = Counter()
    for o in objs:
        r = o.range_m
        if r is None:
            continue
        bands["0-3 m" if r <= 3 else "3-10 m" if r <= 10 else ">10 m"] += 1
    mov_near = sum(1 for o in moving if o.range_m is not None and o.range_m <= 3)
    print(
        f"\n  Range bands        : "
        + "  ".join(f"{k}: {bands[k]}" for k in ("0-3 m", "3-10 m", ">10 m"))
    )
    print(f"  Moving within 3 m  : {mov_near}")

    dyn = Counter(o.dyn_prop for o in objs if o.dyn_prop is not None)
    print(
        f"  dyn_prop values    : {dict(sorted(dyn.items()))}  {DIM}(not used for motion){RESET}"
    )
    print(f"{'─' * 72}")


def print_cycle(c: ScanCycle, thr: float) -> None:
    hdr = f"scan#{c.scan_counter:03d}" if c.scan_counter is not None else "scan#?"
    print(
        f"{GREEN}Cycle {c.cycle_num:5d}  {hdr}  ts={c.sync_timestamp}  "
        f"objects={c.actual_count}{RESET}"
    )
    print(
        DIM + f"  {'slot':>4}  {'x':>7}  {'y':>7}  {'range':>6}  {'az°':>6}  "
        f"{'vx':>6}  {'vy':>6}  {'rcs':>5}  {'dyn':>3}  mov" + RESET
    )

    def f(v, w, p=2):
        return f"{v:>{w}.{p}f}" if v is not None else f"{'n/a':>{w}}"

    for o in c.objects:
        mov = f"{GREEN}●{RESET}" if o.is_moving(thr) else f"{DIM}·{RESET}"
        short = f"  {YELLOW}[short {o.raw_len}B]{RESET}" if o.is_short else ""
        dyn = f"{o.dyn_prop:>3}" if o.dyn_prop is not None else "n/a"
        print(
            f"  {o.slot:>4x}  {f(o.x, 7)}  {f(o.y, 7)}  {f(o.range_m, 6)}  {f(o.azimuth_deg, 6, 1)}  "
            f"{f(o.vx, 6)}  {f(o.vy, 6)}  {f(o.rcs, 5, 1)}  {dyn}   {mov}{short}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Output: CSV
# ──────────────────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "source_file",
    "cycle_num",
    "scan_counter",
    "meas_counter",
    "sync_timestamp",
    "sync_status",
    "obj_count_header",
    "obj_count_actual",
    "slot",
    "obj_timestamp",
    "raw_len",
    "raw_hex",
    "x_m",
    "y_m",
    "range_m",
    "azimuth_deg",
    "vx_mps",
    "vy_mps",
    "speed_mps",
    "is_moving",
    "dyn_prop",
    "rcs_dbsm",
]


def _fmt(v, p=2):
    return "" if v is None else f"{v:.{p}f}"


def write_csv(cycles: list[ScanCycle], out_path: Path, label: str, thr: float) -> None:
    rows = 0
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for c in cycles:
            for o in c.objects:
                w.writerow(
                    {
                        "source_file": label,
                        "cycle_num": c.cycle_num,
                        "scan_counter": ""
                        if c.scan_counter is None
                        else c.scan_counter,
                        "meas_counter": ""
                        if c.meas_counter is None
                        else c.meas_counter,
                        "sync_timestamp": ""
                        if c.sync_timestamp is None
                        else c.sync_timestamp,
                        "sync_status": ""
                        if c.sync_status is None
                        else f"0x{c.sync_status:02x}",
                        "obj_count_header": ""
                        if c.obj_count_hdr is None
                        else c.obj_count_hdr,
                        "obj_count_actual": c.actual_count,
                        "slot": f"0x{o.slot:02x}",
                        "obj_timestamp": "" if o.timestamp is None else o.timestamp,
                        "raw_len": o.raw_len,
                        "raw_hex": o.raw_hex,
                        "x_m": _fmt(o.x),
                        "y_m": _fmt(o.y),
                        "range_m": _fmt(o.range_m, 3),
                        "azimuth_deg": _fmt(o.azimuth_deg, 1),
                        "vx_mps": _fmt(o.vx),
                        "vy_mps": _fmt(o.vy),
                        "speed_mps": _fmt(o.speed, 3),
                        "is_moving": int(o.is_moving(thr)),
                        "dyn_prop": "" if o.dyn_prop is None else o.dyn_prop,
                        "rcs_dbsm": _fmt(o.rcs, 1),
                    }
                )
                rows += 1
    print(f"  CSV written → {out_path}  ({rows} rows)")


# ──────────────────────────────────────────────────────────────────────────────
# Live USB-CAN mode
# ──────────────────────────────────────────────────────────────────────────────


def run_live(args) -> None:
    """Live decode via the ZLG adapter.

    NOTE: the ctypes struct below is hand-copied, not checked against the
    real VCI_CAN_OBJ in controlcan.h. If live output looks wrong while file
    decoding looks right, compare the two field by field.
    """
    import time
    from ctypes import cdll, Structure, c_uint32, c_uint8, c_byte, byref

    here = Path(__file__).parent
    lib_path = next(
        (
            p
            for p in (
                here / "usbcan_ii_libusb_x64" / "libusbcan.so",
                here / "libusbcan.so",
            )
            if p.exists()
        ),
        None,
    )
    if lib_path is None:
        sys.exit("[ERROR] Cannot find libusbcan.so next to this script")
    lib = cdll.LoadLibrary(str(lib_path))

    class VCI_CAN_OBJ(Structure):
        _fields_ = [
            ("ID", c_uint32),
            ("TimeStamp", c_uint32),
            ("TimeFlag", c_uint8),
            ("SendType", c_byte),
            ("RemoteFlag", c_byte),
            ("ExternFlag", c_byte),
            ("DataLen", c_byte),
            ("Data", c_uint8 * 8),
            ("Reserved", c_uint8 * 3),
        ]

    class VCI_INIT_CONFIG(Structure):
        _fields_ = [
            ("AccCode", c_uint32),
            ("AccMask", c_uint32),
            ("Reserved", c_uint32),
            ("Filter", c_uint8),
            ("Timing0", c_uint8),
            ("Timing1", c_uint8),
            ("Mode", c_uint8),
        ]

    DEV, IDX, BAUD = c_uint32(4), c_uint32(0), 0x1C00  # USBCAN-II, 500 kbps
    if not lib.VCI_OpenDevice(DEV, IDX, 0):
        sys.exit("[ERROR] VCI_OpenDevice failed — device plugged in? permissions?")
    for chn in range(2):
        cfg = VCI_INIT_CONFIG(0, 0xFFFFFFFF, 0, 1, BAUD & 0xFF, BAUD >> 8, 0)
        lib.VCI_InitCAN(DEV, IDX, chn, byref(cfg))
        lib.VCI_StartCAN(DEV, IDX, chn)
    print(
        f"[OK] USBCAN-II open, 2 channels @ 500 kbps\n{BOLD}Live decode — Ctrl+C to stop{RESET}\n"
    )

    b = CycleBuilder()
    try:
        while True:
            n = lib.VCI_GetReceiveNum(DEV, IDX, 0)
            if n <= 0:
                time.sleep(0.005)
                continue
            buf = (VCI_CAN_OBJ * min(n, 500))()
            got = lib.VCI_Receive(DEV, IDX, 0, byref(buf), len(buf), 100)
            for i in range(got):
                fr = buf[i]
                if fr.RemoteFlag:
                    continue
                raw = list(fr.Data[: fr.DataLen])
                done = b.feed_frame(fr.ID & 0x1FFFFFFF, raw, fr.TimeStamp)
                if done is not None:
                    filter_cycles([done], args)
                    print_cycle(done, args.moving_threshold)
    except KeyboardInterrupt:
        print("\n[INFO] Stopping...")
    finally:
        for chn in range(2):
            lib.VCI_ResetCAN(DEV, IDX, chn)
        lib.VCI_CloseDevice(DEV, IDX)
        print("[OK] Device closed")
        cycles = filter_cycles(b.finish(), args)
        b.stats.print_report()
        if args.csv:
            write_csv(cycles, Path(args.csv), "live", args.moving_threshold)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SR75 CAN radar decoder (bit-packed object layout)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("files", nargs="*", help="test.c text logs and/or decoder CSVs")
    p.add_argument("--live", action="store_true", help="read from the USB-CAN adapter")
    p.add_argument("--csv", metavar="FILE", help="write decoded rows to CSV")
    p.add_argument(
        "--max-range",
        type=float,
        metavar="M",
        help="keep only objects within M metres (e.g. 3 for the demo area)",
    )
    p.add_argument(
        "--moving-only",
        action="store_true",
        help="keep only objects with speed ≥ --moving-threshold",
    )
    p.add_argument(
        "--moving-threshold",
        type=float,
        default=DEFAULT_MOVING_THRESHOLD,
        metavar="MPS",
        help=f"speed counted as moving (default {DEFAULT_MOVING_THRESHOLD})",
    )
    p.add_argument(
        "--slot", type=lambda s: int(s, 0), help="keep a single slot ID, e.g. 0x0a"
    )
    p.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="print the first N cycles in detail (-1 = all)",
    )
    p.add_argument("--no-color", action="store_true", help="disable ANSI colours")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.no_color:
        global CYAN, GREEN, YELLOW, DIM, RED, RESET, BOLD
        CYAN = GREEN = YELLOW = DIM = RED = RESET = BOLD = ""

    if args.live:
        run_live(args)
        return
    if not args.files:
        build_parser().print_help()
        return

    out_base = Path(args.csv) if args.csv else None
    all_cycles, all_stats = [], ParseStats()

    for name in args.files:
        path = Path(name)
        if not path.exists():
            print(f"[WARN] File not found: {path}", file=sys.stderr)
            continue

        print(f"\n{BOLD}Parsing {path.name}…{RESET}")
        cycles, stats = parse_any(path)
        cycles = filter_cycles(cycles, args)
        all_stats.merge(stats)

        print_summary(cycles, path.name, args.moving_threshold)
        stats.print_report()

        if args.cycles:
            shown = cycles if args.cycles == -1 else cycles[: args.cycles]
            for c in shown:
                print_cycle(c, args.moving_threshold)

        if out_base:
            out = (
                out_base
                if len(args.files) == 1
                else out_base.with_name(f"{out_base.stem}_{path.stem}{out_base.suffix}")
            )
            if out.resolve() == path.resolve():
                sys.exit(f"[ERROR] refusing to overwrite the input file {path}")
            write_csv(cycles, out, path.name, args.moving_threshold)

        all_cycles.extend(cycles)

    if len(args.files) > 1:
        print_summary(
            all_cycles, f"{len(args.files)} files combined", args.moving_threshold
        )
        all_stats.print_report()


if __name__ == "__main__":
    main()
