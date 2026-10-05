"""Prepared, immutable attacker-pool exports with identity keys and a bounded loading contract.

Building pools parses CSVs and links tracks (seconds per process). A prepared export stores the result
once, keyed by everything that determines it, so a runtime can load it instead of re-deriving it.

Identity key = SHA-256 over: export schema, recording SHA-256s, segment bounds, and the configuration
slice pools depend on (ROI, motion threshold, track linking, protocol framing). Any change gives a new
key; a loader asked for key K refuses a file whose stored key differs. Files are written once
(``x`` mode) and never overwritten.

Loading contract: ``.npz`` read with ``allow_pickle=False`` (no code execution), a byte-size bound
checked before reading, required arrays and shapes validated, finite values only. Pools derive from
clean recordings only (never labels); an export is not a detector input.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np

from phantomguard.attack.pools import Pools, pools_from_tracks
from phantomguard.eval.splits import Segment

PREPARED_SCHEMA = "phantomguard.pools/1"
MAX_BYTES = 256 * 1024 * 1024
_ARRAYS = ("pos_roi", "pos_moving", "vel_moving", "rcs_roi", "rcs_range_roi", "moving_points", "moving_offsets",
           "static_points", "static_offsets", "azimuth_range")


class PreparedError(ValueError):
    pass


def config_slice(cfg: dict) -> dict:
    return {"roi": cfg["roi"], "motion": cfg["motion"], "tracks": cfg["tracks"], "protocol": cfg["protocol"],
            "tick_seconds": cfg["units"]["tick_seconds"]}


def identity_key(cfg: dict, segments: list[Segment], recording_sha256: dict[str, str]) -> str:
    ident = {"schema": PREPARED_SCHEMA, "config": config_slice(cfg),
             "segments": [[s.file, s.lo, s.hi, recording_sha256[s.file]] for s in segments]}
    return hashlib.sha256(json.dumps(ident, sort_keys=True, default=str).encode()).hexdigest()


def _pack(tracks: list) -> tuple[np.ndarray, np.ndarray]:
    if not tracks:
        return np.empty((0, 0)), np.zeros(1, dtype=np.int64)
    return np.concatenate(tracks), np.cumsum([0] + [len(t) for t in tracks]).astype(np.int64)


def _unpack(points: np.ndarray, offsets: np.ndarray) -> list:
    return [points[a:b] for a, b in zip(offsets[:-1], offsets[1:])]


def export(cfg: dict, segments: list[Segment], recording_sha256: dict[str, str], directory: Path) -> Path:
    """Build pools from the segments and write ``<directory>/pools-<key>.npz`` (never overwrites)."""
    from phantomguard.stats.baseline import collect_segment

    key = identity_key(cfg, segments, recording_sha256)
    path = Path(directory) / f"pools-{key}.npz"
    if path.exists():
        return path
    tracks = [t for s in segments for t in collect_segment(cfg, s).tracks.values()]
    pools = pools_from_tracks(cfg, tracks)
    mp, mo = _pack(pools.moving_tracks)
    sp, so = _pack(pools.static_tracks)
    meta = json.dumps({"schema": PREPARED_SCHEMA, "key": key, "segments": [[s.file, s.lo, s.hi] for s in segments],
                       "recording_sha256": {s.file: recording_sha256[s.file] for s in segments}})
    buf = io.BytesIO()
    np.savez_compressed(buf, meta=np.frombuffer(meta.encode(), dtype=np.uint8), pos_roi=pools.pos_roi,
                        pos_moving=pools.pos_moving, vel_moving=pools.vel_moving, rcs_roi=pools.rcs_roi,
                        rcs_range_roi=pools.rcs_range_roi, moving_points=mp, moving_offsets=mo, static_points=sp,
                        static_offsets=so, azimuth_range=np.asarray(pools.azimuth_range, dtype=float))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:            # immutable: fail rather than replace
        f.write(buf.getvalue())
    return path


def load(path: Path, expected_key: str, max_bytes: int = MAX_BYTES) -> Pools:
    path = Path(path)
    size = path.stat().st_size
    if size > max_bytes:
        raise PreparedError(f"{path}: {size} bytes exceeds the loading bound {max_bytes}")
    try:
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(bytes(z["meta"]).decode())
            arrays = {k: z[k] for k in _ARRAYS}
    except (KeyError, ValueError, OSError) as exc:
        raise PreparedError(f"{path}: not a valid prepared pool export ({exc})") from exc
    if meta.get("schema") != PREPARED_SCHEMA:
        raise PreparedError(f"{path}: schema {meta.get('schema')!r} not supported (expects {PREPARED_SCHEMA})")
    if meta.get("key") != expected_key:
        raise PreparedError(f"{path}: identity key differs from the requested recordings/config; rebuild the export")
    for k, a in arrays.items():
        if a.dtype.kind == "f" and not np.isfinite(a).all():
            raise PreparedError(f"{path}: non-finite values in {k}")
    for name in ("pos_roi", "pos_moving", "vel_moving", "rcs_range_roi"):
        if arrays[name].ndim != 2 or arrays[name].shape[1] != 2:
            raise PreparedError(f"{path}: {name} must be (n, 2)")
    for pts, off in (("moving_points", "moving_offsets"), ("static_points", "static_offsets")):
        o = arrays[off]
        if o.ndim != 1 or o[0] != 0 or (np.diff(o) < 0).any() or o[-1] != len(arrays[pts]):
            raise PreparedError(f"{path}: inconsistent {off}")
    return Pools(arrays["pos_roi"], arrays["pos_moving"], arrays["vel_moving"], arrays["rcs_roi"], arrays["rcs_range_roi"],
                 _unpack(arrays["moving_points"], arrays["moving_offsets"]),
                 _unpack(arrays["static_points"], arrays["static_offsets"]), tuple(float(x) for x in arrays["azimuth_range"]))
