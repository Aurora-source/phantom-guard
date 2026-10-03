"""Load configs/default.yaml and the generated configs/baseline.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.yaml"
BASELINE_PATH = REPO_ROOT / "configs" / "baseline.json"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_CONFIG) as f:
        return yaml.safe_load(f)


def raw_path(cfg: dict[str, Any], name: str) -> Path:
    return REPO_ROOT / cfg["data"]["raw_dir"] / name


def load_baseline(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path or BASELINE_PATH)
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run scripts/learn_baseline.py first")
    with open(p) as f:
        return json.load(f)


def save_baseline(data: dict[str, Any], path: Path | str | None = None) -> None:
    p = Path(path or BASELINE_PATH)
    with open(p, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def bval(baseline: dict[str, Any], key: str) -> Any:
    """Value of a baseline entry ({value, rule, ...}) or a plain value."""
    entry = baseline[key]
    return entry["value"] if isinstance(entry, dict) and "value" in entry else entry
