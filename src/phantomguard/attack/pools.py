"""Real-data pools the attacker samples from (rule 3: no simulation artefacts).

Everything here comes from recorded clean data, never from labels. The attacker may use training
data (its "recordings of the bus") and, for the strongest variants, recordings the defender never saw.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

import numpy as np

from phantomguard.eval.splits import Segment
from phantomguard.stats.baseline import P_CYCLE, P_R, P_VX, P_VY, P_X, P_Y, P_RCS, collect_segment, track_is_moving


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
    return pools_from_tracks(cfg, tracks)


def pools_from_tracks(cfg: dict, tracks: list) -> Pools:
    roi = cfg["roi"]["max_range"]
    thr = cfg["motion"]["moving_threshold_mps"]
    if not tracks:
        raise ValueError("attacker pools unavailable: no recorded tracks")
    pts = np.concatenate(tracks)
    inroi = pts[pts[:, P_R] <= roi]
    if not len(inroi):
        raise ValueError("attacker pools unavailable: no in-ROI observations")
    spd = np.hypot(inroi[:, P_VX], inroi[:, P_VY])
    mv = inroi[spd >= thr]
    mov_tr, sta_tr = [], []
    for p in tracks:
        # Replay material must stay inside ROI and retain consecutive observations.
        cuts = np.flatnonzero((np.diff(p[:, P_CYCLE]) != 1) |
                              (p[:-1, P_R] > roi) | (p[1:, P_R] > roi)) + 1
        for run in np.split(p, cuts):
            if len(run) >= 10 and (run[:, P_R] <= roi).all():
                (mov_tr if track_is_moving(run, cfg) else sta_tr).append(run)
    az = np.degrees(np.arctan2(inroi[:, P_Y], inroi[:, P_X]))
    return Pools(inroi[:, [P_X, P_Y]], mv[:, [P_X, P_Y]], mv[:, [P_VX, P_VY]], inroi[:, P_RCS],
                 inroi[:, [P_R, P_RCS]], mov_tr, sta_tr, (float(np.quantile(az, 0.005)), float(np.quantile(az, 0.995))))


_cache: dict = {}


def build_pools(cfg: dict, segs: list[Segment]) -> Pools:
    key = (json.dumps(cfg, sort_keys=True), tuple((s.file, s.lo, s.hi) for s in segs))
    if key not in _cache:
        _cache[key] = _build(cfg, tuple(segs))
    return _cache[key]


def stream_pools(cfg: dict, cycles, lo: int, hi: int) -> Pools:
    """Only the victim stream prefix [lo, hi), never future/evaluation-source frames."""
    from collections import defaultdict
    from phantomguard.cycles import iter_cycles
    from phantomguard.frames import CAN_ID_HEADER, CAN_ID_OBJECT, Frame, build_header
    from phantomguard.tracks import TrackManager

    def frames():
        for c in cycles[lo:hi]:
            if c.sync_timestamp is not None and c.meas_counter is not None and c.obj_count_header is not None:
                yield Frame(CAN_ID_HEADER, build_header(c.obj_count_header, c.meas_counter, c.sync_status or 0),
                            c.sync_timestamp)
            for t, raw in c.objects:
                yield Frame(CAN_ID_OBJECT, raw, t)

    manager = TrackManager(cfg["tracks"]["reassign_jump_default"], cfg["units"]["tick_seconds"],
                           cfg["motion"]["moving_threshold_mps"], max_gap_cycles=cfg["tracks"]["max_gap_cycles"])
    points = defaultdict(list)
    for cycle in iter_cycles(frames()):
        for _, track in manager.update(cycle):
            p = track.last
            points[track.track_id].append((p.cycle_index, p.t_s, p.x, p.y, p.vx, p.vy, p.rcs, p.rng, p.vr))
    return pools_from_tracks(cfg, [np.asarray(p) for p in points.values()])
