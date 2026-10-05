import numpy as np
import pytest

from conftest import needs_data
from phantomguard.attack.pools import build_pools
from phantomguard.attack.prepared import PreparedError, export, identity_key, load
from phantomguard.config import load_config
from phantomguard.eval.splits import Segment

CFG = load_config()
SEGS = [Segment("multiplePeopleChaotic.csv", 0, 600)]
SHA = {"multiplePeopleChaotic.csv": "2e08ed3c26c9cb64a5039878b1ef371fc2138ad0aa717efddc2eca1bea952f52"}


@needs_data
def test_export_roundtrip_is_identical_and_immutable(tmp_path):
    path = export(CFG, SEGS, SHA, tmp_path)
    key = identity_key(CFG, SEGS, SHA)
    loaded, direct = load(path, key), build_pools(CFG, SEGS)
    for name in ("pos_roi", "pos_moving", "vel_moving", "rcs_roi", "rcs_range_roi"):
        assert np.array_equal(getattr(loaded, name), getattr(direct, name))
    assert len(loaded.moving_tracks) == len(direct.moving_tracks)
    assert all(np.array_equal(a, b) for a, b in zip(loaded.static_tracks, direct.static_tracks))
    assert loaded.azimuth_range == direct.azimuth_range
    before = path.read_bytes()
    assert export(CFG, SEGS, SHA, tmp_path) == path and path.read_bytes() == before
    with pytest.raises(PreparedError, match="identity key"):
        load(path, identity_key(CFG, [Segment("multiplePeopleChaotic.csv", 0, 601)], SHA))
    with pytest.raises(PreparedError, match="loading bound"):
        load(path, key, max_bytes=10)


def test_key_depends_on_config_and_recording_identity():
    k = identity_key(CFG, SEGS, SHA)
    cfg2 = {**CFG, "roi": {**CFG["roi"], "max_range": 16.0}}
    assert identity_key(cfg2, SEGS, SHA) != k
    assert identity_key(CFG, SEGS, {"multiplePeopleChaotic.csv": "0" * 64}) != k


def test_garbage_file_is_rejected(tmp_path):
    bad = tmp_path / "pools-x.npz"
    bad.write_bytes(b"not a zip")
    with pytest.raises(PreparedError):
        load(bad, "x")
