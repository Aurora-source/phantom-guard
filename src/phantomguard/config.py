"""Configuration and calibrated values (public backward compatible API)."""
from phantomguard.paths import (REPO_ROOT, DEFAULT_CONFIG, BASELINE_PATH, Paths, project_root, anchored, paths, load_config, load_baseline, save_baseline, raw_path, add_path_arguments, config_from_args)
from typing import Any

def bval(baseline: dict[str, Any], key: str) -> Any:
    """Value of a baseline entry ({value, rule, ...}) or a plain value."""
    entry = baseline[key]
    return entry["value"] if isinstance(entry, dict) and "value" in entry else entry


def effective_cfg(cfg: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Config with values calibrated into the baseline overlaid (currently fusion M-of-N).

    ``scripts/calibrate.py`` chooses M/N on clean validation data and stores it as ``fusion_mn``;
    without that entry the defaults in configs/default.yaml apply.
    """
    out = cfg
    mn = baseline.get("fusion_mn")
    if mn:
        m, n = mn["value"]
        out = {**out, "fusion": {**out["fusion"], "m": int(m), "n": int(n)}}
    policy = baseline.get("fusion_policy")  # detector profile v2: hard codes, cycle persistence, learned route
    if policy:
        out = {**out, "fusion": {**out["fusion"], **policy["value"]}}
    return out
