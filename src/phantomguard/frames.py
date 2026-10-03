"""CAN frame model for the SR75: Frame, object decode/encode, header build/parse.

Decoding delegates to ``tools/decode.py`` (decoder v2) so the field formulas exist in exactly one
place. ``encode_object`` is the exact inverse of that decoder: it uses the same bit layout and
scale/offset values (listed in ``FIELDS`` below and checked against the decoder by the round-trip
test over every recorded row). Forged frames are built only through ``encode_object`` /
``build_header`` and so take the same decode path as real frames.
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from pathlib import Path

CAN_ID_HEADER = 0x60A
CAN_ID_OBJECT = 0x60B
OBJECT_LEN = 8
HEADER_MIN_LEN = 5  # bytes 0..4 carry count, counter, (unknown byte3), status

_DECODER_PATH = Path(__file__).resolve().parents[2] / "tools" / "decode.py"


def _load_decoder():
    spec = importlib.util.spec_from_file_location("_sr75_decode_v2", _DECODER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load decoder from {_DECODER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_decoder = _load_decoder()


@dataclass(frozen=True)
class FieldSpec:
    """Bit width and linear scaling of one 0x60B field (value = raw * scale + offset)."""

    bits: int
    scale: float
    offset: float

    @property
    def raw_max(self) -> int:
        return (1 << self.bits) - 1

    def to_raw(self, value: float) -> int:
        raw = round((value - self.offset) / self.scale)
        if not 0 <= raw <= self.raw_max:
            raise ValueError(f"value {value} out of encodable range for {self}")
        return raw

    def quantise(self, value: float) -> float:
        """Nearest value the frame can carry (same rounding as the decoder)."""
        raw = min(max(round((value - self.offset) / self.scale), 0), self.raw_max)
        return round(raw * self.scale + self.offset, 2)

    @property
    def lo(self) -> float:
        return self.offset

    @property
    def hi(self) -> float:
        return self.raw_max * self.scale + self.offset


# Same layout and scaling as tools/decode.py (decode_object). Verified by tests/test_frames.py.
FIELDS: dict[str, FieldSpec] = {
    "x": FieldSpec(13, 0.2, -500.0),
    "y": FieldSpec(11, 0.2, -204.6),
    "vx": FieldSpec(10, 0.25, -128.0),
    "vy": FieldSpec(9, 0.25, -64.0),
    "rcs": FieldSpec(8, 0.5, -64.0),
}


@dataclass(frozen=True)
class Frame:
    """One CAN frame as seen on the bus."""

    can_id: int
    data: bytes
    timestamp_ticks: int


@dataclass(frozen=True)
class RadarObject:
    slot: int
    x: float
    y: float
    vx: float
    vy: float
    dyn_prop: int
    rcs: float
    reserved: int = 0  # bits 4-3 of byte 6, unused by the protocol

    @property
    def range(self) -> float:
        return math.hypot(self.x, self.y)

    @property
    def azimuth_deg(self) -> float:
        return math.degrees(math.atan2(self.y, self.x))

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    @property
    def radial_velocity(self) -> float:
        """Velocity component along the line of sight (negative = approaching)."""
        r = self.range
        if r == 0:
            return 0.0
        return (self.vx * self.x + self.vy * self.y) / r


@dataclass(frozen=True)
class Header:
    count: int
    meas_counter: int
    status: int
    byte3: int = 0


class MalformedFrame(ValueError):
    pass


def decode_object(data: bytes) -> RadarObject:
    """Decode one 8-byte 0x60B payload via tools/decode.py."""
    if len(data) != OBJECT_LEN:
        raise MalformedFrame(f"object frame has {len(data)} bytes, expected {OBJECT_LEN}")
    o = _decoder.decode_object(list(data), None)
    return RadarObject(
        slot=o.slot,
        x=o.x,
        y=o.y,
        vx=o.vx,
        vy=o.vy,
        dyn_prop=o.dyn_prop,
        rcs=o.rcs,
        reserved=(data[6] >> 3) & 0x03,
    )


def encode_object(
    slot: int,
    x: float,
    y: float,
    vx: float,
    vy: float,
    rcs: float,
    dyn_prop: int = 0,
    reserved: int = 0,
) -> bytes:
    """Pack fields into the bit-packed 8-byte 0x60B layout (inverse of decode_object)."""
    if not 0 <= slot <= 0xFF:
        raise ValueError(f"slot {slot} out of range")
    if not 0 <= dyn_prop <= 7 or not 0 <= reserved <= 3:
        raise ValueError("dyn_prop must be 0..7 and reserved 0..3")
    xr = FIELDS["x"].to_raw(x)
    yr = FIELDS["y"].to_raw(y)
    vxr = FIELDS["vx"].to_raw(vx)
    vyr = FIELDS["vy"].to_raw(vy)
    rr = FIELDS["rcs"].to_raw(rcs)
    return bytes(
        [
            slot,
            xr >> 5,
            ((xr & 0x1F) << 3) | (yr >> 8),
            yr & 0xFF,
            vxr >> 2,
            ((vxr & 0x03) << 6) | (vyr >> 3),
            ((vyr & 0x07) << 5) | (reserved << 3) | dyn_prop,
            rr,
        ]
    )


def encode_radar_object(o: RadarObject) -> bytes:
    return encode_object(o.slot, o.x, o.y, o.vx, o.vy, o.rcs, o.dyn_prop, o.reserved)


def build_header(count: int, meas_counter: int, status: int = 0x01, length: int = HEADER_MIN_LEN) -> bytes:
    """0x60A payload: byte0 count, bytes1-2 counter (big-endian), byte3 0x00, byte4 status."""
    if not 0 <= count <= 0xFF or not 0 <= meas_counter <= 0xFFFF:
        raise ValueError("count or meas_counter out of range")
    if length < HEADER_MIN_LEN:
        raise ValueError(f"header length must be >= {HEADER_MIN_LEN}")
    body = [count, (meas_counter >> 8) & 0xFF, meas_counter & 0xFF, 0x00, status]
    return bytes(body + [0] * (length - HEADER_MIN_LEN))


def parse_header(data: bytes) -> Header:
    if len(data) < HEADER_MIN_LEN:
        raise MalformedFrame(f"header frame has {len(data)} bytes, expected >= {HEADER_MIN_LEN}")
    return Header(count=data[0], meas_counter=(data[1] << 8) | data[2], status=data[4], byte3=data[3])


def hex_to_bytes(raw_hex: str) -> bytes:
    return bytes(int(t, 16) for t in raw_hex.split())
