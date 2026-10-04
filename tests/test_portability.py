"""Portability: evaluation output must not depend on the OS, checkout location, or optional tools."""

from __future__ import annotations

import subprocess

from phantomguard.config import REPO_ROOT, load_config
from phantomguard.eval import report


def test_portable_path_is_repo_relative_posix(tmp_path):
    assert report.portable_path(REPO_ROOT / "models" / "ae_timeblock.npz") == "models/ae_timeblock.npz"
    outside = tmp_path / "x.csv"
    assert report.portable_path(outside) == outside.resolve().as_posix()


def test_provenance_survives_missing_git(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    m = report.provenance(load_config(), [REPO_ROOT / "models" / "nonexistent.npz"])
    assert m["git_head"] is None and m["git_dirty"] is False
    assert m["artifacts"][0]["path"] == "models/nonexistent.npz"
    assert all("\\" not in k for k in m["implementation_sha256"])
    assert all(not r["path"].startswith("/") for r in m["recordings"])  # default raw_dir is inside the repo
