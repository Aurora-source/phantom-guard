"""Viewer regressions use explicit frame fixtures, never headline attacker results."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest
from PIL import Image

from phantomguard.config import REPO_ROOT, load_config
from phantomguard.detect.common import CycleResult, ObjVerdict
from phantomguard.frames import CAN_ID_OBJECT, Frame, decode_object, encode_object
from phantomguard.viz.console import COLORS, ConsoleView


@pytest.fixture
def demo():
    spec = importlib.util.spec_from_file_location("replay_demo_under_test", REPO_ROOT / "scripts" / "replay_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verdict(index=1, **kwargs):
    fields = dict(frame_index=index, slot=1, x=3.0, y=1.0, vx=0.5, vy=0.25,
                  in_roi=True, moving=True)
    fields.update(kwargs)
    return ObjVerdict(**fields)


def test_colours_use_verdicts_and_outside_roi_protocol_alerts():
    view = ConsoleView(15, 1e-4)
    try:
        clean = verdict()
        bad = verdict(2, in_roi=False, flagged=True, alert=True, reasons=["DUP_SLOT"])
        assert view._status(clean) == "ok"
        assert view._status(bad) == "alert"
        view.render(CycleResult(0, 1000, [clean, bad]))
        labels = [t.get_text() for t in view.ax.get_legend().get_texts()]
        assert "not flagged" in labels
        assert "authentic" not in labels
        assert view.ax.collections[0].get_facecolors()[0].tolist() == pytest.approx(
            matplotlib.colors.to_rgba(COLORS["ok"]))
        assert any("DUP_SLOT" in t.get_text() for t in view.ax.texts)
        assert len(view.ax.patches) >= 3  # ROI circle and velocity arrows.
        view.render(CycleResult(1, 1330, [clean]), "onePersonMovingFrontAndBack.csv [test] clean replay; "
                    "alerts are false positives (green = not flagged)")
        assert len(view.ax.get_title().splitlines()) == 3
    finally:
        plt.close(view.fig)


def test_malformed_objects_and_repeated_render_preserve_alert_log():
    view = ConsoleView(15, 1e-4)
    result = CycleResult(0, 1000, [verdict(7, slot=None, x=None, y=None, vx=None, vy=None,
                                         in_roi=False, flagged=True, alert=True, reasons=["FRAME_LEN"])],
                         ["COUNT_MISMATCH"], cycle_alert=True)
    try:
        view.render(result)
        logged = list(view.log)
        assert "frame 7 slot unknown" in logged[0]
        assert any("CYCLE: COUNT_MISMATCH" in line for line in logged)
        view.render(result)
        assert list(view.log) == logged
        assert any("CYCLE ALERT" in text.get_text() for text in view.ax.texts)
    finally:
        plt.close(view.fig)


def test_png_gif_and_lossless_alert_log_export(demo, tmp_path):
    cfg = load_config()
    results = [CycleResult(i, 1000 + i * 330, [verdict(2*i + 1, x=3+i/5,
                flagged=i == 1, alert=i == 1, reasons=["DUP_SLOT"] if i == 1 else [])]) for i in range(3)]
    paths = demo.export_replay(results, cfg, tmp_path, "fixture_T1_A2_seed11", "fixture only",
                               period_seconds=0.033, gif_cycles=3, stride=1, around_first_alert=True)
    png, gif, log = paths
    assert png.name.endswith("first_alert.png")
    with Image.open(png) as image:
        assert image.format == "PNG"
        assert image.width > 500
    with Image.open(gif) as image:
        assert image.format == "GIF"
        assert image.n_frames == 3
    with log.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["frame_index"] == "3"
    assert rows[0]["header_timestamp_ticks"] == "1330"
    assert rows[0]["reasons"] == "DUP_SLOT"
    assert not plt.get_fignums()


def test_empty_export_fails_clearly(demo, tmp_path):
    with pytest.raises(ValueError, match="empty replay clip"):
        demo.export_replay([], load_config(), tmp_path, "empty", "", period_seconds=0.033)


@pytest.mark.parametrize("args", [
    ["--attack", "T1"], ["--level", "A2"], ["--attack", "T0", "--level", "A2"],
    ["--attack", "T1", "--level", "A5"], ["--attack", "T1", "--level", "A2", "--part", "train"],
    ["--attack", "T1", "--level", "A2", "--part", "all"], ["--start", "-1"],
    ["--cycles", "-1"], ["--stride", "0"], ["--gif-cycles", "0"], ["--seed", "-1"],
    ["--provider", "fixtures:create"],
])
def test_invalid_arguments_fail_before_loading_models(demo, args):
    with pytest.raises(SystemExit) as exc:
        demo.main(args)
    assert exc.value.code == 2


@pytest.fixture
def local_replay(demo, tmp_path, monkeypatch):
    path = tmp_path / "recording.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["cycle_num", "meas_counter", "obj_count_header", "sync_status",
                                              "sync_timestamp", "obj_timestamp", "raw_len", "raw_hex"])
        writer.writeheader()
        for i in range(10):
            writer.writerow(dict(cycle_num=i+1, meas_counter=100+i, obj_count_header=1, sync_status="0x01",
                                 sync_timestamp=1000+i*330, obj_timestamp=1003+i*330, raw_len=8,
                                 raw_hex=encode_object(1, 3, 1, 0, 0, 20).hex(" ")))
    cfg = load_config()
    baseline = {"cadence_lo": 328, "cadence_hi": 336}
    monkeypatch.setattr(demo, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(demo, "load_baseline", lambda path=None: baseline)
    artifacts_calls = []

    def artifacts(tag, **kwargs):
        artifacts_calls.append((tag, kwargs))
        return object(), set()

    monkeypatch.setattr(demo, "load_artifacts", artifacts)
    consumed = []

    class FixtureDetector:
        def __init__(self, *args):
            pass

        def run(self, source):
            # Explicit test-only detector makes no claim about attack performance.
            for i, frame in enumerate(source):
                assert isinstance(frame, Frame)
                consumed.append(frame)
                if frame.can_id == CAN_ID_OBJECT:
                    obj = decode_object(frame.data)
                    yield CycleResult(i, frame.timestamp_ticks, [verdict(i, slot=obj.slot, x=obj.x, y=obj.y,
                                                                         vx=obj.vx, vy=obj.vy)])

    monkeypatch.setattr(demo, "Detector", FixtureDetector)
    return path, cfg, baseline, artifacts_calls, consumed


def test_clean_cli_uses_strict_artifacts_and_exports(demo, local_replay, tmp_path):
    path, cfg, baseline, calls, consumed = local_replay
    out = tmp_path / "exports"
    demo.main(["--file", str(path), "--export", "--cycles", "2", "--gif-cycles", "2",
               "--stride", "1", "--output-dir", str(out)])
    assert calls == [("timeblock", dict(strict=True, cfg=cfg, baseline=baseline))]
    assert len(consumed) == 4  # Two final time-block cycles, each header + object.
    assert any(p.name.endswith("clean_start0_cycles2.gif") for p in out.iterdir())


def test_interactive_cli_renders_cycles_and_paces_by_relative_time(demo, local_replay, monkeypatch):
    import matplotlib.pyplot as plt
    path, _, _, _, consumed = local_replay
    pauses = []
    monkeypatch.setattr(plt, "pause", pauses.append)
    monkeypatch.setattr(plt, "show", lambda: None)
    try:
        demo.main(["--file", str(path)])
        assert len(consumed) == 4
        assert len(pauses) == 2 and all(0 < gap <= .034 for gap in pauses)
    finally:
        plt.close("all")


def test_attack_cli_integrates_source_without_label_leakage(demo, local_replay, monkeypatch, tmp_path):
    path, cfg, baseline, _, consumed = local_replay
    adapter_calls = []
    seg = SimpleNamespace(file="permitted_training.csv", lo=0, hi=6)
    monkeypatch.setattr(demo, "time_block_segments", lambda cfg: {"train": [seg]})

    class FixtureAttackedSource:
        def __init__(self, source):
            self.source = source

        @property
        def labels(self):
            raise AssertionError("viewer/detector must not inspect attack labels")

        def __iter__(self):
            # Ordinary frames exercise integration; this is not a production attacker.
            for frame in self.source:
                yield frame
                if frame.can_id == CAN_ID_OBJECT:
                    yield Frame(CAN_ID_OBJECT, encode_object(2, 9, 1, 0, 0, 17), frame.timestamp_ticks + 2)

    def attack_source(source, cfg_arg, baseline_arg, **kwargs):
        adapter_calls.append((cfg_arg, baseline_arg, kwargs))
        return FixtureAttackedSource(source)

    monkeypatch.setitem(sys.modules, "phantomguard.eval.attack_adapter", SimpleNamespace(attack_source=attack_source))
    out = tmp_path / "exports"
    demo.main(["--file", str(path), "--attack", "T1", "--level", "A2", "--seed", "22",
               "--export", "--gif-cycles", "2", "--stride", "1", "--output-dir", str(out)])
    kwargs = adapter_calls[0][2]
    assert kwargs["train_segments"] == [seg]
    assert kwargs["replay_provenance"] == "training"
    assert kwargs["attack_type"] == "T1" and kwargs["level"] == "A2" and kwargs["seed"] == 22
    assert kwargs["labels_path"].name.endswith("T1_A2_seed22_labels.csv")
    assert consumed and any(p.name.endswith("T1_A2_seed22.gif") for p in out.iterdir())
    assert any(frame.can_id == CAN_ID_OBJECT and decode_object(frame.data).slot == 2 for frame in consumed)
    log = next(out.glob("*_alerts.csv"))
    assert not list(csv.DictReader(log.open(newline="")))  # Fixture labels never create red verdicts.


def test_missing_attacker_and_empty_recording_fail_clearly(demo, local_replay, monkeypatch, capsys, tmp_path):
    path, *_ = local_replay

    def missing(*args, **kwargs):
        raise RuntimeError("Phase 2 unavailable: attack/scenarios.py and attack/injector.py are missing")

    monkeypatch.setattr(demo, "time_block_segments", lambda cfg: {"train": []})
    monkeypatch.setitem(sys.modules, "phantomguard.eval.attack_adapter", SimpleNamespace(attack_source=missing))
    with pytest.raises(SystemExit):
        demo.main(["--file", str(path), "--attack", "T1", "--level", "A2", "--export"])
    assert "Phase 2 unavailable" in capsys.readouterr().err

    empty = tmp_path / "empty.csv"
    empty.write_text("cycle_num,raw_hex,obj_timestamp\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        demo.main(["--file", str(empty), "--export"])
    assert "empty recording" in capsys.readouterr().err


def test_missing_artifacts_and_empty_selection_are_explicit(demo, local_replay, monkeypatch, capsys):
    path, *_ = local_replay
    with pytest.raises(SystemExit):
        demo.main(["--file", str(path), "--start", "999", "--export"])
    assert "empty replay clip" in capsys.readouterr().err

    def missing(*args, **kwargs):
        raise FileNotFoundError("AE artifact missing: run scripts/train.py first")

    monkeypatch.setattr(demo, "load_artifacts", missing)
    with pytest.raises(SystemExit):
        demo.main(["--file", str(path), "--export"])
    assert "AE artifact missing" in capsys.readouterr().err


def test_names_distinguish_clean_type_level_seed_and_selection(demo):
    rows = [(None, None, 11), ("T1", "A2", 11), ("T1", "A2", 22), ("T1", "A3", 11), ("T2", "A2", 11)]
    stems = {demo.output_stem("same.csv", "test", attack, level, seed) for attack, level, seed in rows}
    assert len(stems) == len(rows)
    assert demo.output_stem("same.csv", "test", None, None, 11, 5, 10).endswith("start5_cycles10")
