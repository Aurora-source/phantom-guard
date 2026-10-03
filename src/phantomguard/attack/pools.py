"""Real-data pools the attacker samples from (rule 3: no simulation artefacts).

Everything here comes from recorded clean data, never from labels. The attacker may use training
data (its "recordings of the bus") and, for the strongest variants, recordings the defender never saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from phantomguard.eval.splits import Segment
from phantomguard.stats.baseline import P_R, P_VX, P_VY, P_X, P_Y, P_RCS, collect_segment, track_is_moving


@dataclass
class Pools:
    pos_roi: np.ndarray  # (n, 2) positions of in-ROI objects
    pos_moving: np.ndarray  # (n, 2) positions of moving in-ROI points
    vel_moving: np.ndarray  # (n, 2) reported (vx, vy) of moving in-ROI points
    rcs_roi: np.ndarray  # RCS of in-ROI objects
    rcs_range_roi: np.ndarray  # (n, 2) (range, rcs) for conditional sampling
    moving_tracks: list  # list of point arrays (stats.baseline column layout)
    static_tracks: list
    azimuth_range: tuple[float, float]  # degrees, p0.5-p99.5 of in-ROI objects


def _build(cfg: dict, segs: tuple[Segment, ...]) -> Pools:
    roi = cfg["roi"]["max_range"]
    thr = cfg["motion"]["moving_threshold_mps"]
    tracks = []
    for s in segs:
        tracks.extend(collect_segment(cfg, s).tracks.values())
    pts = np.concatenate(tracks)
    inroi = pts[pts[:, P_R] <= roi]
    spd = np.hypot(inroi[:, P_VX], inroi[:, P_VY])
    mv = inroi[spd >= thr]
    mov_tr, sta_tr = [], []
    for p in tracks:
        if np.median(p[:, P_R]) > roi or len(p) < 10:
            continue
        (mov_tr if track_is_moving(p, cfg) else sta_tr).append(p)
    az = np.degrees(np.arctan2(inroi[:, P_Y], inroi[:, P_X]))
    return Pools(inroi[:, [P_X, P_Y]], mv[:, [P_X, P_Y]], mv[:, [P_VX, P_VY]], inroi[:, P_RCS],
                 inroi[:, [P_R, P_RCS]], mov_tr, sta_tr, (float(np.quantile(az, 0.005)), float(np.quantile(az, 0.995))))


_cache: dict = {}


def build_pools(cfg: dict, segs: list[Segment]) -> Pools:
    key = (id(cfg), tuple((s.file, s.lo, s.hi) for s in segs))
    if key not in _cache:
        _cache[key] = _build(cfg, tuple(segs))
    return _cache[key]
