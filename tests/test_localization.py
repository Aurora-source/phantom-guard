"""Evaluator-only lineage, exact localization and paired clean/attacked measurement."""

from __future__ import annotations

import csv
import json
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import needs_data
from phantomguard.attack.injector import MixedSource
from phantomguard.attack.scenarios import Instance, ObjPlan, plan_run
from phantomguard.config import load_baseline, load_config, raw_path
from phantomguard.detect.common import LAYERS
from phantomguard.eval.attack_adapter import attack_source
from phantomguard.eval.localize import (ControlAlerts, alert_episodes, control_alerts, localization_metrics,
                                        read_lineage, replay_lineage)
from phantomguard.eval.metrics import AttackLabel, LiteCycle, LiteVerdict
from phantomguard.eval.splits import time_block_segments
from phantomguard.frames import encode_object
from phantomguard.io.replay import ReplaySource


# ------------------------------------------------------------------ synthetic classification


def _cycle(index, objects, header_frame, cycle_alert=False):
    return LiteCycle(header_t=index * 332, cycle_reasons=[], objects=objects, cycle_alert=cycle_alert, index=index,
                     header_frame_index=header_frame,
                     frames=[SimpleNamespace(frame_index=header_frame, timestamp_ticks=index * 332, can_id=0x60A,
                                             kind="header", reasons=())]
                     + [SimpleNamespace(frame_index=o.frame_index, timestamp_ticks=index * 332 + 3, can_id=0x60B,
                                        kind="object", reasons=tuple(o.reasons)) for o in objects])


def _verdict(fi, track, alert, reasons=()):
    return LiteVerdict(track_id=track, in_roi=True, moving=False, reasons=list(reasons), flagged=bool(reasons),
                       alert=alert, frame_index=fi)


def _synthetic(alert_forged: bool, real_alert: bool, coincident: bool):
    """Two cycles; frame 2 is forged, frame 1 real. Control alerts the source of frame 1 iff coincident."""
    cfg = deepcopy(load_config())
    cycles = [_cycle(0, [_verdict(1, 1, real_alert, ["RCS_BAND"] if real_alert else []),
                         _verdict(2, 9, alert_forged, ["RR_RESID"] if alert_forged else [])], 0),
              _cycle(1, [], 3)]
    labels = [AttackLabel(0, False), AttackLabel(1, False), AttackLabel(2, True, "0", "T4", "A3"), AttackLabel(3, False)]
    lineage = [{"frame_index": 0, "kind": "header", "source_cycle": 10, "source_obj": -1, "source_slot": None, "attack_id": ""},
               {"frame_index": 1, "kind": "object", "source_cycle": 10, "source_obj": 0, "source_slot": 5, "attack_id": ""},
               {"frame_index": 2, "kind": "forged", "source_cycle": None, "source_obj": None, "source_slot": None, "attack_id": "0"},
               {"frame_index": 3, "kind": "header", "source_cycle": 11, "source_obj": -1, "source_slot": None, "attack_id": ""}]
    control = ControlAlerts(object_alerts=frozenset({(10, 0)}) if coincident else frozenset(), cycles=2)
    # Fusion must not rewrite these hand-set alerts: M=N=1 over a flagged vote reproduces them.
    cfg["fusion"] = {**cfg["fusion"], "m": 1, "n": 1, "learned_alone": True}
    for c in cycles:
        for v in c.objects:
            v.flagged = bool(v.reasons)
    return cycles, labels, lineage, control, cfg


@pytest.mark.parametrize("alert_forged,real_alert,coincident,status", [
    (True, False, False, "exact"), (True, True, False, "exact"),
    (False, True, False, "excess_unlocalized"), (False, True, True, "baseline_coincident_only"),
    (False, False, False, "none")])
def test_paired_instance_status_separates_exact_excess_and_baseline_coincident(alert_forged, real_alert, coincident, status):
    cycles, labels, lineage, control, cfg = _synthetic(alert_forged, real_alert, coincident)
    res = localization_metrics(cycles, labels, lineage, control, cfg, LAYERS)
    assert res.instances["0"]["paired_status"] == status


def test_exact_precision_recall_and_false_suspects_use_lineage_not_indices():
    cycles, labels, lineage, control, cfg = _synthetic(True, True, False)
    row = localization_metrics(cycles, labels, lineage, control, cfg, LAYERS).row
    assert row["loc_forged_object_frames"] == 1 and row["loc_alerting_forged_frames"] == 1
    assert row["loc_exact_recall"] == 1.0
    assert row["loc_exact_precision"] == 0.5            # one forged + one real alert
    assert row["loc_excess_precision"] == 0.5           # the real alert is not in the control: false suspect
    assert row["loc_false_suspect_frames"] == 1 and row["loc_baseline_coincident_alert_frames"] == 0
    # The same real alert present in the control is baseline-coincident: it no longer lowers excess precision.
    cycles, labels, lineage, control, cfg = _synthetic(True, True, True)
    row = localization_metrics(cycles, labels, lineage, control, cfg, LAYERS).row
    assert row["loc_excess_precision"] == 1.0 and row["loc_baseline_coincident_alert_frames"] == 1
    assert row["loc_false_suspect_frames"] == 0


def test_lineage_must_cover_the_emitted_stream():
    cycles, labels, lineage, control, cfg = _synthetic(True, False, False)
    with pytest.raises(ValueError, match="lineage coverage"):
        localization_metrics(cycles, labels, lineage[:-1], control, cfg, LAYERS)


def test_alert_episodes_count_transitions_not_alerting_frames():
    v = lambda fi, alert: _verdict(fi, 7, alert)
    cycles = [_cycle(i, [v(10 + i, a)], 100 + i) for i, a in enumerate([False, True, True, False, True])]
    eps = alert_episodes(cycles)
    assert [(e["start_cycle"], e["end_cycle"]) for e in eps] == [(1, 2), (4, 4)]


# ------------------------------------------------------------------ real-stream lineage


@needs_data
def test_replay_lineage_covers_every_clean_frame_in_order():
    cfg = load_config()
    seg = next(s for s in time_block_segments(cfg)["test"] if s.file == "multiplePeopleChaotic.csv")
    source = ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi))
    frames, lineage = list(source), replay_lineage(source)
    assert [r["frame_index"] for r in lineage] == list(range(len(frames)))
    for fr, row in zip(frames, lineage):
        if row["kind"] == "object":
            cycle = source.cycles[row["source_cycle"]]
            assert cycle.objects[row["source_obj"]][1] == fr.data


@needs_data
@pytest.mark.parametrize("attack,level,kw", [("T1", "A3", {"motion_case": "moving"}), ("T4", "A3", {}),
                                             ("T2", "A2", {"motion_case": "static"})])
def test_attacked_lineage_matches_recorded_frames_and_marks_forged_and_replaced(tmp_path, attack, level, kw):
    cfg, base = load_config(), None
    base = load_baseline(cfg=cfg)
    sp = time_block_segments(cfg)
    seg = next(s for s in sp["test"] if s.file == "multiplePeopleChaotic.csv")
    clean = ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi))
    path = tmp_path / "labels.csv"
    frames = list(attack_source(ReplaySource(raw_path(cfg, seg.file), (seg.lo, seg.hi)), cfg, base, attack_type=attack,
                                level=level, seed=11, train_segments=sp["train"], labels_path=path, **kw))
    lineage = read_lineage(path.with_suffix(".lineage.csv"))
    labels = {int(r["frame_index"]): r for r in csv.DictReader(path.open(newline=""))}
    assert [r["frame_index"] for r in lineage] == list(range(len(frames)))
    kinds = {r["kind"] for r in lineage}
    assert "forged" in kinds or "replacement" in kinds
    for fr, row in zip(frames, lineage):
        lab = labels[row["frame_index"]]
        if row["kind"] == "object":
            assert lab["is_attack"] in {"0", ""} or lab["is_attack"] == "0"
            assert clean.cycles[row["source_cycle"]].objects[row["source_obj"]][1] == fr.data  # same recorded bytes
        elif row["kind"] == "forged":
            assert lab["is_attack"] == "1" and row["source_cycle"] is None
        elif row["kind"] == "replacement":
            assert lab["is_attack"] == "1"
            overwritten = clean.cycles[row["source_cycle"]].objects[row["source_obj"]][1]
            assert overwritten[0] == fr.data[0]  # a replacement keeps the overwritten object's slot
    real = [r for r in lineage if r["kind"] == "object"]
    originals = sum(len(c.objects) for c in clean.selected())
    replaced = sum(r["kind"] == "replacement" for r in lineage)
    assert len(real) + replaced == originals           # every recorded object is either kept or overwritten
    lifecycle = json.loads(path.with_suffix(".instances.json").read_text())
    assert lifecycle["planner_version"] == 2 and lifecycle["lineage_version"] == 1


# ------------------------------------------------------------------ planner lifecycle


def _planner_ctx(cfg, rng_seed=11):
    return SimpleNamespace(cfg=cfg, rng=np.random.default_rng(rng_seed), seed=rng_seed, last_failure=None)


def test_retries_leave_the_version1_prefix_unchanged_and_log_reasons(monkeypatch):
    import phantomguard.attack.scenarios as scenarios
    cfg = deepcopy(load_config())
    cfg["attack"]["instances_per_run"] = 6
    state = {"calls": 0}

    def fake(ctx, atype, aid, c0, cycles, lo):
        state["calls"] += 1
        if state["calls"] == 2:          # the second schedule index fails on its first attempt only
            ctx.last_failure = "scene_infeasible"
            return None
        return Instance(aid, atype, "A2", c0, c0 + 29, [ObjPlan(1, True, {c: (3, 0, 0, 0, 20) for c in range(c0, c0 + 30)})])

    monkeypatch.setattr(scenarios, "plan_instance", fake)
    cfg0, cfg6 = deepcopy(cfg), deepcopy(cfg)
    cfg0["attack"]["planner_retries"], cfg6["attack"]["planner_retries"] = 0, 6
    c0, c6 = _planner_ctx(cfg0), _planner_ctx(cfg6)
    state["calls"] = 0
    v1 = plan_run(c0, "T4", [], 0, 826)
    state["calls"] = 0
    v2 = plan_run(c6, "T4", [], 0, 826)
    assert len(v2) > len(v1)                             # the retried instance is added; nothing is lost
    assert [(i.c0, i.c1) for i in v1[:1]] == [(i.c0, i.c1) for i in v2[:1]]   # prefix before the failure unchanged
    assert [e["attempts"] for e in c6.plan_log][:3] == [1, 2, 1]
    assert [e["outcome"] for e in c0.plan_log].count("planner_exhausted") == 1


def test_no_source_material_is_not_retried_and_is_counted_separately(monkeypatch):
    import phantomguard.attack.scenarios as scenarios
    cfg = deepcopy(load_config())
    cfg["attack"]["planner_retries"] = 6
    attempts = []

    def none(ctx, *a):
        attempts.append(1)
        ctx.last_failure = "no_source_material"
        return None

    monkeypatch.setattr(scenarios, "plan_instance", none)
    ctx = _planner_ctx(cfg)
    assert plan_run(ctx, "T3", [], 0, 826) == []
    assert len(attempts) == cfg["attack"]["instances_per_run"]      # one attempt per scheduled instance
    assert {e["outcome"] for e in ctx.plan_log} == {"no_source_material"}


def test_slot_capacity_drops_the_instance_not_the_run_and_releases_reservations():
    cfg = load_config()
    ctx = SimpleNamespace(cfg=cfg, slot_max=1, slot_p=np.array([0.5, 0.5]))
    real = SimpleNamespace(objects=[(3, encode_object(0, 3, 0, 0, 0, 20))])
    base = SimpleNamespace(name="cap", cycles=[real, real])
    only_slot_1 = lambda: Instance(0, "T1", "A2", 0, 1, [ObjPlan(1, False, {0: (3, 0, 0, 0, 20), 1: (3, 0, 0, 0, 20)})])
    greedy = Instance(1, "T1", "A2", 0, 1, [ObjPlan(1, False, {0: (3, 0, 0, 0, 20), 1: (3, 0, 0, 0, 20)})])
    mixed = MixedSource(base, ctx, [only_slot_1(), greedy], on_slot_exhausted="drop")
    assert [i.attack_id for i in mixed.instances] == [0]
    assert mixed.dropped and mixed.dropped[0]["reason"] == "slot_capacity" and mixed.dropped[0]["attack_id"] == 1
    # the exhausted-slot failure remains explicit when dropping is not requested
    from phantomguard.eval.attack_adapter import UnsupportedAttack
    with pytest.raises(UnsupportedAttack):
        MixedSource(base, ctx, [only_slot_1(), Instance(1, "T1", "A2", 0, 1, [ObjPlan(1, False, {0: (3, 0, 0, 0, 20)})])])
