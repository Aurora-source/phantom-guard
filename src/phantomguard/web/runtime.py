"""Trusted read-only runtime identities and opt-in numerical policy (no NumPy)."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from phantomguard.config import paths

SCHEMA = "runtime-input-v1"


def numerical_policy():
    """Explicit server profile only; never overwrite a user's numerical setting."""
    profile = os.environ.get("PHANTOMGUARD_RUNTIME_PROFILE", "development")
    if profile not in {"development", "server"}:
        raise ValueError("PHANTOMGUARD_RUNTIME_PROFILE must be development or server")
    if profile == "server":
        for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ.setdefault(key, "1")
    return profile


def file_stamp(path):
    try:
        s = Path(path).stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    except FileNotFoundError:
        return None


def input_stamp(cfg):
    """Metadata probe, not content verification; include absence and directory entries."""
    p = paths(cfg)
    required = [p.config, p.baseline, *(p.raw / f for f in cfg["data"]["files"])]
    for root in (p.models, p.processed):
        required.append(root)
        if root.is_dir():
            required.extend(sorted(x for x in root.rglob("*") if x.is_file()))
    # Runtime code identity prevents sharing versions across installed implementations.
    code = Path(__file__).resolve().parents[1]
    required.extend(sorted(code.rglob("*.py")))
    return (SCHEMA, json.dumps(cfg, sort_keys=True), tuple((str(x), file_stamp(x)) for x in required))


def content_identity(stamp):
    """Hash trusted inputs after metadata changes, outside the fast polling path."""
    h = hashlib.sha256(repr(stamp[:2]).encode())
    for name, metadata in stamp[2]:
        h.update(name.encode())
        p = Path(name)
        if metadata is None:
            raise FileNotFoundError(name)
        if p.is_file():
            with p.open("rb") as f:
                for block in iter(lambda: f.read(1048576), b""):
                    h.update(block)
    if input_stamp_from_paths(stamp) != stamp[2]:
        raise RuntimeError("Inputs changed during content verification; retry readiness")
    return h.hexdigest()


def input_stamp_from_paths(stamp):
    return tuple((name, file_stamp(name)) for name, _ in stamp[2])
