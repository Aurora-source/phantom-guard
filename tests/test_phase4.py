from __future__ import annotations

import copy
import pickle
from dataclasses import replace

import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from phantomguard.config import load_baseline, load_config
from phantomguard.detect.autoencoder import (
    ArtifactError, FEATURE_NAMES, LearnedChecker, NumpyAE, build_model_metadata,
    calibrated_thresholds, load_iforest, score_iforest, track_windows, train_autoencoder,
    validate_artifact_metadata, windows_from_tracks,
)
from phantomguard.detect.common import CycleResult, ObjVerdict
from phantomguard.detect.fusion import Fusion
from phantomguard.detect.pipeline import Detector
from phantomguard.eval.splits import Segment
from phantomguard.tracks import TrackPoint
from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header, encode_object


def config():
    cfg = copy.deepcopy(load_config())
    cfg["learned"].update(window_cycles=2, hidden=4, latent=2, epochs=2, batch_size=8, max_train_seconds=10)
    return cfg


def ae(n=2):
    width = n * len(FEATURE_NAMES)
    return NumpyAE({"mean": np.zeros(width), "std": np.ones(width),
                    "layers": [(np.eye(width), np.zeros(width))]})


def points(n=9):
    return [TrackPoint(i, i * .0332, 3 + i * .02, i * .03, (-1)**i * .5, .25,
                       20 - i % 3, 3 + i * .02, .5, 90 + i) for i in range(n)]


def array(pts):
    return np.asarray([(p.cycle_index, p.t_s, p.x, p.y, p.vx, p.vy, p.rcs, p.rng, p.vr) for p in pts])


def test_offline_online_features_identical_and_identifiers_are_excluded():
    cfg, pts = config(), points()
    checker = LearnedChecker(cfg, ae(), 1, 1)
    offline, moving = track_windows(array(pts), 2, 15, .3)
    for i, window in enumerate(offline):
        current = pts[i:i + 3]
        online, online_moving = checker.window(current)
        np.testing.assert_array_equal(online, window)
        assert online_moving == moving[i]
        renamed = [replace(p, cycle_index=p.cycle_index + 800, t_s=p.t_s + 999,
                           frame_index=p.frame_index + 10000) for p in current]
        np.testing.assert_array_equal(checker.window(renamed)[0], online)


def test_learning_windows_cannot_bridge_missing_cycles_or_roi():
    cfg, pts = config(), points(7)
    checker = LearnedChecker(cfg, ae(), 1, 1)
    gap = [*pts[:2], *pts[3:]]
    offline, _ = track_windows(array(gap), 2, 15, .3)
    assert len(offline) == 2
    assert checker.window_outcome(gap[:3])[2] == "track_gap"
    outside = [replace(p, rng=16) if i == 2 else p for i, p in enumerate(pts)]
    windows, _ = track_windows(array(outside), 2, 15, .3)
    assert len(windows) == 2
    assert checker.window_outcome(outside[2:5])[2] == "outside_roi"
    assert checker.window_outcome(pts[:2])[2] == "insufficient_history"
    assert checker.window_outcome([*pts[:2], replace(pts[2], x=float("nan"))])[2] == "invalid_values"


def test_windows_reset_at_independent_track_and_scenario_boundaries():
    short = array(points(2))
    windows, _ = windows_from_tracks([short, short, short], 2, 15, .3)
    assert windows.shape == (0, 14)
    valid = array(points(4))
    a, _ = windows_from_tracks([valid, valid], 2, 15, .3)
    assert len(a) == 4  # no windows assembled from the end/start of the two recordings


def test_empty_or_incomplete_training_and_calibration_have_explicit_outcomes():
    cfg = config()
    with pytest.raises(ValueError, match="no eligible training windows"):
        train_autoencoder(np.empty((0, 14)), cfg)
    with pytest.raises(ValueError, match="no eligible moving validation windows"):
        calibrated_thresholds(np.array([.1, .2]), np.array([False, False]), .999, "AE", 2)
    with pytest.raises(ValueError, match="invalid validation scores"):
        calibrated_thresholds(np.array([np.nan]), np.array([True]), .999, "AE", 2)
    with pytest.raises(ArtifactError, match="window size"):
        LearnedChecker(cfg, ae(3), 1, 1)
    with pytest.raises(ValueError, match="must be finite"):
        LearnedChecker(cfg, ae(), float("nan"), 1)


def test_normalization_fits_only_supplied_training_windows():
    cfg = config()
    xtr = np.arange(56, dtype=float).reshape(4, 14) / 10
    model = train_autoencoder(xtr, cfg, seed=11, log=lambda _: None)
    np.testing.assert_allclose(model["mean"], xtr.mean(axis=0))
    np.testing.assert_allclose(model["std"], xtr.std(axis=0))
    learned = NumpyAE(model)
    initial_mean = learned.mean.copy()
    learned.errors(np.full((3, 14), 1000.0))
    np.testing.assert_array_equal(learned.mean, initial_mean)


def test_artifact_provenance_rejects_missing_stale_split_and_source(tmp_path):
    cfg = config()
    cfg["data"]["raw_dir"] = str(tmp_path)
    raw = tmp_path / "allowed.csv"
    raw.write_text("recording one")
    train, val = [Segment("allowed.csv", 0, 60)], [Segment("allowed.csv", 60, 80)]
    base = {"_meta": {"train_segments": [s.__dict__ for s in train], "val_segments": [s.__dict__ for s in val]},
            "reassign_jump": {"value": 1.0}}
    metadata = build_model_metadata(cfg, train, val, "timeblock", 11, base)
    base["learned_artifacts"] = {"ae": metadata}
    validate_artifact_metadata(metadata, cfg, base, "timeblock", strict=True)
    with pytest.raises(ArtifactError, match="no provenance"):
        validate_artifact_metadata(None, cfg, base, "timeblock", strict=True)
    with pytest.raises(ArtifactError, match="requested"):
        validate_artifact_metadata(metadata, cfg, base, "loso_unseen", strict=True)
    changed = copy.deepcopy(cfg)
    changed["learned"]["window_cycles"] = 3
    with pytest.raises(ArtifactError, match="configuration contract changed"):
        validate_artifact_metadata(metadata, changed, base, "timeblock", strict=True)
    stale = copy.deepcopy(base)
    stale.pop("learned_artifacts")  # learn_baseline.py rewrote the baseline after training
    with pytest.raises(ArtifactError, match="baseline provenance"):
        validate_artifact_metadata(metadata, cfg, stale, "timeblock", strict=True)
    with pytest.raises(ValueError, match="overlapping"):
        build_model_metadata(cfg, train, [Segment("allowed.csv", 59, 80)], "timeblock", 11, base)
    assert set(metadata["source_sha256"]) == {"allowed.csv"}
    raw.write_text("recording changed after training")
    with pytest.raises(ArtifactError, match="recording .* changed"):
        validate_artifact_metadata(metadata, cfg, base, "timeblock", strict=True)


def test_ae_artifact_checks_normalization_and_weights():
    width = 14
    with pytest.raises(ArtifactError, match="nonpositive"):
        NumpyAE({"mean": np.zeros(width), "std": np.zeros(width), "layers": []})
    with pytest.raises(ArtifactError, match="layer dimensions"):
        NumpyAE({"mean": np.zeros(width), "std": np.ones(width),
                 "layers": [(np.eye(width - 1), np.zeros(width - 1))]})
    with pytest.raises(ValueError, match="feature width"):
        ae().errors(np.zeros((2, 13)))


def test_iforest_batch_scores_exact_online_windows_and_reports_calibration(tmp_path):
    x, moving = track_windows(array(points()), 2, 15, .3)
    iso = IsolationForest(n_estimators=5, random_state=11).fit(x)
    artifact = {"model": iso, "mean": np.zeros(14), "std": np.ones(14)}
    expected = -iso.score_samples(x)
    np.testing.assert_array_equal(score_iforest(artifact, list(x)), expected)
    thresholds = calibrated_thresholds(np.array([.1, .2, .3, .9]),
                                       np.array([False, False, True, True]), .9, "isolation-forest anomaly", 2)
    assert thresholds["static"]["value"] == pytest.approx(.19)
    assert thresholds["moving"]["quantile"] == .9
    assert thresholds["moving"]["split"] == "val"
    assert thresholds["moving"]["n_windows"] == 2
    with pytest.raises(FileNotFoundError, match="run scripts/train.py"):
        load_iforest(models_dir=tmp_path, strict=True)
    (tmp_path / "iforest_timeblock.pkl").write_bytes(pickle.dumps(artifact))
    with pytest.raises(ArtifactError, match="no provenance"):
        load_iforest(models_dir=tmp_path, strict=True)


def verdict(tid=7, reasons=()):
    return ObjVerdict(0, 1, 3, 0, 0, 0, True, False, track_id=tid, reasons=list(reasons))


def test_fusion_counts_missing_cycles_and_retires_ended_track_state():
    cfg = config()
    cfg["fusion"] = {"m": 2, "n": 3}
    fusion = Fusion(cfg, ("protocol", "kinematic"))
    first = verdict(reasons=["RR_RESID"])
    fusion.apply(CycleResult(0, 0, [first]), active_track_ids={7})
    fusion.apply(CycleResult(1, 1, []), active_track_ids={7})
    second = verdict(reasons=["RR_RESID"])
    fusion.apply(CycleResult(2, 2, [second]), active_track_ids={7})
    assert second.alert  # [flagged, absent, flagged]
    third = verdict()
    fusion.apply(CycleResult(3, 3, [third]), active_track_ids={7})
    assert not third.alert  # [absent, flagged, clean]
    fusion.apply(CycleResult(4, 4, []), active_track_ids=set())
    assert fusion.hist == {}
    newborn = verdict(tid=8, reasons=["RR_RESID"])
    fusion.apply(CycleResult(5, 5, [newborn]), active_track_ids={8})
    assert not newborn.alert
    hard = verdict(tid=8, reasons=["DUP_SLOT"])
    fusion.apply(CycleResult(6, 6, [hard]), active_track_ids={8})
    assert hard.alert


def test_fusion_skipped_cycles_expire_votes_and_invalid_policy_is_explicit():
    cfg = config()
    cfg["fusion"] = {"m": 2, "n": 3}
    fusion = Fusion(cfg, ("kinematic",))
    fusion.apply(CycleResult(0, 0, [verdict(reasons=["RR_RESID"])]))
    late = verdict(reasons=["RR_RESID"])
    fusion.apply(CycleResult(5, 5, [late]))
    assert not late.alert
    cfg["fusion"] = {"m": 4, "n": 3}
    with pytest.raises(ValueError, match="1 <= m <= n"):
        Fusion(cfg, ("kinematic",))


def test_duplicate_track_verdicts_cast_one_vote_per_scan_independent_of_order():
    cfg = config()
    cfg["fusion"] = {"m": 3, "n": 5}
    outcomes = []
    for reverse in (False, True):
        fusion = Fusion(cfg, ("kinematic",))
        flagged, clean = verdict(reasons=["RR_RESID"]), verdict()
        duplicate = [flagged, clean, verdict(reasons=["RR_RESID"])]
        if reverse:
            duplicate.reverse()
        fusion.apply(CycleResult(0, 0, duplicate))
        assert list(fusion.hist[7]) == [True]
        assert not any(v.alert for v in duplicate)
        later = verdict(reasons=["RR_RESID"])
        fusion.apply(CycleResult(1, 1, [later]))
        assert not later.alert
        third = verdict(reasons=["RR_RESID"])
        fusion.apply(CycleResult(2, 2, [third]))
        outcomes.append(third.alert)
    assert outcomes == [True, True]


def test_pipeline_reports_unavailable_models_and_captures_exact_scored_windows():
    cfg = config()
    baseline = load_baseline()
    baseline["ae_threshold_static"] = {"value": 1.0}
    baseline["ae_threshold_moving"] = {"value": 1.0}
    frames = []
    for i in range(5):
        frames += [Frame(CAN_ID_HEADER, build_header(1, i), 10000 + i * 332),
                   Frame(CAN_ID_OBJECT, encode_object(1, 3 + i * .2, 0, -.5, 0, 20), 10003 + i * 332)]
    missing = Detector(cfg, baseline)
    no_model = list(missing.run(frames))
    assert "unavailable" in no_model[0].layer_status["learned"]
    assert no_model[0].objects[0].score_status["ae"] == "unavailable_model"
    det = Detector(cfg, baseline, ae=ae(), library=set(), capture_windows=True)
    results = list(det.run(frames))
    assert results[0].objects[0].score_status["ae"] == "insufficient_history"
    assert all(r.layer_status["learned"] == "active" for r in results)
    offline, moving = track_windows(array(list(det.tracks.active[1].points)), 2, 15, .3)
    scored = [r for r in results if r.learned_windows]
    assert len(scored) == len(offline) == 3
    for result, expected, expected_moving in zip(scored, offline, moving):
        fi = result.objects[0].frame_index
        captured, window_moving = result.learned_windows[fi]
        np.testing.assert_array_equal(captured, expected)
        assert window_moving == expected_moving
        assert result.objects[0].scores["ae"] == 0
