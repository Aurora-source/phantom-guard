"""Layer 4: learned normal behaviour on per-track windows.

Window = the last N points of a track (N = learned.window_cycles). Per point: dx, dy (from the
previous point), vx, vy, radial v, RCS, range. No cycle number, counter, timestamp or slot id.
Training (offline, torch) happens in scripts/train.py; inference here is numpy only, so the
detector stays fast and has no torch dependency at run time. Isolation forest is a comparison
baseline scored offline in batch.
"""

from __future__ import annotations

import math
import hashlib
import json
import pickle
from functools import lru_cache
from pathlib import Path

import numpy as np

from phantomguard.stats.baseline import P_CYCLE, P_R, P_RCS, P_T, P_VR, P_VX, P_VY, P_X, P_Y

N_FEAT = 7
FEATURE_NAMES = ("dx", "dy", "vx", "vy", "radial_velocity", "rcs", "range")
ARTIFACT_SCHEMA = 1


class ArtifactError(ValueError):
    """A trained artifact cannot be used with the requested detector contract."""


def feature_contract(cfg: dict, kind: str = "ae") -> dict:
    shared = {"roi_max_range": cfg["roi"]["max_range"],
              "moving_threshold": cfg["motion"]["moving_threshold_mps"],
              "tick_seconds": cfg["units"]["tick_seconds"],
              "max_gap_cycles": cfg["tracks"]["max_gap_cycles"],
              "reassign_jump_default": cfg["tracks"]["reassign_jump_default"],
              "window_policy": "consecutive_cycles_all_points_in_roi"}
    if kind == "replay":
        shared["replay"] = dict(cfg["replay"])
    else:
        shared.update(window_cycles=cfg["learned"]["window_cycles"], features=list(FEATURE_NAMES))
    return shared


def baseline_signature(baseline: dict) -> str:
    meta = baseline.get("_meta", {})
    provenance = {"train_segments": meta.get("train_segments"), "val_segments": meta.get("val_segments"),
                  "reassign_jump": baseline.get("reassign_jump")}
    return hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()


def collection_config(cfg: dict, baseline: dict) -> dict:
    """Use the online detector's baseline reassignment rule when collecting offline windows."""
    from phantomguard.config import bval

    return dict(cfg, tracks=dict(cfg["tracks"], reassign_jump_default=bval(baseline, "reassign_jump")))


@lru_cache(maxsize=32)
def _recording_digest(path: str, size: int, modified_ns: int) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def recording_digest(path: Path) -> str:
    st = path.stat()
    return _recording_digest(str(path.resolve()), st.st_size, st.st_mtime_ns)


def build_model_metadata(cfg: dict, train, val, tag: str, seed: int, baseline: dict,
                         kind: str = "ae", payload: bytes = b"") -> dict:
    """Provenance only; recording bytes are hashed, never used as learned features."""
    from phantomguard.config import raw_path

    segments = {"train": [dict(s.__dict__) for s in train], "val": [dict(s.__dict__) for s in val]}
    if not train or not val or any(s.hi <= s.lo for s in [*train, *val]):
        raise ValueError("artifact provenance requires nonempty train and validation segments")
    if any(a.file == b.file and max(a.lo, b.lo) < min(a.hi, b.hi) for a in train for b in val):
        raise ValueError("artifact provenance has overlapping train and validation segments")
    hashes = {name: recording_digest(raw_path(cfg, name)) for name in sorted({s.file for s in [*train, *val]})}
    meta = {"schema_version": ARTIFACT_SCHEMA, "kind": kind, "tag": tag, "seed": seed,
            "contract": feature_contract(cfg, kind), "segments": segments, "source_sha256": hashes,
            "baseline_signature": baseline_signature(baseline),
            "normalization_split": "train", "threshold_split": "val",
            "selection_rule": "AE fixed for NumPy CPU inference; IF is an offline comparison; no test selection"}
    meta["artifact_id"] = hashlib.sha256(json.dumps(meta, sort_keys=True).encode() + payload).hexdigest()
    return meta


def validate_artifact_metadata(metadata: dict | None, cfg: dict | None = None, baseline: dict | None = None,
                               tag: str | None = None, kind: str = "ae", strict: bool = False) -> None:
    if not metadata:
        if strict:
            raise ArtifactError(f"{kind} artifact has no provenance; rerun scripts/train.py")
        return
    if metadata.get("schema_version") != ARTIFACT_SCHEMA or metadata.get("kind") != kind:
        raise ArtifactError(f"incompatible {kind} artifact schema")
    if cfg is not None and metadata.get("contract") != feature_contract(cfg, kind):
        raise ArtifactError(f"stale {kind} artifact: feature/configuration contract changed; retrain")
    if cfg is not None and strict:
        from phantomguard.config import raw_path

        for name, expected_hash in metadata.get("source_sha256", {}).items():
            if recording_digest(raw_path(cfg, name)) != expected_hash:
                raise ArtifactError(f"stale {kind} artifact: training/calibration recording {name} changed")
    if tag is not None and metadata.get("tag") != tag:
        raise ArtifactError(f"{kind} artifact belongs to {metadata.get('tag')}, requested {tag}")
    if baseline is not None:
        expected = baseline.get("learned_artifacts", {}).get(kind, {})
        if expected.get("artifact_id") != metadata.get("artifact_id"):
            raise ArtifactError(f"stale {kind} artifact: baseline provenance does not match; retrain")
        if metadata.get("baseline_signature") != baseline_signature(baseline):
            raise ArtifactError(f"stale {kind} artifact: baseline training splits changed; retrain")
        base_meta = baseline.get("_meta", {})
        for part in ("train", "val"):
            if metadata.get("segments", {}).get(part) != base_meta.get(f"{part}_segments"):
                raise ArtifactError(f"{kind} artifact {part} segments differ from baseline")


def calibrated_thresholds(scores: np.ndarray, moving: np.ndarray, q: float, model_name: str,
                          n: int) -> dict:
    scores, moving = np.asarray(scores), np.asarray(moving, dtype=bool)
    if scores.ndim != 1 or scores.shape != moving.shape or not np.isfinite(scores).all():
        raise ValueError(f"{model_name}: invalid validation scores")
    if not 0 <= q <= 1:
        raise ValueError("calibration quantile must be between 0 and 1")
    out = {}
    for label, mask in (("static", ~moving), ("moving", moving)):
        if not mask.any():
            raise ValueError(f"{model_name}: no eligible {label} validation windows; calibration unavailable")
        out[label] = {"value": float(np.quantile(scores[mask], q)), "quantile": q,
                      "rule": f"q{q} of VALIDATION clean {model_name} score ({label} windows, window={n} cycles)",
                      "split": "val", "n_windows": int(mask.sum())}
    return out


def point_features(prev, cur) -> list[float]:
    """prev/cur are (x, y, vx, vy, vr, rcs, range) tuples."""
    return [cur[0] - prev[0], cur[1] - prev[1], cur[2], cur[3], cur[4], cur[5], cur[6]]


def track_windows(p: np.ndarray, n: int, roi: float, thr: float) -> tuple[np.ndarray, np.ndarray]:
    """Windows of consecutive observations, with all N+1 points inside ROI.

    Returns (windows (m, n*7), is_moving (m,)). A window is 'moving' if any point has speed >= thr.
    """
    if n < 1:
        raise ValueError("learned.window_cycles must be positive")
    if len(p) < n + 1:
        return np.empty((0, n * N_FEAT)), np.empty(0, dtype=bool)
    d = np.diff(p[:, [P_X, P_Y]], axis=0)
    f = np.column_stack([d, p[1:, P_VX], p[1:, P_VY], p[1:, P_VR], p[1:, P_RCS], p[1:, P_R]])
    sw = np.lib.stride_tricks.sliding_window_view(f, (n, N_FEAT))[:, 0]
    w = sw.reshape(len(sw), n * N_FEAT)
    spd = np.hypot(p[1:, P_VX], p[1:, P_VY]) >= thr
    mov = np.lib.stride_tricks.sliding_window_view(spd, n).any(axis=1)
    eligible_points = (p[:, P_R] <= roi) & np.isfinite(p).all(axis=1)
    keep = np.lib.stride_tricks.sliding_window_view(eligible_points, n + 1).all(axis=1)
    consecutive = (np.diff(p[:, P_CYCLE]) == 1) & (np.diff(p[:, P_T]) > 0)
    keep &= np.lib.stride_tricks.sliding_window_view(consecutive, n).all(axis=1)
    return w[keep], mov[keep]


def windows_from_tracks(tracks: list, n: int, roi: float, thr: float) -> tuple[np.ndarray, np.ndarray]:
    ws, ms = [], []
    for p in tracks:
        w, m = track_windows(p, n, roi, thr)
        if len(w):
            ws.append(w)
            ms.append(m)
    if not ws:
        return np.empty((0, n * N_FEAT)), np.empty(0, dtype=bool)
    return np.concatenate(ws), np.concatenate(ms)


class NumpyAE:
    """MLP autoencoder forward pass in numpy. Weights come from scripts/train.py."""

    def __init__(self, params: dict):
        self.mean = np.asarray(params["mean"], dtype=float)
        self.std = np.asarray(params["std"], dtype=float)
        self.layers = [(np.asarray(W, dtype=float), np.asarray(b, dtype=float)) for W, b in params["layers"]]
        self.metadata = params.get("metadata")
        self.validate()

    def validate(self, cfg: dict | None = None, baseline: dict | None = None, tag: str | None = None,
                 strict: bool = False) -> None:
        if self.mean.ndim != 1 or self.std.shape != self.mean.shape or not len(self.mean):
            raise ArtifactError("AE normalization has invalid dimensions")
        if not np.isfinite(self.mean).all() or not np.isfinite(self.std).all() or (self.std <= 0).any():
            raise ArtifactError("AE normalization contains nonfinite or nonpositive values")
        width = len(self.mean)
        if not self.layers:
            raise ArtifactError("AE artifact contains no layers")
        for W, b in self.layers:
            if W.ndim != 2 or W.shape[0] != width or b.shape != (W.shape[1],):
                raise ArtifactError("AE artifact has incompatible layer dimensions")
            if not np.isfinite(W).all() or not np.isfinite(b).all():
                raise ArtifactError("AE artifact contains nonfinite weights")
            width = W.shape[1]
        if width != len(self.mean):
            raise ArtifactError("AE reconstruction width differs from feature width")
        if cfg is not None and len(self.mean) != cfg["learned"]["window_cycles"] * N_FEAT:
            raise ArtifactError("AE artifact window size differs from learned.window_cycles")
        validate_artifact_metadata(self.metadata, cfg, baseline, tag, "ae", strict)

    def errors(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.mean) or not np.isfinite(x).all():
            raise ValueError("AE scoring needs finite windows of the trained feature width")
        z = (x - self.mean) / self.std
        h = z
        for i, (W, b) in enumerate(self.layers):
            h = h @ W + b
            if i < len(self.layers) - 1:
                h = np.tanh(h)
        return ((h - z) ** 2).mean(axis=1)

    @classmethod
    def load(cls, path: Path) -> "NumpyAE":
        try:
            with np.load(path, allow_pickle=True) as d:
                return cls(d["params"].item())
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ArtifactError(f"{path}: invalid AE artifact ({exc}); retrain") from exc


def load_iforest(tag: str = "timeblock", models_dir: Path | None = None, cfg: dict | None = None,
                 baseline: dict | None = None, required: bool = False, strict: bool = False) -> dict | None:
    from phantomguard.config import paths, load_config

    required = required or strict
    path = (models_dir if models_dir is not None else paths(cfg or load_config()).models) / f"iforest_{tag}.pkl"
    if not path.exists():
        if required:
            raise FileNotFoundError(f"{path} missing: run scripts/train.py")
        return None
    try:
        artifact = pickle.loads(path.read_bytes())
    except (pickle.UnpicklingError, EOFError) as exc:
        raise ArtifactError(f"{path}: invalid isolation-forest artifact; retrain") from exc
    if not isinstance(artifact, dict) or not {"mean", "std", "model"} <= artifact.keys():
        raise ArtifactError(f"{path}: isolation-forest artifact is missing model or normalization")
    validate_artifact_metadata(artifact.get("metadata"), cfg, baseline, tag, "iforest", required)
    if required:
        if baseline is None:
            raise ArtifactError("strict isolation-forest loading requires the matching baseline calibration")
        from phantomguard.config import bval

        for key in ("iforest_threshold_static", "iforest_threshold_moving"):
            if key not in baseline:
                raise ArtifactError(f"{key} missing: run scripts/train.py then scripts/calibrate.py")
            try:
                threshold = float(bval(baseline, key))
            except (TypeError, ValueError) as exc:
                raise ArtifactError(f"{key} must be a finite nonnegative calibration threshold") from exc
            if not math.isfinite(threshold) or threshold < 0:
                raise ArtifactError(f"{key} must be a finite nonnegative calibration threshold")
    mean, std = np.asarray(artifact["mean"]), np.asarray(artifact["std"])
    if mean.ndim != 1 or std.shape != mean.shape or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ArtifactError("isolation-forest normalization is invalid")
    expected = cfg["learned"]["window_cycles"] * N_FEAT if cfg is not None else len(mean)
    if len(mean) != expected or getattr(artifact["model"], "n_features_in_", None) != expected:
        raise ArtifactError("isolation-forest feature width differs from the window contract")
    return artifact


def score_iforest(artifact: dict, windows) -> np.ndarray:
    """Batch score the exact same windows as online AE; excluded windows remain unscored."""
    x = np.asarray(windows, dtype=float)
    if x.size == 0:
        return np.empty(0)
    if x.ndim != 2 or x.shape[1] != len(artifact["mean"]) or not np.isfinite(x).all():
        raise ValueError("isolation-forest scoring needs finite windows of the trained feature width")
    return -artifact["model"].score_samples((x - artifact["mean"]) / artifact["std"])


def train_autoencoder(x: np.ndarray, cfg: dict, seed: int = 0, log=print) -> dict:
    """Train with torch on CPU; returns numpy-ready params."""
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or x.shape[1] != cfg["learned"]["window_cycles"] * N_FEAT:
        raise ValueError("AE training windows have an incompatible feature width")
    if not len(x):
        raise ValueError("AE training unavailable: no eligible training windows")
    if not np.isfinite(x).all():
        raise ValueError("AE training windows contain nonfinite values")
    if cfg["learned"]["epochs"] < 1 or cfg["learned"]["batch_size"] < 1:
        raise ValueError("AE epochs and batch_size must be positive")
    import time

    import torch
    from torch import nn

    lc = cfg["learned"]
    torch.manual_seed(seed)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-6] = 1.0
    z = torch.tensor((x - mean) / std, dtype=torch.float32)
    d = z.shape[1]
    h, lat = lc["hidden"], lc["latent"]
    model = nn.Sequential(nn.Linear(d, h), nn.Tanh(), nn.Linear(h, lat), nn.Tanh(), nn.Linear(lat, h), nn.Tanh(),
                          nn.Linear(h, d))
    opt = torch.optim.Adam(model.parameters(), lr=lc["lr"])
    bs = lc["batch_size"]
    t0 = time.time()
    g = torch.Generator().manual_seed(seed)
    stopping_reason = "configured_epochs_complete"
    for ep in range(lc["epochs"]):
        perm = torch.randperm(len(z), generator=g)
        tot = 0.0
        for i in range(0, len(z), bs):
            b = z[perm[i:i + bs]]
            loss = ((model(b) - b) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += float(loss.detach()) * len(b)
        log(f"    epoch {ep + 1:3d}  loss {tot / len(z):.4f}  ({time.time() - t0:.0f}s)")
        if time.time() - t0 > lc["max_train_seconds"]:
            log("    stopping: max_train_seconds reached")
            stopping_reason = "max_train_seconds"
            break
    lin = [m for m in model if isinstance(m, nn.Linear)]
    layers = [(m.weight.detach().numpy().T.copy(), m.bias.detach().numpy().copy()) for m in lin]
    return {"mean": mean, "std": std, "layers": layers, "train_seconds": time.time() - t0,
            "epochs_completed": ep + 1, "stopping_reason": stopping_reason}


class LearnedChecker:
    """Online scoring: one window per in-ROI track that has N+1 points."""

    def __init__(self, cfg: dict, model: NumpyAE, thr_static: float, thr_moving: float):
        self.n = cfg["learned"]["window_cycles"]
        self.mthr = cfg["motion"]["moving_threshold_mps"]
        self.roi = cfg["roi"]["max_range"]
        model.validate(cfg)
        if not all(math.isfinite(t) and t >= 0 for t in (thr_static, thr_moving)):
            raise ValueError("learned calibration thresholds must be finite and nonnegative")
        self.model = model
        self.thr = (thr_static, thr_moving)

    def window(self, pts) -> tuple[np.ndarray, bool] | None:
        features, moving, status = self.window_outcome(pts)
        return (features, moving) if status == "available" else None

    def window_outcome(self, pts) -> tuple[np.ndarray | None, bool, str]:
        if len(pts) < self.n + 1:
            return None, False, "insufficient_history"
        win = list(pts)[-(self.n + 1):]
        if not all(math.isfinite(value) for p in win for value in
                   (p.cycle_index, p.t_s, p.x, p.y, p.vx, p.vy, p.vr, p.rcs, p.rng)):
            return None, False, "invalid_values"
        if any(p.rng > self.roi for p in win):
            return None, False, "outside_roi"
        if any(b.cycle_index - a.cycle_index != 1 or b.t_s <= a.t_s for a, b in zip(win, win[1:])):
            return None, False, "track_gap"
        feats = []
        moving = False
        for a, b in zip(win, win[1:]):
            feats.extend(point_features((a.x, a.y, a.vx, a.vy, a.vr, a.rcs, a.rng),
                                        (b.x, b.y, b.vx, b.vy, b.vr, b.rcs, b.rng)))
            moving |= math.hypot(b.vx, b.vy) >= self.mthr
        features = np.asarray(feats)
        if not np.isfinite(features).all():
            return None, False, "invalid_values"
        return features, moving, "available"

    def score(self, windows: list[np.ndarray]) -> np.ndarray:
        if not windows:
            return np.empty(0)
        return self.model.errors(np.vstack(windows))
