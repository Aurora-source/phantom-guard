"""The detector: frames in, per-object verdicts out. Causal; never sees labels.

Imports only the FrameSource interface, never a concrete source.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Iterator

from phantomguard.config import REPO_ROOT, bval, effective_cfg
from phantomguard.cycles import Cycle, CycleAssembler
from phantomguard.detect.autoencoder import LearnedChecker, NumpyAE
from phantomguard.detect.common import LAYERS, CycleResult, ObjVerdict
from phantomguard.detect.fusion import Fusion
from phantomguard.detect.kinematic import KinematicChecker
from phantomguard.detect.protocol import ProtocolChecker
from phantomguard.detect.replay_fp import ReplayChecker
from phantomguard.frames import Frame
from phantomguard.tracks import TrackManager

MODELS_DIR = REPO_ROOT / "models"


def load_artifacts(tag: str = "timeblock", models_dir: Path = MODELS_DIR) -> tuple[NumpyAE | None, set | None]:
    """Trained AE and replay library written by scripts/train.py (None if not trained yet)."""
    import pickle

    ae_p = models_dir / f"ae_{tag}.npz"
    lib_p = models_dir / f"replay_library_{tag}.pkl"
    ae = NumpyAE.load(ae_p) if ae_p.exists() else None
    lib = pickle.loads(lib_p.read_bytes()) if lib_p.exists() else None
    return ae, lib


class Detector:
    def __init__(self, cfg: dict, baseline: dict, ae: NumpyAE | None = None, library: set | None = None,
                 layers: Iterable[str] = LAYERS):
        self.cfg = cfg
        self.layers = tuple(layers)
        self.roi = cfg["roi"]["max_range"]
        self.thr = cfg["motion"]["moving_threshold_mps"]
        self.protocol = ProtocolChecker(cfg, baseline)
        self.kin = KinematicChecker(cfg, baseline)
        self.replay = ReplayChecker(cfg, library)
        self.learned = None
        if ae is not None and "ae_threshold_static" in baseline:
            self.learned = LearnedChecker(cfg, ae, bval(baseline, "ae_threshold_static"),
                                          bval(baseline, "ae_threshold_moving"))
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
                                       o.speed >= self.thr))
        for ob in cycle.malformed_objects:
            v = ObjVerdict(ob.frame_index, ob.data[0] if ob.data else None, None, None, None, None, False, False)
            v.add("FRAME_LEN")
            verdicts.append(v)
        cyc_reasons = self.protocol.check(cycle, verdicts)
        coloc, windows, wverd = [], [], []
        for (ob, tr), v in zip(self.tracks.update(cycle), verdicts):
            o = ob.obj
            v.track_id = tr.track_id
            self.kin.check_value(o, v)
            if not v.in_roi:
                continue
            self.kin.check_track(o, tr, v)
            coloc.append((o.x, o.y, tr.total_points, v))
            hit, src = self.replay.check(tr, cycle.index)
            if hit:
                v.add("REPLAY")
                v.scores["replay_src"] = 1.0 if src == "library" else 2.0
            if self.learned is not None:
                w = self.learned.window(tr.points)
                if w is not None:
                    windows.append(w[0])
                    wverd.append((v, w[1]))
        self.kin.check_colocation(coloc)
        if windows:
            errs = self.learned.score(windows)
            for (v, moving), e in zip(wverd, errs):
                v.scores["ae"] = float(e)
                if e > self.learned.thr[1 if moving else 0]:
                    v.add("LEARNED")
        res = CycleResult(cycle.index, cycle.header_t, verdicts, cyc_reasons)
        self.fusion.apply(res)
        res.latency_ms = (time.perf_counter() - t0) * 1000.0
        return res

    def run(self, frames: Iterable[Frame]) -> Iterator[CycleResult]:
        asm = CycleAssembler()
        for fr in frames:
            c = asm.push(fr)
            if c is not None:
                yield self.process_cycle(c)
        c = asm.flush()
        if c is not None:
            yield self.process_cycle(c)
