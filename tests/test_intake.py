import json
import os
import stat

import pytest

from phantomguard.eval.intake import IntakeError, load_manifest, scan, verify
from phantomguard.workspace import CSV_COLUMNS


def _csv(path, counter0=100, cycles=3, objs=2, speed="0.0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(CSV_COLUMNS)]
    for c in range(cycles):
        for o in range(objs):
            row = {k: "0" for k in CSV_COLUMNS}
            row.update(source_file="live", cycle_num=str(c), meas_counter=str(counter0 + c),
                       sync_timestamp=str(1000 + 332 * c), sync_status="0x01", obj_count_header=str(objs),
                       obj_count_actual=str(objs), slot=f"0x{o:02x}", raw_len="8", speed_mps=speed)
            lines.append(",".join(row[k] for k in CSV_COLUMNS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "rec"
    _csv(src / "a" / "one.csv", 100)
    _csv(src / "b" / "two.csv", 500, speed="1.0")
    _csv(src / "c" / "three.csv", 900)
    return src


def test_scan_reserves_without_reading_content_and_never_writes_sources(source):
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.rglob("*.csv")}
    for p in before:
        os.chmod(p, stat.S_IREAD)
    try:
        m = scan(source, roles={"a": "train", "b": "validation"}, reserve={"c"}, now="2026-10-05")
    finally:
        for p in before:
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.rglob("*.csv")} == before
    c = m["groups"]["c"]
    assert c["reserved"] and not c["inspected"] and c["role"] == "final_test"
    assert set(c["files"][0]) == {"file", "bytes", "sha256"}          # nothing beyond identity
    a = m["groups"]["a"]["files"][0]["structure"]
    assert a["rows"] == 6 and a["cycles"] == 3 and a["meas_counter"]["discontinuities"] == 0
    assert m["groups"]["b"]["files"][0]["structure"]["moving_row_fraction"] == 1.0
    assert any("recommended" in n for n in m["notes"])


def test_reservation_is_sticky_and_final_test_requires_reserve(source, tmp_path):
    m = scan(source, reserve={"c"}, now="2026-10-05")
    again = scan(source, previous=m, now="2026-10-06")
    assert again["groups"]["c"]["reserved"] and again["groups"]["c"]["reservation"]["since"] == "2026-10-05"
    assert again["manifest_id"] == m["manifest_id"]
    with pytest.raises(IntakeError, match="requires --reserve"):
        scan(source, roles={"a": "final_test"})
    with pytest.raises(IntakeError, match="only have role final_test"):
        scan(source, roles={"c": "train"}, reserve={"c"})


def test_changed_bytes_and_duplicates_fail_clearly(source, tmp_path):
    m = scan(source, now="2026-10-05")
    _csv(source / "a" / "one.csv", 101)
    with pytest.raises(IntakeError, match="changed since the previous manifest"):
        scan(source, previous=m)
    assert verify(m, source) == ["changed a/one.csv"]
    (source / "c" / "dup.csv").write_bytes((source / "b" / "two.csv").read_bytes())
    with pytest.raises(IntakeError, match="byte-identical"):
        scan(source)


def test_manifest_schema_and_id_are_checked(source, tmp_path):
    path = tmp_path / "m.json"
    m = scan(source, now="2026-10-05")
    path.write_text(json.dumps(m), encoding="utf-8")
    assert load_manifest(path)["manifest_id"] == m["manifest_id"]
    path.write_text(json.dumps({**m, "schema": "phantomguard.intake/99"}), encoding="utf-8")
    with pytest.raises(IntakeError, match="not supported"):
        load_manifest(path)
    m["groups"]["a"]["role"] = "train"
    path.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(IntakeError, match="does not match"):
        load_manifest(path)


def test_overlapping_counters_and_bad_schema_reported(tmp_path):
    src = tmp_path / "rec"
    _csv(src / "x" / "p.csv", 100)
    _csv(src / "y" / "q.csv", 101)
    assert any("overlap" in n for n in scan(src)["notes"])
    (src / "y" / "bad.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(IntakeError, match="decoder-v2"):
        scan(src)
