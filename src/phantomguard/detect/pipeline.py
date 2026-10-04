"""The detector: frames in, per-object verdicts out. Causal; never sees labels.

Imports only the FrameSource interface, never a concrete source.
"""

from __future__ import annotations

import time
import math
from pathlib import Path
from typing import Iterable, Iterator

from phantomguard.config import REPO_ROOT, bval, effective_cfg, paths, load_config
from phantomguard.cycles import Cycle, CycleAssembler
from phantomguard.detect.autoencoder import LearnedChecker, NumpyAE
from phantomguard.detect.common import LAYERS, CycleResult, FrameRecord, ObjVerdict
from phantomguard.detect.fusion import Fusion
from phantomguard.detect.kinematic import KinematicChecker
from phantomguard.detect.protocol import ProtocolChecker
from phantomguard.detect.replay_fp import ReplayChecker
from phantomguard.frames import Frame
from phantomguard.tracks import TrackManager

MODELS_DIR = REPO_ROOT / "models"


def load_artifacts(tag: str = "timeblock", models_dir: Path | None = None, *, cfg: dict | None = None,
                   baseline: dict | None = None, required: bool = False,
                   strict: bool = False) -> tuple[NumpyAE | None, set | None]:
    """Trained AE and replay library written by scripts/train.py (None if not trained yet)."""
    import pickle

    strict = strict or required
    models_dir = Path(models_dir) if models_dir is not None else paths(cfg or load_config()).models

    ae_p = models_dir / f"ae_{tag}.npz"
    lib_p = models_dir / f"replay_library_{tag}.pkl"
    missing = [str(p) for p in (ae_p, lib_p) if not p.exists()]
    if strict and missing:
        raise FileNotFoundError("trained artifacts missing: " + ", ".join(missing) + "; run scripts/train.py")
    ae = NumpyAE.load(ae_p) if ae_p.exists() else None
    if ae is not None:
        ae.validate(cfg, baseline, tag, strict=strict)
        if strict:
            if baseline is None:
                raise ValueError("strict artifact loading requires the matching baseline calibration")
            for key in ("ae_threshold_static", "ae_threshold_moving"):
                if key not in baseline:
                    raise ValueError(f"{key} missing: run scripts/train.py and scripts/calibrate.py")
                value = bval(baseline, key)
                if value is None or not math.isfinite(float(value)) or float(value) < 0:
                    raise ValueError(f"{key} must be a finite nonnegative calibration threshold")
    lib = None
    if lib_p.exists():
        try:
            stored = pickle.loads(lib_p.read_bytes())
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError, TypeError) as exc:
            raise ValueError(f"{lib_p}: corrupt or incompatible replay library; retrain artifacts") from exc
        if isinstance(stored, dict):
            from phantomguard.detect.autoencoder import validate_artifact_metadata

            validate_artifact_metadata(stored.get("metadata"), cfg, baseline, tag, "replay", strict=strict)
            lib = stored.get("fingerprints")
        else:
            if strict:
                raise ValueError(f"{lib_p}: legacy replay library has no provenance; retrain artifacts")
            lib = stored
        if not isinstance(lib, set):
            raise ValueError(f"{lib_p}: replay fingerprints must be a set")
    return ae, lib


class Detector:
    def __init__(self, cfg: dict, baseline: dict, ae: NumpyAE | None = None, library: set | None = None,
                 layers: Iterable[str] = LAYERS, *, capture_windows: bool = False):
        self.cfg = cfg
        self.layers = tuple(layers)
        if set(self.layers) - set(LAYERS):
            raise ValueError(f"unknown detector layers: {set(self.layers) - set(LAYERS)}")
        self.capture_windows = capture_windows
        self.roi = cfg["roi"]["max_range"]
        self.thr = cfg["motion"]["moving_threshold_mps"]
        self.protocol = ProtocolChecker(cfg, baseline)
        self.kin = KinematicChecker(cfg, baseline)
        self.replay = ReplayChecker(cfg, library)
        self.learned = None
        self.layer_status = {layer: "active" for layer in LAYERS}
        if library is None:
            self.layer_status["replay"] = "active_stream_only: training library unavailable"
        if ae is not None and {"ae_threshold_static", "ae_threshold_moving"} <= baseline.keys():
            self.learned = LearnedChecker(cfg, ae, bval(baseline, "ae_threshold_static"),
                                          bval(baseline, "ae_threshold_moving"))
        else:
            self.layer_status["learned"] = "unavailable: model or static/moving calibration missing"
        hist = max(cfg["kinematic"]["window_cycles"], cfg["kinematic"]["rcs_window_cycles"],
                   cfg["replay"]["k_gram"] + 1, cfg["learned"]["window_cycles"] + 1)
        self.tracks = TrackManager(bval(baseline, "reassign_jump"), cfg["units"]["tick_seconds"], self.thr,
                                   history=hist, max_gap_cycles=cfg["tracks"]["max_gap_cycles"])
        self.fusion = Fusion(effective_cfg(cfg, baseline), self.layers)
        # Every layer runs so reasons and scores are always reported; fusion uses only enabled layers.

    def process_cycle(self, cycle: Cycle) -> CycleResult:
        t0 = time.perf_counter()
        verdicts = []
        for ob in cycle.objects:
            o = ob.obj
            verdicts.append(ObjVerdict(ob.frame_index, o.slot, o.x, o.y, o.vx, o.vy, o.range <= self.roi,
                                       o.speed >= self.thr, timestamp_ticks=ob.t))
        for ob in cycle.malformed_objects:
            v = ObjVerdict(ob.frame_index, ob.data[0] if ob.data else None, None, None, None, None, False, False,
                           timestamp_ticks=ob.t)
            v.add("FRAME_LEN")
            v.score_status["ae"] = "malformed_frame"
            verdicts.append(v)
        cyc_reasons = self.protocol.check(cycle, verdicts)
        coloc, windows, wverd = [], [], []
        captured = {}
        for (ob, tr), v in zip(self.tracks.update(cycle), verdicts):
            o = ob.obj
            v.track_id = tr.track_id
            if not v.in_roi:
                v.score_status["ae"] = "outside_roi"
                continue
            self.kin.check_value(o, v)
            self.kin.check_track(o, tr, v)
            coloc.append((o.x, o.y, tr.total_points, v))
            hit, src = self.replay.check(tr, cycle.index)
            if hit:
                v.add("REPLAY")
                v.scores["replay_src"] = {"library": 1.0, "earlier_stream": 2.0, "concurrent": 3.0}[src]
            if self.learned is not None:
                w, moving, status = self.learned.window_outcome(tr.points)
                v.score_status["ae"] = status
                if w is not None:
                    windows.append(w)
                    wverd.append((v, moving))
                    if self.capture_windows:
                        captured[v.frame_index] = (tuple(float(x) for x in w), moving)
            else:
                v.score_status["ae"] = "unavailable_model"
        self.kin.check_colocation(coloc)
        if windows:
            errs = self.learned.score(windows)
            for (v, moving), e in zip(wverd, errs):
                v.scores["ae"] = float(e)
                if e > self.learned.thr[1 if moving else 0]:
                    v.add("LEARNED")
        frames = {}
        if cycle.header_frame_index is not None and cycle.header_t is not None:
            header_reasons = [r for r in cyc_reasons if r not in {"BAD_ID", "FRAME_LEN"}]
            frames[cycle.header_frame_index] = FrameRecord(cycle.header_frame_index, cycle.header_t,
                self.cfg["protocol"]["header_can_id"], "header" if cycle.header is not None else "other",
                tuple(header_reasons))
        by_frame = {v.frame_index: v for v in verdicts}
        for ob in cycle.objects + cycle.malformed_objects:
            frames[ob.frame_index] = FrameRecord(ob.frame_index, ob.t, self.cfg["protocol"]["object_can_id"],
                "object" if ob.obj is not None else "malformed", tuple(by_frame[ob.frame_index].reasons))
        for other in cycle.other:
            if other.frame_index in frames:
                continue  # malformed header identity was retained above
            frames[other.frame_index] = FrameRecord(other.frame_index, other.frame.timestamp_ticks,
                other.frame.can_id, "other", (other.reason,))
        delay = cycle.closed_t - cycle.header_t if cycle.closed_t is not None and cycle.header_t is not None else None
        res = CycleResult(cycle.index, cycle.header_t, verdicts, cyc_reasons, frames=sorted(frames.values(),
            key=lambda f: f.frame_index), header_frame_index=cycle.header_frame_index, closed_t=cycle.closed_t,
            assembly_delay_ticks=delay, layer_status=dict(self.layer_status), learned_windows=captured)
        self.fusion.apply(res, active_track_ids={tr.track_id for tr in self.tracks.active.values()})
        res.latency_ms = (time.perf_counter() - t0) * 1000.0
        res.detector_cpu_ms = res.latency_ms
        return res

    def run(self, frames: Iterable[Frame]) -> Iterator[CycleResult]:
        pc = self.cfg["protocol"]
        asm = CycleAssembler(pc["header_can_id"], pc["object_can_id"], pc["header_len"])
        assembly_cpu_ms = 0.0
        for fr in frames:
            # Time grouping/decoding after a source has yielded: CSV I/O and capture wait
            # are excluded. A boundary frame's assembly CPU belongs to the cycle it closes.
            t0 = time.perf_counter()
            c = asm.push(fr)
            assembly_cpu_ms += (time.perf_counter() - t0) * 1000.0
            if c is not None:
                res = self.process_cycle(c)
                res.assembly_cpu_ms = assembly_cpu_ms
                res.latency_ms = res.detector_cpu_ms + res.assembly_cpu_ms
                assembly_cpu_ms = 0.0
                yield res
        t0 = time.perf_counter()
        c = asm.flush()
        assembly_cpu_ms += (time.perf_counter() - t0) * 1000.0
        if c is not None:
            res = self.process_cycle(c)
            res.assembly_cpu_ms = assembly_cpu_ms
            res.latency_ms = res.detector_cpu_ms + res.assembly_cpu_ms
            yield res
