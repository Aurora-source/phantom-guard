"""Portable configuration: all relative paths use an explicit project anchor."""
from __future__ import annotations
import argparse
import json
import os
import math
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
import yaml

REPO_ROOT = Path(os.environ.get('PHANTOMGUARD_ROOT', Path(__file__).resolve().parents[2])).resolve()
DEFAULT_CONFIG = REPO_ROOT / "configs/default.yaml"
BASELINE_PATH = REPO_ROOT / "configs/baseline.json"  # backward compatible constant


def project_root(value=None) -> Path:
    selected = value or os.environ.get("PHANTOMGUARD_ROOT")
    if selected:
        p = Path(selected).expanduser()
        if not p.is_absolute():
            raise ValueError("--root / PHANTOMGUARD_ROOT must be an absolute path")
        return p.resolve()
    if (REPO_ROOT / "pyproject.toml").is_file() and (REPO_ROOT / "src/phantomguard").is_dir():
        return REPO_ROOT
    raise ValueError("Installed wheel needs --root or PHANTOMGUARD_ROOT (absolute workspace); run phantomguard init --root PATH")


def anchored(value, root: Path) -> Path:
    p = Path(value).expanduser()
    return (p if p.is_absolute() else root / p).resolve()


@dataclass(frozen=True)
class Paths:
    root: Path
    config: Path
    raw: Path
    processed: Path
    models: Path
    output: Path
    baseline: Path
    reports: Path


def paths(cfg: dict) -> Paths:
    p = cfg.get("_paths", {})
    root = project_root(p.get("root"))
    return Paths(root, anchored(p.get("config", "configs/default.yaml"), root),
                 anchored(cfg["data"]["raw_dir"], root), anchored(cfg["data"]["processed_dir"], root),
                 anchored(p.get("models", "models"), root), anchored(p.get("output", "runs"), root),
                 anchored(p.get("baseline", "configs/baseline.json"), root),
                 anchored(p.get("reports", "docs/results"), root))


def load_config(path=None, *, root=None, overrides=None) -> dict:
    root = project_root(root)
    p = anchored(path or os.environ.get("PHANTOMGUARD_CONFIG", "configs/default.yaml"), root)
    if p.is_file():
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    elif path or os.environ.get("PHANTOMGUARD_CONFIG"):
        raise FileNotFoundError(f"Configuration missing: {p}; run phantomguard init --root {root}")
    else:
        cfg = yaml.safe_load(files("phantomguard.resources").joinpath("default.yaml").read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("data"), dict):
        raise ValueError(f"{p}: expected a configuration mapping with data section")
    if cfg.get('splits') != {'train_frac':0.6,'val_frac':0.2}:
        raise ValueError('Evaluation splits are fixed at 60/20/20; do not alter splits to tune held-out results')
    for section,key in (('units','tick_seconds'),('roi','max_range')):
        value=float(cfg[section][key])
        if not math.isfinite(value) or value<=0:
            raise ValueError(f'{section}.{key} must be finite and positive')
    runtime = dict(cfg.get("paths", {}))
    runtime.update(root=str(root), config=str(p))
    envs = {"data_dir": "PHANTOMGUARD_DATA_DIR", "processed_dir": "PHANTOMGUARD_PROCESSED_DIR",
            "models": "PHANTOMGUARD_MODELS_DIR", "output": "PHANTOMGUARD_OUTPUT_DIR",
            "baseline": "PHANTOMGUARD_BASELINE", "reports": "PHANTOMGUARD_REPORTS_DIR"}
    chosen = {k: os.environ[v] for k, v in envs.items() if os.environ.get(v)}
    chosen.update({k: v for k, v in (overrides or {}).items() if v is not None})
    for key, val in chosen.items():
        if key in {"data_dir", "processed_dir"}:
            cfg["data"]["raw_dir" if key == "data_dir" else "processed_dir"] = str(val)
        else:
            runtime[key] = str(val)
    cfg["_paths"] = runtime
    resolved = paths(cfg)
    cfg["data"]["raw_dir"] = str(resolved.raw)
    cfg["data"]["processed_dir"] = str(resolved.processed)
    cfg["_paths"] = {k: str(getattr(resolved, k)) for k in ("root", "config", "models", "output", "baseline", "reports")}
    return cfg


def add_path_arguments(parser: argparse.ArgumentParser) -> None:
    existing = {a.dest for a in parser._actions}
    for flag, help_text in (("root", "absolute workspace root"), ("config", "YAML config"),
                            ("data-dir", "immutable decoder-v2 CSV directory"), ("processed-dir", "generated LOSO baselines/caches"),
                            ("models-dir", "trained artifact directory"), ("output-dir", "writable generated outputs"),
                            ("baseline", "matching calibrated baseline JSON"), ("reports-dir", "published report directory")):
        if flag.replace("-", "_") not in existing:
            parser.add_argument("--" + flag, type=Path, help=help_text)


def config_from_args(args) -> dict:
    return load_config(getattr(args, "config", None), root=getattr(args, "root", None), overrides={
        key: getattr(args, dest, None) for key, dest in (("data_dir", "data_dir"), ("processed_dir", "processed_dir"),
        ("models", "models_dir"), ("output", "output_dir"), ("baseline", "baseline"), ("reports", "reports_dir"))})


def load_baseline(path=None, *, cfg=None) -> dict:
    if path is not None and Path(path).is_absolute():
        p=Path(path)
    else:
        resolved = paths(cfg or load_config())
        p = anchored(path,resolved.root) if path else resolved.baseline
    if not p.is_file():
        raise FileNotFoundError(f"Baseline missing: {p}; restore a bundle or run phantomguard baseline --loso, train --loso, calibrate --loso")
    return json.loads(p.read_text(encoding="utf-8"))


def save_baseline(data, path=None, *, cfg=None) -> None:
    if path is not None and Path(path).is_absolute():
        p=Path(path)
    else:
        resolved = paths(cfg or load_config())
        p = anchored(path,resolved.root) if path else resolved.baseline
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def raw_path(cfg, name) -> Path:
    return paths(cfg).raw / name
