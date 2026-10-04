from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from phantomguard.config import REPO_ROOT, load_config
from phantomguard.detect.common import CycleResult, FrameRecord, ObjVerdict
from phantomguard.eval.attack_adapter import (AttackUnavailable, UnsupportedAttack, attack_source,
                                             check_available, support_reason)
from phantomguard.eval.metrics import (AttackLabel, align_labels, assembly_stats, attack_metrics,
                                       latency_stats, lite, parse_labels)
from phantomguard.eval.report import write_report
from phantomguard.eval.splits import Segment, loso_folds, time_block, time_block_segments
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame


def fixture_cycles(object_reasons=(), cycle_reasons=()):
    v1 = ObjVerdict(1, 7, 3, 0, 0, 0, True, False, track_id=10, scores={"ae": .1, "iforest": .2},
                    timestamp_ticks=3)
    # A duplicate slot is deliberately not the matching key.
    v2 = ObjVerdict(2, 7, 4, 0, 1, 0, True, True, track_id=11, reasons=list(object_reasons),
                    scores={"ae": .8, "iforest": .9}, timestamp_ticks=5)
    r = CycleResult(0, 0, [v1, v2], list(cycle_reasons), frames=[
        FrameRecord(0, 0, CAN_ID_HEADER, "header"), FrameRecord(1, 3, CAN_ID_OBJECT, "object"),
        FrameRecord(2, 5, CAN_ID_OBJECT, "object")], header_frame_index=0, closed_t=332,
        assembly_delay_ticks=332, latency_ms=1.2)
    last = CycleResult(1, 332, [], frames=[FrameRecord(3, 332, CAN_ID_HEADER, "header")], header_frame_index=3)
    labels = [AttackLabel(0, False), AttackLabel(1, False), AttackLabel(2, True, "instance", "T1", "A2"),
              AttackLabel(3, False)]
    return [lite(r), lite(last)], labels


def test_compact_records_preserve_identity_scores_timing_and_flags():
    cycles, _ = fixture_cycles()
    v = cycles[0].objects[1]
    assert (v.frame_index, v.timestamp_ticks, v.slot, v.scores) == (2, 5, 7, {"ae": .8, "iforest": .9})
    assert [f.frame_index for f in cycles[0].frames] == [0, 1, 2]
    assert cycles[0].closed_t == 332


def test_final_index_join_duplicate_slots_and_header_detection_separate():
    cycles, labels = fixture_cycles(cycle_reasons=("COUNT_MISMATCH",))
    row, instances = attack_metrics(cycles, labels, load_config(), ("protocol",))
    assert row["attack_instance_detection_rate"] == 1
    assert row["object_detection_rate"] == 0
    assert instances[0]["ttd_cycles"] == 0
    assert instances[0]["ttd_seconds"] == pytest.approx((332-5)*1e-4)
    assert not instances[0]["right_censored"]


def test_direct_object_detection_and_comparable_auroc():
    cycles, labels = fixture_cycles(object_reasons=("DUP_SLOT",))
    row, instances = attack_metrics(cycles, labels, load_config(), ("protocol",))
    assert row["identified_object_frames"] == 1
    assert row["object_detection_rate"] == 1
    assert row["ae_auroc"] == row["iforest_auroc"] == 1
    assert row["static_forged_roi_objects"] == 0
    assert row["moving_forged_roi_objects"] == 1
    assert instances[0]["identified"]


def test_reused_run_scores_and_mutable_ablation_match_independent_scoring():
    cycles, labels = fixture_cycles(object_reasons=("DUP_SLOT",))
    cfg, cache = load_config(), {}
    original = [v.alert for c in cycles for v in c.objects]
    for layers in (("protocol",), ("learned",), ("kinematic",), ("protocol", "learned")):
        expected = attack_metrics(cycles, labels, cfg, layers)
        assert [v.alert for c in cycles for v in c.objects] == original
        actual = attack_metrics(cycles, labels, cfg, layers, copy_records=False, score_cache=cache)
        assert actual == expected
        original = [v.alert for c in cycles for v in c.objects]


def test_generated_matrix_pools_denominators_instead_of_averaging_rates():
    from phantomguard.eval.report import aggregate_runs
    rows = [dict(status="ok", split="timeblock", attack_instances=n, detected_instances=d,
                 identified_instances=d, forged_object_frames=n, identified_object_frames=d,
                 undetected_instances=n-d, right_censored_instances=0, p99_ms=1,
                 latency_budget_met=True, ae_auroc=.7) for n,d in ((1,1),(9,0))]
    aggregate = aggregate_runs(rows, [], ("split",))[0]
    assert aggregate["attack_instance_detection_rate"] == .1
    assert aggregate["object_detection_rate"] == .1
    assert aggregate["ae_mean_run_auroc"] == .7


def test_forged_header_does_not_create_a_forged_object_denominator():
    cycles, labels = fixture_cycles(cycle_reasons=("COUNT_MISMATCH",))
    labels = [AttackLabel(0, True, "h", "T2", "A2")] + [AttackLabel(i, False) for i in (1, 2, 3)]
    row, instances = attack_metrics(cycles, labels, load_config(), ("protocol",))
    assert row["attack_instances"] == 1 and row["detected_instances"] == 1
    assert row["forged_object_frames"] == 0 and row["object_detection_rate"] is None
    assert instances[0]["motion"] == "unknown"


def test_undetected_and_eof_censored_remain_in_denominator():
    cycles, labels = fixture_cycles()
    row, instances = attack_metrics(cycles[:1], labels[:3], load_config(), ("protocol",))
    assert row["attack_instance_detection_rate"] == 0
    assert row["undetected_instances"] == row["right_censored_instances"] == 1
    assert instances[0]["ttd_cycles"] is None and instances[0]["ttd_seconds"] is None
    assert row["ae_auroc"] == 1  # scores exist even with no alert at the fixed threshold


def test_malformed_objects_keep_denominator_without_motion_class():
    cfg = load_config()
    v = ObjVerdict(1, None, None, None, None, None, False, False, reasons=["FRAME_LEN"])
    cycle = lite(CycleResult(0, 0, [v], frames=[FrameRecord(0, 0, CAN_ID_HEADER, "header"),
                                              FrameRecord(1, 3, CAN_ID_OBJECT, "malformed")]))
    labels = [AttackLabel(0, False), AttackLabel(1, True, "bad", "T1", "A0")]
    row, instances = attack_metrics([cycle], labels, cfg, ("protocol",))
    assert row["forged_object_frames"] == row["identified_object_frames"] == 1
    assert instances[0]["motion"] == "unknown"
    assert row["static_forged_roi_objects"] == row["moving_forged_roi_objects"] == 0
    assert row["ae_auroc"] is None and row["ae_valid_scores"] == 0


@pytest.mark.parametrize("which", ["missing", "extra", "duplicate"])
def test_bad_label_coverage_fails_explicitly(which):
    cycles, labels = fixture_cycles()
    bad = labels[:-1] if which == "missing" else labels + [AttackLabel(4 if which == "extra" else 2, False)]
    with pytest.raises(ValueError, match="coverage|duplicate"):
        align_labels(cycles, bad)


def test_label_parser_validates_metadata_and_flags():
    row = dict(frame_index="2", is_attack="1", attack_id="a", attack_type="T4", level="A2")
    assert parse_labels([row])[0] == AttackLabel(2, True, "a", "T4", "A2")
    with pytest.raises(ValueError, match="invalid is_attack"):
        parse_labels([dict(row, is_attack="maybe")])
    with pytest.raises(ValueError, match="needs"):
        parse_labels([dict(row, attack_id="")])
    with pytest.raises(ValueError, match="columns"):
        parse_labels([{"slot": 7}])


def test_empty_latency_and_assembly_delay_are_explicit():
    assert latency_stats([])["p99_ms"] is None
    cycles, _ = fixture_cycles()
    result = assembly_stats(cycles, load_config())
    assert result["assembly_p99_ms"] == pytest.approx(33.2)
    assert result["assembly_eof_unmeasured"] == 1


def test_unseen_attack_material_cannot_overlap_defender_training():
    with pytest.raises(ValueError, match="overlap"):
        attack_source([], load_config(), {}, attack_type="T3", level="A4", seed=11,
                      train_segments=[Segment("test.csv", 0, 60)], unseen_segments=[Segment("test.csv", 59, 80)],
                      replay_provenance="unseen")
    assert support_reason("T3", "A2") and support_reason("T3", "A3") is None


def test_provider_receives_no_labels_as_detector_frames(monkeypatch, tmp_path):
    import phantomguard.eval.attack_adapter as adapter
    frames = [Frame(CAN_ID_HEADER, bytes(5), 0), Frame(CAN_ID_OBJECT, bytes(8), 3)]
    captured = {}
    def fixture_provider(source, cfg, baseline, **context):
        captured.update(context)
        return iter(source)
    monkeypatch.setattr(adapter, "check_available", lambda provider: provider)
    monkeypatch.setattr(adapter, "_provider", lambda provider: fixture_provider)
    source = attack_source(frames, load_config(), {}, attack_type="T1", level="A2", seed=11,
                           provider="fixture:factory", labels_path=tmp_path / "labels.csv",
                           train_segments=[Segment("a.csv", 0, 60)])
    assert list(source) == frames
    assert captured["train_segments"] == (Segment("a.csv", 0, 60),)
    assert not hasattr(frames[0], "is_attack")
    monkeypatch.setattr(adapter, "_provider", lambda provider: lambda *a, **k: iter([{"frame": frames[0], "label": True}]))
    with pytest.raises(TypeError, match="ordinary Frame"):
        list(attack_source(frames, load_config(), {}, attack_type="T1", level="A2", seed=11,
                           provider="fixture:factory"))


def test_blocked_report_contains_no_claimed_attack_performance(tmp_path):
    manifest = {"blockers": ["Phase 2 missing"]}
    summary = write_report(tmp_path, manifest, [{"status": "blocked_phase2"}], [], [], [])
    text = summary.read_text(encoding="utf-8")
    assert "**unmeasured**" in text and "Completed attack runs: 0" in text
    report = json.loads((tmp_path / "attack_eval_manifest.json").read_text())
    assert report["completed_attack_runs"] == 0
    assert "ae_auroc" not in (tmp_path / "attack_eval_runs.csv").read_text()
    from phantomguard.eval.report import artifact_details
    corrupt = tmp_path / "replay_library_fixture.pkl"
    corrupt.write_bytes(b"invalid pickle")
    assert artifact_details(corrupt)["status"] == "invalid"


def test_fixed_splits_do_not_overlap_or_cross_scenarios(monkeypatch):
    import phantomguard.eval.splits as splits
    monkeypatch.setattr(splits, "n_cycles", lambda cfg, name: 100)
    assert time_block(100, .6, .2) == {"train": (0, 60), "val": (60, 80), "test": (80, 100)}
    cfg = load_config()
    tb = time_block_segments(cfg)
    for train, val, test in zip(tb["train"], tb["val"], tb["test"]):
        assert train.file == val.file == test.file
        assert train.hi == val.lo and val.hi == test.lo
    folds = loso_folds(cfg)
    assert len(folds) == 4
    for held, fold in folds.items():
        assert all(s.file != held for s in fold["train"] + fold["val"])
        assert {s.file for s in fold["test"]} == {held}


def test_evaluator_seed_repetitions_are_reproducible_and_distinct():
    spec = importlib.util.spec_from_file_location("attack_eval_script", REPO_ROOT / "scripts/run_attack_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = load_config()
    context = {"split": "timeblock", "fold": "timeblock", "tag": "timeblock", "part": "test"}
    jobs = module.attack_jobs(cfg, context, Segment("file.csv", 80, 100))
    again = module.attack_jobs(cfg, context, Segment("file.csv", 80, 100))
    assert jobs == again
    first_cell = [job for job in jobs if job["attack_type"] == "T1" and job["level"] == "A0"
                  and job["motion_case"] == "static"]
    assert len(first_cell) == len(cfg["attack"]["seeds"]) * cfg["eval"]["runs_per_cell"]
    assert len({job["effective_seed"] for job in first_cell}) == len(first_cell)


def test_duplicate_linked_alerts_count_one_episode_and_unlinked_alerts_stay_distinct():
    from phantomguard.eval.metrics import score
    cycles, _ = fixture_cycles(object_reasons=("DUP_SLOT",))
    cycles[0].objects[0].track_id = cycles[0].objects[1].track_id
    cycles[0].objects[0].reasons = ["DUP_SLOT"]
    metric = score(cycles, load_config(), ("protocol",))
    assert metric.track_alert_events == 1
    for verdict in cycles[0].objects:
        verdict.track_id = None
        verdict.reasons = ["FRAME_LEN"]
    metric = score(cycles, load_config(), ("protocol",))
    assert metric.track_alert_events == 2


def test_attacker_cannot_mutate_defender_configuration(monkeypatch):
    import phantomguard.eval.attack_adapter as adapter
    cfg, baseline = load_config(), {"rr_scale": {"value": .8}}
    def malicious_fixture(source, cfg, baseline, **context):
        cfg["fusion"]["m"] = 1
        baseline["rr_scale"]["value"] = 50
        return source
    monkeypatch.setattr(adapter, "check_available", lambda provider: provider)
    monkeypatch.setattr(adapter, "_provider", lambda provider: malicious_fixture)
    list(attack_source([], cfg, baseline, attack_type="T1", level="A2", seed=11, provider="fixture:factory"))
    assert cfg["fusion"]["m"] == 3 and baseline["rr_scale"]["value"] == .8


def test_object_verdicts_cannot_silently_drop_or_duplicate_frame_indices():
    cycles, labels = fixture_cycles()
    cycles[0].objects.append(cycles[0].objects[0])
    with pytest.raises(ValueError, match="object verdicts"):
        align_labels(cycles, labels)


def test_attack_runner_end_to_end_with_test_only_source(tmp_path, monkeypatch):
    """Interface verification only: this deterministic fixture is not the attacker."""
    import csv
    from types import SimpleNamespace
    from phantomguard.config import BASELINE_PATH
    from phantomguard.detect.autoencoder import NumpyAE
    from phantomguard.frames import build_header, encode_object
    import phantomguard.eval.attack_adapter as adapter

    spec = importlib.util.spec_from_file_location("fixture_attack_runner", REPO_ROOT / "scripts/run_attack_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = load_config()
    cfg["data"]["raw_dir"] = str(tmp_path)
    cfg["data"]["files"] = ["fixture.csv"]
    path = tmp_path / "fixture.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["cycle_num", "meas_counter", "obj_count_header", "sync_status",
                                                   "sync_timestamp", "obj_timestamp", "raw_len", "raw_hex"])
        writer.writeheader()
        for i in range(12):
            writer.writerow(dict(cycle_num=i+1, meas_counter=100+i, obj_count_header=1, sync_status="0x01",
                                 sync_timestamp=i*332, obj_timestamp=i*332+3, raw_len=8,
                                 raw_hex=encode_object(3, 3, 0, 0, 0, 20).hex(" ")))
    width = cfg["learned"]["window_cycles"] * 7
    ae = NumpyAE({"mean": np.zeros(width), "std": np.ones(width), "layers": [(np.eye(width), np.zeros(width))]})
    monkeypatch.setattr(module, "load_artifacts", lambda *a, **k: (ae, set()))
    monkeypatch.setattr(module, "load_iforest", lambda *a, **k: {
        "mean": np.zeros(width), "std": np.ones(width),
        "model": SimpleNamespace(score_samples=lambda z: np.zeros(len(z)))})
    monkeypatch.setattr(adapter, "check_available", lambda provider: provider)

    def test_source(source, cfg, baseline, **context):
        rows, frames = [], []
        def add(frame, attack=False):
            rows.append(dict(frame_index=len(frames), is_attack=int(attack), attack_id="fixture" if attack else "",
                             attack_type="T1" if attack else "", level="A0" if attack else ""))
            frames.append(frame)
        for frame in source:
            if frame.can_id == CAN_ID_HEADER:
                counter = int.from_bytes(frame.data[1:3], "big")
                add(Frame(frame.can_id, build_header(2, counter), frame.timestamp_ticks))
            else:
                add(frame)
                add(Frame(CAN_ID_OBJECT, b"\x09", frame.timestamp_ticks+2), True)
        with context["labels_path"].open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return frames
    monkeypatch.setattr(adapter, "_provider", lambda provider: test_source)
    context = dict(split="timeblock", part="test", fold="timeblock", tag="timeblock",
                   baseline_path=BASELINE_PATH, train=[], test=[Segment("fixture.csv", 0, 12)])
    job = dict(module.job_identity(context, context["test"][0]), attack_type="T1", level="A0", motion_case="static",
               replay_provenance="not_applicable", replay_variant="not_applicable", effective_seed=11, run=0)
    rows, instances, counts = module.run_attack((cfg, context, context["test"][0], job, "test:fixture", tmp_path))
    fused = next(row for row in rows if row.get("layers") == "all")
    assert fused["status"] == "ok" and fused["forged_object_frames"] == 12
    assert fused["identified_object_frames"] == 12 and fused["attack_instances"] == 1
    assert fused["ae_auroc"] is None and fused["iforest_auroc"] is None
    assert fused["p99_budget_ms"] == cfg["latency"]["p99_budget_ms"]
    assert counts["emitted_frames"] == 36 and counts["malformed_object_attempts"] == 12
    assert len(instances) == len(module.SUBSETS)
