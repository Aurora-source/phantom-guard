"""Layer 4: learned normal behaviour on per-track windows.

Window = the last N points of a track (N = learned.window_cycles). Per point: dx, dy (from the
previous point), vx, vy, radial v, RCS, range. No cycle number, counter, timestamp or slot id.
Training (offline, torch) happens in scripts/train.py; inference here is numpy only, so the
detector stays fast and has no torch dependency at run time. Isolation forest is a comparison
baseline scored offline in batch.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from phantomguard.stats.baseline import P_R, P_RCS, P_VR, P_VX, P_VY, P_X, P_Y

N_FEAT = 7


def point_features(prev, cur) -> list[float]:
    """prev/cur are (x, y, vx, vy, vr, rcs, range) tuples."""
    return [cur[0] - prev[0], cur[1] - prev[1], cur[2], cur[3], cur[4], cur[5], cur[6]]


def track_windows(p: np.ndarray, n: int, roi: float, thr: float) -> tuple[np.ndarray, np.ndarray]:
    """All windows of a track array (stats.baseline layout) whose last point is in ROI.

    Returns (windows (m, n*7), is_moving (m,)). A window is 'moving' if any point has speed >= thr.
    """
    if len(p) < n + 1:
        return np.empty((0, n * N_FEAT)), np.empty(0, dtype=bool)
    d = np.diff(p[:, [P_X, P_Y]], axis=0)
    f = np.column_stack([d, p[1:, P_VX], p[1:, P_VY], p[1:, P_VR], p[1:, P_RCS], p[1:, P_R]])
    sw = np.lib.stride_tricks.sliding_window_view(f, (n, N_FEAT))[:, 0]
    w = sw.reshape(len(sw), n * N_FEAT)
    last_r = p[n:, P_R]
    spd = np.hypot(p[1:, P_VX], p[1:, P_VY]) >= thr
    mov = np.lib.stride_tricks.sliding_window_view(spd, n).any(axis=1)
    keep = last_r <= roi
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
        self.mean = np.asarray(params["mean"])
        self.std = np.asarray(params["std"])
        self.layers = [(np.asarray(W), np.asarray(b)) for W, b in params["layers"]]

    def errors(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.mean) / self.std
        h = z
        for i, (W, b) in enumerate(self.layers):
            h = h @ W + b
            if i < len(self.layers) - 1:
                h = np.tanh(h)
        return ((h - z) ** 2).mean(axis=1)

    @classmethod
    def load(cls, path: Path) -> "NumpyAE":
        d = np.load(path, allow_pickle=True)
        return cls(d["params"].item())


def train_autoencoder(x: np.ndarray, cfg: dict, seed: int = 0, log=print) -> dict:
    """Train with torch on CPU; returns numpy-ready params."""
    import time

    import torch
    from torch import nn

    lc = cfg["learned"]
    torch.manual_seed(seed)
    np.random.seed(seed)
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
            break
    lin = [m for m in model if isinstance(m, nn.Linear)]
    layers = [(m.weight.detach().numpy().T.copy(), m.bias.detach().numpy().copy()) for m in lin]
    return {"mean": mean, "std": std, "layers": layers, "train_seconds": time.time() - t0}


class LearnedChecker:
    """Online scoring: one window per in-ROI track that has N+1 points."""

    def __init__(self, cfg: dict, model: NumpyAE, thr_static: float, thr_moving: float):
        self.n = cfg["learned"]["window_cycles"]
        self.mthr = cfg["motion"]["moving_threshold_mps"]
        self.model = model
        self.thr = (thr_static, thr_moving)

    def window(self, pts) -> tuple[np.ndarray, bool] | None:
        if len(pts) < self.n + 1:
            return None
        win = list(pts)[-(self.n + 1):]
        feats = []
        moving = False
        for a, b in zip(win, win[1:]):
            feats.extend((b.x - a.x, b.y - a.y, b.vx, b.vy, b.vr, b.rcs, b.rng))
            moving |= math.hypot(b.vx, b.vy) >= self.mthr
        return np.asarray(feats), moving

    def score(self, windows: list[np.ndarray]) -> np.ndarray:
        if not windows:
            return np.empty(0)
        return self.model.errors(np.vstack(windows))
