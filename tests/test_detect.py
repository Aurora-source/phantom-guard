from __future__ import annotations

import numpy as np
import pytest

from conftest import needs_data
from phantomguard.config import BASELINE_PATH, bval, load_baseline, load_config, raw_path
from phantomguard.cycles import iter_cycles
from phantomguard.detect.common import CycleResult, ObjVerdict
from phantomguard.detect.fusion import Fusion
from phantomguard.detect.pipeline import Detector, load_artifacts
from phantomguard.eval.splits import time_block_segments
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header, encode_object
from phantomguard.io.replay import ReplaySource

pytestmark = pytest.mark.skipif(not BASELINE_PATH.exists(), reason="run scripts/learn_baseline.py first")
CFG = load_config()


def B():
    return load_baseline()


def cycle_frames(i, objs, count=None, t0=None, counter0=1000, gap=332, first=3, spacing=2):
    """Frames of one well-formed cycle: objs = list of (slot, x, y, vx, vy, rcs)."""
    t = (t0 if t0 is not None else 10_000) + i * gap
    out = [Frame(CAN_ID_HEADER, build_header(len(objs) if count is None else count, counter0 + i), t)]
    for k, o in enumerate(objs):
        out.append(Frame(CAN_ID_OBJECT, encode_object(*o), t + first + spacing * k))
    return out


def clean_scene(n=12):
    # n range-sorted static objects, unique slots, integer RCS; count within the learned 11-32
    return [(i + 1, 2.0 + i, (-1.0 if i % 2 else 1.0), 0.0, 0.0, 18.0) for i in range(n)]


def run(frames, b=None):
    det = Detector(CFG, b or B())
    return list(det.run(frames))


def reasons(results):
    obj = set(r for res in results for v in res.objects for r in v.reasons)
    cyc = set(r for res in results for r in res.cycle_reasons)
    return obj, cyc


def test_well_formed_synthetic_stream_has_no_protocol_reasons():
    frames = [f for i in range(30) for f in cycle_frames(i, clean_scene())]
    obj, cyc = reasons(run(frames))
    assert not cyc
    assert not obj & {"ARRIVAL", "BURST_GAP", "RANGE_ORDER", "DUP_SLOT", "SLOT_RANGE", "FIXED_FIELD", "FRAME_LEN"}


@pytest.mark.parametrize("mutate,expect,level", [
    (lambda fr: fr[:-1], "COUNT_MISMATCH", "cycle"),  # header says 3, 2 arrive
    (lambda fr: fr + [Frame(CAN_ID_OBJECT, encode_object(1, 12.0, 0.0, 0, 0, 16.0), fr[-1].timestamp_ticks + 2)],
     "DUP_SLOT", "obj"),
    (lambda fr: fr[:-1] + [Frame(CAN_ID_OBJECT, fr[-1].data, fr[0].timestamp_ticks + 200)],
     ("ARRIVAL", "ARRIVAL_POS"), "obj"),   # legacy window rule / profile v2 burst-position rule
    (lambda fr: fr[:-1] + [Frame(CAN_ID_OBJECT, fr[-1].data, fr[-2].timestamp_ticks + 40)], "BURST_GAP", "obj"),
    (lambda fr: fr[:2] + [Frame(CAN_ID_OBJECT, encode_object(9, 1.0, 0.0, 0, 0, 20.0), fr[2].timestamp_ticks)] + fr[3:],
     "RANGE_ORDER", "obj"),
    (lambda fr: fr[:-1] + [Frame(CAN_ID_OBJECT, encode_object(0xF0, 9.0, -1.0, 0, 0, 17.0), fr[-1].timestamp_ticks)],
     "SLOT_RANGE", "obj"),
    (lambda fr: fr[:-1] + [Frame(CAN_ID_OBJECT, encode_object(3, 9.0, -1.0, 0, 0, 17.0, dyn_prop=3), fr[-1].timestamp_ticks)],
     "FIXED_FIELD", "obj"),
    (lambda fr: fr[:-1] + [Frame(CAN_ID_OBJECT, fr[-1].data[:5], fr[-1].timestamp_ticks)], "FRAME_LEN", "cycle"),
    (lambda fr: fr + [Frame(0x123, bytes(8), fr[-1].timestamp_ticks + 2)], "BAD_ID", "cycle"),
])
def test_protocol_violation_is_caught(mutate, expect, level):
    frames = []
    for i in range(12):
        fr = cycle_frames(i, clean_scene())
        frames += mutate(fr) if i == 8 else fr
    obj, cyc = reasons(run(frames))
    found = cyc if level == "cycle" else obj
    assert (set(expect) & found) if isinstance(expect, tuple) else expect in found


def test_counter_and_cadence_violations():
    frames = []
    for i in range(12):
        frames += cycle_frames(i, clean_scene(), counter0=1000 + (5 if i >= 8 else 0))
    _, cyc = reasons(run(frames))
    assert "COUNTER" in cyc
    frames = []
    for i in range(12):
        frames += cycle_frames(i, clean_scene(), t0=10_000 + (150 if i >= 8 else 0))
    _, cyc = reasons(run(frames))
    assert "CADENCE" in cyc


def test_value_checks_rcs_grid_and_speed():
    objs = clean_scene()
    bad = [objs[0], (2, 6.0, 1.0, 0.0, 0.0, 19.5), (3, 9.0, -1.0, 7.0, 0.0, 17.0)]
    frames = [f for i in range(5) for f in cycle_frames(i, bad)]
    obj, _ = reasons(run(frames))
    assert {"RCS_GRID", "SPEED"} <= obj


def test_fusion_m_of_n_and_hard_reasons():
    cfg = dict(CFG, fusion={"m": 3, "n": 5})
    fus = Fusion(cfg, ("protocol", "kinematic", "replay", "learned"))
    seq = [["RR_RESID"], [], ["RR_RESID"], ["RR_RESID"], []]
    alerts = []
    for rs in seq:
        v = ObjVerdict(0, 1, 0, 0, 0, 0, True, False, track_id=7, reasons=list(rs))
        fus.apply(CycleResult(0, 0, [v]))
        alerts.append(v.alert)
    assert alerts == [False, False, False, True, True]
    v = ObjVerdict(0, 1, 0, 0, 0, 0, True, False, track_id=8, reasons=["DUP_SLOT"])
    fus.apply(CycleResult(0, 0, [v]))
    assert v.alert
    # disabled layer is ignored
    fus2 = Fusion(cfg, ("protocol",))
    v = ObjVerdict(0, 1, 0, 0, 0, 0, True, False, track_id=9, reasons=["SPEED"])
    fus2.apply(CycleResult(0, 0, [v]))
    assert not v.flagged and not v.alert


@needs_data
def test_clean_validation_has_no_protocol_reasons():
    cfg = CFG
    for seg in time_block_segments(cfg)["val"]:
        res = run(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)))
        obj, cyc = reasons(res)
        protocol = {"ARRIVAL", "BURST_GAP", "RANGE_ORDER", "DUP_SLOT", "SLOT_RANGE", "FIXED_FIELD", "FRAME_LEN"}
        assert not cyc, (seg.file, cyc)
        assert not obj & protocol, (seg.file, obj & protocol)


@needs_data
@pytest.mark.slow
def test_latency_p99_under_budget():
    cfg = CFG
    ae, lib = load_artifacts("timeblock")
    if ae is None:
        pytest.skip("run scripts/train.py first")
    seg = time_block_segments(cfg)["test"][3]  # chaotic scene: most objects and tracks
    det = Detector(cfg, B(), ae, lib)
    lat = [r.latency_ms for r in det.run(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)))]
    p99 = float(np.percentile(lat, 99))
    print(f"latency over {len(lat)} cycles: p50 {np.percentile(lat, 50):.3f} ms, p99 {p99:.3f} ms, max {max(lat):.3f} ms")
    assert p99 < cfg["latency"]["p99_budget_ms"]


def test_learned_layer_only_corroborates():
    cfg = {**CFG, "fusion": {"m": 1, "n": 1, "learned_alone": False}}
    fus = Fusion(cfg, ("protocol", "kinematic", "replay", "learned"))
    alone = ObjVerdict(0, 1, 0, 0, 0, 0, True, False, track_id=1, reasons=["LEARNED"])
    backed = ObjVerdict(1, 2, 0, 0, 0, 0, True, False, track_id=2, reasons=["LEARNED", "RCS_STD"])
    fus.apply(CycleResult(0, 0, [alone, backed]))
    assert not alone.flagged and not alone.alert and alone.reasons == ["LEARNED"]  # kept for display
    assert backed.flagged and backed.alert
    # with learned_alone the old behaviour returns
    fus2 = Fusion({**CFG, "fusion": {"m": 1, "n": 1, "learned_alone": True}}, ("learned",))
    v = ObjVerdict(0, 1, 0, 0, 0, 0, True, False, track_id=3, reasons=["LEARNED"])
    fus2.apply(CycleResult(0, 0, [v]))
    assert v.flagged and v.alert


def test_effective_cfg_takes_calibrated_mn():
    from phantomguard.config import effective_cfg

    assert effective_cfg(CFG, {}) is CFG
    eff = effective_cfg(CFG, {"fusion_mn": {"value": [4, 6]}})
    assert (eff["fusion"]["m"], eff["fusion"]["n"]) == (4, 6)
    assert eff["fusion"]["learned_alone"] == CFG["fusion"]["learned_alone"]
    assert CFG["fusion"]["m"] == 3  # original untouched
