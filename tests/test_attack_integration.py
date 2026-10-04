"""Accepted attacker integration; labels remain outside the detector."""
from copy import deepcopy
import csv
import json
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import needs_data
from phantomguard.attack.injector import AttackedSource, MixedSource
from phantomguard.attack.scenarios import Instance, ObjPlan, create_attacker, make_context, plan_instance
from phantomguard.attack.scenarios import plan_run
from phantomguard.attack.pools import build_pools, stream_pools
from phantomguard.config import load_baseline, load_config, raw_path
from phantomguard.detect.pipeline import Detector
from phantomguard.eval.attack_adapter import UnsupportedAttack, attack_source
from phantomguard.eval.metrics import align_labels, lite, parse_labels
from phantomguard.eval.splits import time_block_segments
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header, decode_object, encode_object
from phantomguard.io.replay import ReplaySource


@pytest.fixture
def recorded():
    cfg = deepcopy(load_config())
    cfg["attack"]["instances_per_run"] = 1
    cfg["attack"]["T1"]["count"] = [1, 1]
    base = load_baseline()
    split = time_block_segments(cfg)
    victim = next(s for s in split["test"] if s.file == "multiplePeopleChaotic.csv")
    return cfg, base, split, victim


@needs_data
@pytest.mark.parametrize("motion", ["static", "moving"])
def test_real_source_complete_labels_and_exact_object_join(recorded, tmp_path, motion):
    cfg, baseline, split, victim = recorded
    clean = ReplaySource(raw_path(cfg, victim.file), (victim.lo, victim.hi))
    path = tmp_path / "labels.csv"
    source = attack_source(clean, cfg, baseline, attack_type="T1", level="A2", seed=11,
                           train_segments=split["train"], motion_case=motion, labels_path=path)
    frames = list(source)
    labels = parse_labels(csv.DictReader(path.open(newline="")))
    cycles = [lite(c) for c in Detector(cfg, baseline).run(frames)]
    align_labels(cycles, labels)
    assert len(labels) == len(frames) and any(l.is_attack for l in labels)
    assert any(l.is_attack and frames[l.frame_index].can_id == CAN_ID_HEADER for l in labels)
    for label in labels:
        assert isinstance(frames[label.frame_index], Frame)
        assert not hasattr(frames[label.frame_index], "is_attack")
        if label.is_attack and frames[label.frame_index].can_id == CAN_ID_OBJECT:
            obj = decode_object(frames[label.frame_index].data)
            assert (obj.speed > 0) == (motion == "moving")
    lifecycle = json.loads(path.with_suffix(".instances.json").read_text())
    assert lifecycle["planned_instances"][0]["emitted"]


@needs_data
@pytest.mark.parametrize("provenance", ["training", "earlier_stream", "unseen"])
@pytest.mark.parametrize("variant", ["exact", "translated"])
def test_replay_provenance_is_explicit_and_earlier_sources_are_prefix_only(recorded, provenance, variant, monkeypatch):
    cfg, baseline, split, victim = recorded
    source = ReplaySource(raw_path(cfg, victim.file), (victim.lo, victim.hi))
    import phantomguard.attack.pools as pools
    cuts = []
    def checked(cfg, cycles, lo, hi):
        cuts.append((lo, hi))
        return stream_pools(cfg, cycles, lo, hi)
    monkeypatch.setattr(pools, "stream_pools", checked)
    attacker = create_attacker(cfg, baseline, attack_type="T3", level="A4", seed=22,
                              train_segments=split["train"], replay_provenance=provenance,
                              unseen_segments=[s for s in split["test"] if s.file != victim.file],
                              motion_case="moving", replay_variant=variant)
    instances = attacker.plan(source)
    if provenance == "earlier_stream":
        assert cuts and all(lo == victim.lo and hi < victim.hi for lo, hi in cuts)
        assert all(i.c0 >= cuts[0][1] for i in instances)
    else:
        assert not cuts
    assert instances and all(len(i.objects[0].per_cycle) >= cfg["attack"]["T3"]["min_len"] for i in instances)
    assert all(variant in i.note for i in instances)


@needs_data
def test_t4_a1_duplicates_original_slot_and_a2_overwrites_it(recorded):
    cfg, baseline, split, victim = recorded
    pools = build_pools(cfg, split["train"])
    base = ReplaySource(raw_path(cfg, victim.file), (victim.lo, victim.hi))
    for level in ("A1", "A2"):
        ctx = make_context(cfg, baseline, level, np.random.default_rng(11), pools, None)
        instance = plan_instance(ctx, "T4", 0, victim.lo, base.cycles, victim.lo)
        assert instance is not None
        original_slot = instance.objects[0].slot_pref
        mixed = MixedSource(base, ctx, [instance])
        frames = list(mixed)
        labels = {l.frame_index for l in mixed.labels}
        assert labels
        assert all(decode_object(frames[i].data).slot == original_slot for i in labels)
        from phantomguard.cycles import iter_cycles
        attacked_cycles = [c for c in iter_cycles(frames) if any(o.frame_index in labels for o in c.objects)]
        for c in attacked_cycles:
            assert sum(o.obj.slot == original_slot for o in c.objects) == (2 if level == "A1" else 1)


def test_generic_source_preserves_malformed_header_unknown_id_and_complete_indices(tmp_path):
    cfg = load_config()
    ctx = SimpleNamespace(cfg=cfg, baseline={"burst_gap_lo": 2}, level=SimpleNamespace(fix_header=False, order_aware=False),
                          slot_max=3, slot_p=np.ones(4)/4)
    original = [Frame(CAN_ID_HEADER, b"\x01", 0), Frame(0x123, b"\x01", 1),
                Frame(CAN_ID_OBJECT, b"\x02", 3), Frame(CAN_ID_HEADER, build_header(1, 1), 332),
                Frame(CAN_ID_OBJECT, encode_object(1, 3, 0, 0, 0, 20), 335)]
    attacker = SimpleNamespace(context=ctx, seed=11, run_index=0, plan=lambda source: [])
    path = tmp_path / "labels.csv"
    source = AttackedSource(iter(original), attacker, path)
    assert list(source) == original
    labels = parse_labels(csv.DictReader(path.open(newline="")))
    assert [l.frame_index for l in labels] == list(range(len(original)))
    assert not any(l.is_attack for l in labels)
    assert list(source) == original and len(source.labels) == len(original)


def test_exhausted_stable_slots_fail_explicitly():
    cfg = load_config()
    ctx = SimpleNamespace(slot_max=0, slot_p=np.array([1.]))
    base = SimpleNamespace(name="capacity", cycles=[SimpleNamespace(objects=[(3,b"\x00"*8)])])
    instance = Instance(0, "T2", "A2", 0, 0, [ObjPlan(0, False, {0:(3,0,0,0,20)})])
    with pytest.raises(UnsupportedAttack, match="stable free slot"):
        MixedSource(base, ctx, [instance])


def test_timing_naive_insertion_serialises_the_bus_and_delays_following_frames():
    ctx = SimpleNamespace(baseline={"burst_gap_lo": {"value": 2}},
                          level=SimpleNamespace(order_aware=False), sample_offset_window=lambda: 3)
    mixed = object.__new__(MixedSource)
    mixed.ctx = ctx
    ctx.level.timing_in_window = True
    raw = encode_object(1, 3, 0, 0, 0, 20)
    reals = [[3,raw,1,False,None], [5,raw,1,False,None]]
    fabs = [[None,raw,2,True,(0,"T1","A1"),None,None]]
    output = mixed._assemble(None, 0, True, reals, fabs)
    assert [row[0] for row in output] == [3,5,7]
    assert output[1][3] and not output[2][3]  # inserted frame delays the following genuine frame


@needs_data
def test_naive_attacker_preserves_final_arrival_order_across_headers(recorded, tmp_path):
    cfg, baseline, split, victim = recorded
    cfg["attack"]["T1"]["count"] = [3,3]
    source = attack_source(ReplaySource(raw_path(cfg, victim.file), (victim.lo, victim.hi)), cfg, baseline,
                           attack_type="T1", level="A0", seed=22, train_segments=split["train"],
                           labels_path=tmp_path / "labels.csv")
    frames = list(source)
    assert all(b.timestamp_ticks >= a.timestamp_ticks for a,b in zip(frames, frames[1:]))


def test_scheduler_keeps_configured_clean_gap_and_never_overlaps_t4_overwrites(monkeypatch):
    import phantomguard.attack.scenarios as scenarios
    cfg = load_config()
    cfg["attack"]["instances_per_run"] = 6
    ctx = SimpleNamespace(cfg=cfg, rng=np.random.default_rng(11))
    def instance(ctx, atype, aid, c0, cycles, lo):
        # A live target can start later than the requested anchor.
        start = c0+50
        return Instance(aid, atype, "A2", start, start+79,
                        [ObjPlan(1, True, {c:(3,0,0,0,20) for c in range(start,start+80)})])
    monkeypatch.setattr(scenarios, "plan_instance", instance)
    instances = plan_run(ctx, "T4", [], 0, 826)
    assert instances
    assert all(b.c0-a.c1-1 >= cfg["attack"]["min_gap_cycles"] for a,b in zip(instances,instances[1:]))
    cycles = [c for i in instances for o in i.objects for c in o.per_cycle]
    assert len(cycles) == len(set(cycles))
