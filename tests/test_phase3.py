"""Explicit clean/forged frame fixtures; these are interface tests, not an attacker."""
from __future__ import annotations

import copy
import math
from collections import deque

import numpy as np
import pytest

from phantomguard.config import load_config
from phantomguard.cycles import CycleAssembler, iter_cycles
from phantomguard.detect.common import ObjVerdict
from phantomguard.detect.kinematic import KinematicChecker
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.detect.replay_fp import ReplayChecker, build_library, track_fingerprints
from phantomguard.eval.metrics import AttackLabel, attack_metrics, lite
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, RadarObject, build_header, encode_object
from phantomguard.stats.baseline import SegmentStats, fit_rr_scale, track_features, window_residuals
from phantomguard.tracks import Track, TrackManager, TrackPoint


@pytest.fixture
def config():
    cfg = copy.deepcopy(load_config())
    cfg["kinematic"].update(window_cycles=3, rcs_window_cycles=3, min_moving_cycles=1)
    cfg["learned"]["window_cycles"] = 2
    cfg["replay"].update(k_gram=4, min_complexity=4, history_cycles=10)
    return cfg


@pytest.fixture
def baseline():
    # Intentionally broad test-only envelopes, independent of repository artifacts.
    return dict(cadence_lo=328, cadence_hi=338, counter_step=1, arrival_lo=1, arrival_hi=80,
        first_arrival_hi=8, burst_gap_lo=2, burst_gap_hi=7, range_order_tol=.3, slot_max=53,
        fixed_fields={"dyn_prop": [0], "reserved": [0], "status": [1]}, rcs_integer=True,
        rcs_lo=-30., rcs_hi=30., speed_max=10., accel_hard=1000., rr_scale=.8, rr_resid_hard=10.,
        pos_speed_hard=20., rcs_std_hard=10., rcs_by_range={"edges": [0., 15.], "bands": [[-30., 30., 100]]},
        colocation_min=.1, birth_range_hist={"edges": [0., 5., 10., 15.], "counts": [10, 10, 10]},
        reassign_jump=1., objs_per_cycle_lo=0, objs_per_cycle_hi=50)


def object_frame(slot=1, x=3., t=3, vx=0., rcs=20., can_id=CAN_ID_OBJECT):
    return Frame(can_id, encode_object(slot, x, 0., vx, 0., rcs), t)


def frames_at(times, counters=None, objects=None):
    out = []
    for i, t in enumerate(times):
        obs = [object_frame(t=t+3)] if objects is None else objects[i]
        counter = (i if counters is None else counters[i]) % 65536
        out.append(Frame(CAN_ID_HEADER, build_header(len(obs), counter), t))
        out.extend(obs)
    return out


def signature(result):
    # Processing duration is intentionally excluded from deterministic causal verdicts.
    return (result.index, tuple(result.cycle_reasons), result.cycle_alert,
        tuple((v.frame_index, v.track_id, tuple(v.reasons), v.scores, v.flagged, v.alert)
              for v in result.objects), result.frames)


def test_next_header_payload_and_future_frames_cannot_change_closed_verdict(config, baseline):
    prefix = frames_at([0, 332, 664])
    good = Frame(CAN_ID_HEADER, build_header(1, 3), 996)
    bad = Frame(CAN_ID_HEADER, b"\x01", 996)
    a = list(Detector(config, baseline).run(prefix + [good, object_frame(t=999)]))
    b = list(Detector(config, baseline).run(prefix + [bad, Frame(0x123, b"", 999)]))
    assert [signature(r) for r in a[:3]] == [signature(r) for r in b[:3]]
    assert "SHORT_HEADER" in b[3].cycle_reasons


def test_oversized_classic_can_header_alerts_at_its_own_causal_boundary(config, baseline):
    prefix = frames_at([0, 332, 664])
    invalid = Frame(CAN_ID_HEADER, build_header(1, 3) + bytes(4), 996)
    results = list(Detector(config, baseline).run(prefix + [invalid, object_frame(t=999)]))
    assert "HEADER_LEN" not in results[2].cycle_reasons
    assert "HEADER_LEN" in results[3].cycle_reasons and results[3].cycle_alert
    assert results[3].frames[0].frame_index == 6


@pytest.mark.parametrize("warmup", [0, 1, 2])
def test_exactly_configured_cadence_gaps_are_skipped(config, baseline, warmup):
    config["protocol"]["cadence_warmup_cycles"] = warmup
    # Every gap violates cadence, so the first checked gap is unambiguous.
    results = list(Detector(config, baseline).run(frames_at([0, 59, 118, 177, 236])))
    assert ["CADENCE" in r.cycle_reasons for r in results] == [False] + [i > warmup for i in range(1, 5)]


def test_counter_rollover_is_separate_from_capture_start_timing(config, baseline):
    results = list(Detector(config, baseline).run(frames_at([0, 59, 252, 584], [65534, 65535, 0, 1])))
    assert all("COUNTER" not in r.cycle_reasons and "CADENCE" not in r.cycle_reasons for r in results)


def test_lossless_records_include_header_malformed_duplicate_and_unknown_id(config, baseline):
    frames = [Frame(CAN_ID_HEADER, build_header(3, 0), 0), object_frame(t=3),
              Frame(CAN_ID_OBJECT, b"\x01\x02", 5), object_frame(slot=2, x=4., t=7),
              Frame(0x123, b"\x00", 9), Frame(CAN_ID_HEADER, b"\x00", 332), object_frame(t=335)]
    results = list(Detector(config, baseline).run(frames))
    assert [f.frame_index for r in results for f in r.frames] == list(range(len(frames)))
    assert [f.timestamp_ticks for r in results for f in r.frames] == [f.timestamp_ticks for f in frames]
    first = {v.frame_index: v for v in results[0].objects}
    assert "DUP_SLOT" in first[1].reasons and "DUP_SLOT" in first[2].reasons
    assert "BURST_GAP" not in first[3].reasons  # valid gap includes the malformed object on the bus
    assert {"FRAME_LEN", "BAD_ID"} <= set(results[0].cycle_reasons)
    assert "COUNT_MISMATCH" not in results[0].cycle_reasons  # malformed object counts as received
    assert results[0].assembly_delay_ticks == 332 and results[1].assembly_delay_ticks is None


def test_processing_latency_contains_assembly_cpu_but_not_capture_wait(config, baseline):
    results = list(Detector(config, baseline).run(frames_at([0, 332, 664])))
    for result in results:
        assert result.assembly_cpu_ms > 0
        assert result.detector_cpu_ms > 0
        assert result.latency_ms == pytest.approx(result.assembly_cpu_ms + result.detector_cpu_ms)
    assert results[0].assembly_delay_ticks == 332  # capture wait remains separately expressed
    cycle = next(iter_cycles(frames_at([0])))
    standalone = Detector(config, baseline).process_cycle(cycle)
    assert standalone.assembly_cpu_ms == 0
    assert standalone.detector_cpu_ms == standalone.latency_ms


def test_corrupt_replay_library_fails_with_explicit_outcome(tmp_path):
    (tmp_path / "replay_library_timeblock.pkl").write_bytes(b"not a pickle")
    with pytest.raises(ValueError, match="corrupt or incompatible replay library"):
        load_artifacts(models_dir=tmp_path)


def test_real_pipeline_lossless_records_join_malformed_objects_and_forged_headers(config, baseline):
    # The same numeric slot appears in authentic and malformed frames; only final indices
    # identify labels. A separate forged-header instance never adds an object denominator.
    frames = [Frame(CAN_ID_HEADER, build_header(2, 0), 0), object_frame(slot=7,t=3),
              Frame(CAN_ID_OBJECT,b"\x07",5), Frame(0x123,b"",7),
              Frame(CAN_ID_HEADER,b"",332), object_frame(slot=8,t=335),
              Frame(CAN_ID_HEADER,build_header(1,2),664), object_frame(slot=8,t=667)]
    results = list(Detector(config,baseline).run(frames))
    labels = [AttackLabel(i, i in {2,4}, "bad" if i==2 else "header" if i==4 else "",
                          "T1" if i in {2,4} else "", "A0" if i in {2,4} else "")
              for i in range(len(frames))]
    row, instances = attack_metrics([lite(r) for r in results],labels,config,("protocol",))
    assert row["attack_instances"] == row["detected_instances"] == 2
    assert row["forged_object_frames"] == row["identified_object_frames"] == 1
    assert {i["attack_id"]:i["identified"] for i in instances} == {"bad":True,"header":False}
    assert results[0].frames[0].reasons == ()  # unrelated bad-ID/length never identify this header
    assert results[1].objects[0].reasons == []  # missing header is a cycle warning


def test_headerless_leading_objects_and_invalid_header_preserve_boundaries(config, baseline):
    frames = [object_frame(t=0), Frame(CAN_ID_HEADER, b"", 10), object_frame(t=13),
              Frame(CAN_ID_HEADER, build_header(1, 5), 342), object_frame(t=345)]
    results = list(Detector(config, baseline).run(frames))
    assert len(results) == 3
    assert "NO_HEADER" in results[0].cycle_reasons
    assert {"SHORT_HEADER", "NO_HEADER"} <= set(results[1].cycle_reasons)
    assert "COUNTER" not in results[2].cycle_reasons


def test_configured_can_ids_are_honoured(config, baseline):
    config["protocol"].update(header_can_id=0x700, object_can_id=0x701)
    frames = [Frame(0x700, build_header(1, 0), 0), object_frame(t=3, can_id=0x701)]
    result = next(Detector(config, baseline).run(frames))
    assert not result.cycle_reasons
    assert [f.can_id for f in result.frames] == [0x700, 0x701]


def test_track_gaps_reassignment_duplicates_and_missing_observations(config, baseline):
    tm = TrackManager(1., 1e-4, .3, history=3, max_gap_cycles=1)
    obs = [[object_frame(t=3)], [], [object_frame(x=3.2, t=667)],
           [object_frame(x=8., t=999), object_frame(x=9., t=1001)], [], [], [object_frame(x=8., t=1995)]]
    linked = [tm.update(c) for c in iter_cycles(frames_at([i*332 for i in range(7)], objects=obs))]
    assert linked[0][0][1].track_id == linked[2][0][1].track_id
    assert linked[3][0][1].born_by_jump
    assert linked[3][0][1].track_id != linked[3][1][1].track_id
    assert linked[6][0][1].track_id != linked[3][0][1].track_id
    assert not linked[6][0][1].born_by_jump


def test_roi_excludes_physics_values_but_protocol_still_checks_all_frames(config, baseline):
    outside = object_frame(x=20., vx=12., rcs=40.5)
    result = next(Detector(config, baseline).run(frames_at([0], objects=[[outside]])))
    assert not result.objects[0].in_roi
    assert not set(result.objects[0].reasons) & {"SPEED", "RCS_GRID", "RCS_RANGE", "RCS_BAND"}
    bad = Frame(CAN_ID_OBJECT, encode_object(100, 20., 0., 12., 0., 40.5), 200)
    result = next(Detector(config, baseline).run(frames_at([0], objects=[[bad]])))
    assert {"SLOT_RANGE", "ARRIVAL"} <= set(result.objects[0].reasons)


def point(i, x, vx=1., y=0., *, t=None, rcs=20.):
    rng = math.hypot(x, y)
    return TrackPoint(i, i*.1 if t is None else t, x, y, vx, 0., rcs, rng,
                      vx*x/rng if rng else 0., i)


def test_learned_rr_scale_and_quantised_window_tolerance(config, baseline):
    config["kinematic"]["window_cycles"] = 15
    baseline["rr_resid_hard"] = .12
    pts = [point(i, round((3.+.8*i*.0332)/.2)*.2, t=i*.0332) for i in range(15)]
    track = Track(0, 1, 0, False, deque(pts), total_points=len(pts))
    o = RadarObject(1, pts[-1].x, 0., 1., 0., 0, 20.)
    v = ObjVerdict(14, 1, o.x, 0., 1., 0., True, True)
    KinematicChecker(config, baseline).check_track(o, track, v)
    assert "RR_RESID" not in v.reasons
    wrong_scale = dict(baseline, rr_scale=1.5)
    v2 = ObjVerdict(14, 1, o.x, 0., 1., 0., True, True)
    KinematicChecker(config, wrong_scale).check_track(o, track, v2)
    assert "RR_RESID" in v2.reasons
    array = np.array([(p.cycle_index, p.t_s,p.x,p.y,p.vx,p.vy,p.rcs,p.rng,p.vr) for p in pts])
    rr, mv, _ = window_residuals(array, 15)
    assert v.scores["rr_resid"] == pytest.approx(abs(rr[0]-.8*mv[0]))


def test_birth_is_a_score_and_motion_rcs_envelopes_use_actual_elapsed_time(config, baseline):
    kin = KinematicChecker(config, baseline)
    o = RadarObject(1, 3., 0., 0., 0., 0, 20.)
    birth = Track(0, 1, 0, False, deque([point(0, 3., vx=0.)]), total_points=1)
    v = ObjVerdict(0, 1, 3., 0., 0., 0., True, False)
    kin.check_track(o, birth, v)
    assert v.scores["birth_nll"] > 0 and not v.reasons
    baseline["accel_hard"] = 6.
    baseline["rcs_std_hard"] = .1
    baseline["rcs_by_range"] = {"edges": [0., 15.], "bands": [[19., 21., 100]]}
    kin = KinematicChecker(config, baseline)
    pts = [point(0,3.,vx=0.,t=0.), point(2,3.,vx=.25,t=.0664), point(3,3.,vx=.25,t=.0996,rcs=25.)]
    tr = Track(0, 1, 0, False, deque(pts[:2]), total_points=2)
    v = ObjVerdict(2,1,3.,0.,.25,0.,True,False)
    kin.check_track(RadarObject(1,3.,0.,.25,0.,0,20.),tr,v)
    assert v.scores["accel"] == pytest.approx(.25/.0664)
    assert "ACCEL" not in v.reasons
    tr.points.append(pts[-1]); tr.total_points += 1
    v = ObjVerdict(3,1,3.,0.,.25,0.,True,False)
    kin.check_track(RadarObject(1,3.,0.,.25,0.,0,25.),tr,v)
    assert {"RCS_STD", "RCS_BAND"} <= set(v.reasons)
    older = ObjVerdict(0,1,3.,0.,0.,0.,True,False)
    younger = ObjVerdict(1,2,3.,0.,0.,0.,True,False)
    kin.check_colocation([(3.,0.,5,older),(3.,0.,1,younger)])
    assert "COLOC" not in older.reasons and "COLOC" in younger.reasons


def moving_track(tid=0, offset=0., start=0):
    xs = [1., 1.2, 1.6, 2.2, 3.]
    pts = [point(start+i, x+offset, vx=.5+.25*i) for i, x in enumerate(xs)]
    return Track(tid, tid+1, start, False, deque(pts), total_points=len(pts))


def test_translation_replay_matches_training_earlier_stream_and_concurrent(config):
    original = moving_track()
    fingerprints = track_fingerprints(list(original.points), 4, .2, .25, 4, .3)
    library = {f for _, tf, rf in fingerprints for f in (tf, rf)}
    assert ReplayChecker(config, library).check(moving_track(1, offset=5.), 4) == (True, "library")
    checker = ReplayChecker(config)
    assert checker.check(original, 4) == (False, None)
    assert checker.check(moving_track(1, offset=5., start=1), 5) == (True, "earlier_stream")
    checker = ReplayChecker(config)
    checker.check(original, 4)
    assert checker.check(moving_track(1, offset=5.), 4) == (True, "concurrent")


def test_replay_expiration_roi_and_gaps_do_not_create_matches(config):
    checker = ReplayChecker(config)
    checker.check(moving_track(), 4)
    assert checker.check(moving_track(1, offset=5., start=11), 15) == (False, None)
    gap = moving_track(2)
    gap.points[-1].cycle_index += 1
    assert ReplayChecker(config).check(gap, 5) == (False, None)
    outside = moving_track(3, offset=20.)
    assert not track_fingerprints(list(outside.points), 4, .2, .25, 4, .3, roi=15.)
    arr = np.array([(p.cycle_index,p.t_s,p.x,p.y,p.vx,p.vy,p.rcs,p.rng,p.vr) for p in outside.points])
    assert not build_library(config, [arr])


@pytest.mark.parametrize("speed,step", [(0., 0.), (1., .2)])
def test_static_clutter_and_simple_repeated_motion_are_not_replays(config, speed, step):
    checker = ReplayChecker(config)
    for tid in (0, 1):
        pts = [point(i, 1.+i*step+tid*4, vx=speed) for i in range(5)]
        tr = Track(tid, tid+1, 0, False, deque(pts), total_points=5)
        assert checker.check(tr, 4) == (False, None)


def test_simple_back_and_forth_periodic_motion_is_not_replay_evidence(config):
    checker = ReplayChecker(config)
    for tid in (0, 1):
        for start in (0, 8):  # nonoverlapping repetitions of a two-symbol motion
            pts = [point(start+i, 3.+tid*3+(i % 2)*.2, vx=1. if i % 2 else -1.) for i in range(5)]
            track = Track(tid, tid+1, 0, False, deque(pts), total_points=5)
            assert checker.check(track, start+4) == (False, None)


def test_offline_physics_windows_exclude_outside_roi_and_empty_scale_is_explicit(config):
    arr = np.array([(i,i*.1, x,0.,1.,0.,20.,x,1.) for i,x in enumerate([20., 14., 13., 12., 11.])])
    stats = SegmentStats(tracks={0:arr})
    features = track_features(stats, config)
    assert len(features.rr_all) == 2  # only [14,13,12], [13,12,11]
    assert not len(features.birth_range)  # entry is not a linked-track birth
    with pytest.raises(ValueError, match="cannot learn rr_scale"):
        fit_rr_scale(features)
