from __future__ import annotations

import json

import pytest

from conftest import FILES, needs_data
from phantomguard.config import BASELINE_PATH, load_config, raw_path
from phantomguard.cycles import CycleAssembler, iter_cycles
from phantomguard.eval.splits import loso_folds, time_block, time_block_segments
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header, encode_object
from phantomguard.io.live import LiveSource
from phantomguard.io.replay import ReplaySource
from phantomguard.io.source import FrameSource
from phantomguard.tracks import TrackManager


def test_live_source_is_stub():
    with pytest.raises(NotImplementedError):
        LiveSource()


def test_time_block_is_contiguous_and_disjoint():
    s = time_block(1000, 0.6, 0.2)
    assert s == {"train": (0, 600), "val": (600, 800), "test": (800, 1000)}


def _obj(slot, x, t):
    return Frame(CAN_ID_OBJECT, encode_object(slot, x, 0.0, 0.0, 0.0, 16.0), t)


def test_assembler_groups_by_header_and_counts_headerless():
    frames = [_obj(1, 2.0, 5),  # before any header
              Frame(CAN_ID_HEADER, build_header(2, 10), 100), _obj(1, 2.0, 103), _obj(2, 3.0, 105),
              Frame(0x123, b"\x00" * 8, 106),
              Frame(CAN_ID_HEADER, build_header(1, 11), 432), _obj(1, 2.0, 435), Frame(CAN_ID_OBJECT, b"\x01\x02", 437)]
    cycles = list(iter_cycles(frames))
    assert [c.header is None for c in cycles] == [True, False, False]
    assert [len(c.objects) for c in cycles] == [1, 2, 1]
    assert cycles[1].objects[1].offset == 5 and cycles[1].other[0].reason == "BAD_ID"
    assert len(cycles[2].malformed_objects) == 1 and cycles[2].n_received == 2


def test_track_manager_bridges_one_cycle_gap_and_splits_jumps():
    tm = TrackManager(1.0, 1e-4, 0.3, max_gap_cycles=1)
    hdr = lambda i: Frame(CAN_ID_HEADER, build_header(1, i), 332 * i)
    frames = []
    for i, x in enumerate([2.0, 2.0, None, 2.2, 9.0]):
        frames.append(hdr(i))
        if x is not None:
            frames.append(_obj(5, x, 332 * i + 3))
    ids = []
    for c in iter_cycles(frames):
        for ob, tr in tm.update(c):
            ids.append((tr.track_id, tr.born_by_jump))
    assert ids == [(0, False), (0, False), (0, False), (1, True)]


@needs_data
@pytest.mark.parametrize("name", FILES)
def test_replay_rebuilds_every_frame(name):
    cfg = load_config()
    src = ReplaySource(raw_path(cfg, name))
    assert isinstance(src, FrameSource)
    frames = list(src)
    rep = src.report
    n_hdr = sum(f.can_id == CAN_ID_HEADER for f in frames)
    n_obj = sum(f.can_id == CAN_ID_OBJECT for f in frames)
    assert n_obj == rep.rows and n_hdr == rep.cycles - rep.cycles_without_header
    assert rep.malformed_rows == 0 and rep.header_count_mismatch == 0 and rep.non_monotonic_timestamps == 0
    print(name, rep.as_dict())
    # cycle ranges partition the stream
    n = rep.cycles
    parts = [ReplaySource(raw_path(cfg, name), r) for r in [(0, n // 2), (n // 2, n)]]
    assert sum(1 for p in parts for f in p if f.can_id == CAN_ID_OBJECT) == n_obj


@needs_data
def test_splits_cover_files_without_overlap():
    segs = time_block_segments()
    for f in FILES:
        tr, va, te = (next(s for s in segs[p] if s.file == f) for p in ("train", "val", "test"))
        assert tr.lo == 0 and tr.hi == va.lo and va.hi == te.lo and te.n > 0
    folds = loso_folds()
    for held, fold in folds.items():
        assert all(s.file != held for s in fold["train"] + fold["val"])
        assert fold["test"][0].file == held


@needs_data
def test_baseline_json_records_rule_for_every_threshold():
    if not BASELINE_PATH.exists():
        pytest.skip("run scripts/learn_baseline.py")
    b = json.loads(BASELINE_PATH.read_text())
    for k, v in b.items():
        if k.startswith("_"):
            continue
        if k == "learned_artifacts":
            # Provenance is not a detector threshold; each model records permitted
            # train/validation segments and a stable artifact identity.
            assert set(v) == {"ae", "iforest", "replay"}
            for artifact in v.values():
                assert artifact["artifact_id"] and artifact["schema_version"] == 1
                assert set(artifact["segments"]) == {"train", "val"}
                assert artifact["normalization_split"] == "train"
            continue
        if k == "learned_selection":
            assert v["model"] == "ae" and "no test selection" in v["rule"]
            continue
        assert "rule" in v and "value" in v, k
        # thresholds come from train, calibration from validation; never from test (hard rule 5)
        # profile v2: rule thresholds from out-of-recording CV over train+validation recordings (still never test)
        assert v["split"] in ("train", "val", "cv_out_of_recording"), (k, v["split"])
        if v["split"] == "cv_out_of_recording":
            assert k in ("rule_thresholds", "fusion_mn"), k
        if v["split"] == "val":
            assert k.startswith(("ae_threshold", "iforest_threshold", "soft_quantile", "fusion_mn")), k
    # every threshold that can be checked on held-out clean data has a val exceedance recorded
    for k in ("cadence_lo", "arrival_hi", "burst_gap_hi", "range_order_tol", "rr_resid_hard", "rcs_std_hard",
              "speed_max", "rcs_by_range", "colocation_min"):
        assert "val_exceedance" in b[k], k


@needs_data
def test_collect_merge_keeps_every_track():
    from phantomguard.stats.baseline import collect, collect_segment

    cfg = load_config()
    segs = time_block_segments(cfg)["val"]
    assert len(collect(cfg, segs).tracks) == sum(len(collect_segment(cfg, s).tracks) for s in segs)
