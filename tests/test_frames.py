from __future__ import annotations

import random

import pytest

from conftest import FILES, iter_rows, needs_data
from phantomguard.frames import (
    FIELDS,
    MalformedFrame,
    build_header,
    decode_object,
    encode_object,
    encode_radar_object,
    hex_to_bytes,
    parse_header,
)


def test_encode_decode_known_frame():
    raw = hex_to_bytes("0b 4e 93 f4 80 20 00 a8")
    o = decode_object(raw)
    assert (o.slot, o.x, o.y, o.vx, o.vy, o.rcs) == (0x0B, 2.8, -2.2, 0.0, 0.0, 20.0)
    assert encode_radar_object(o) == raw


def test_random_fields_round_trip():
    rng = random.Random(0)
    for _ in range(20000):
        vals = {k: rng.randint(0, s.raw_max) * s.scale + s.offset for k, s in FIELDS.items()}
        b = encode_object(rng.randint(0, 255), vals["x"], vals["y"], vals["vx"], vals["vy"], vals["rcs"],
                          dyn_prop=rng.randint(0, 7), reserved=rng.randint(0, 3))
        o = decode_object(b)
        assert encode_radar_object(o) == b
        assert decode_object(encode_radar_object(o)) == o


def test_encode_rejects_out_of_range():
    with pytest.raises(ValueError):
        encode_object(1, 2000.0, 0, 0, 0, 0)


def test_short_frame_is_malformed():
    with pytest.raises(MalformedFrame):
        decode_object(b"\x01\x02")


def test_header_round_trip():
    h = parse_header(build_header(23, 47907, 0x01))
    assert (h.count, h.meas_counter, h.status, h.byte3) == (23, 47907, 1, 0)


@needs_data
@pytest.mark.data
@pytest.mark.parametrize("name", FILES)
def test_every_row_round_trips_and_matches_csv(name):
    rows = byte_diffs = col_mismatch = 0
    first_bad = []
    for r in iter_rows(name):
        rows += 1
        raw = hex_to_bytes(r["raw_hex"])
        o = decode_object(raw)
        again = encode_radar_object(o)
        if again != raw:
            byte_diffs += 1
            if len(first_bad) < 5:
                first_bad.append((r["cycle_num"], r["raw_hex"], again.hex(" ")))
        assert decode_object(again) == o
        csv_vals = (r["x_m"], r["y_m"], r["vx_mps"], r["vy_mps"], r["rcs_dbsm"], r["slot"], r["dyn_prop"])
        mine = (f"{o.x:.2f}", f"{o.y:.2f}", f"{o.vx:.2f}", f"{o.vy:.2f}", f"{o.rcs:.1f}",
                f"0x{o.slot:02x}", str(o.dyn_prop))
        col_mismatch += csv_vals != mine
    print(f"{name}: rows={rows} byte_diffs={byte_diffs} csv_column_mismatches={col_mismatch} {first_bad}")
    assert byte_diffs == 0, first_bad
    assert col_mismatch == 0
