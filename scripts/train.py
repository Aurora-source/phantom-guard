#!/usr/bin/env python3
"""Train the learned layer and build the replay library from the TRAIN split only.

- Autoencoder (torch, CPU) on per-track windows from train; thresholds = q of VALIDATION clean
  errors, separately for static and moving windows; written to the baseline JSON with the rule.
- Isolation forest on the same windows (comparison baseline, scored offline).
- Replay fingerprint library from train tracks.

Time-block split by default; --loso trains one model per leave-one-scenario-out fold, using the
fold baselines from `learn_baseline.py --loso`.
"""

from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest

from phantomguard.config import REPO_ROOT, load_baseline, load_config, save_baseline, BASELINE_PATH
from phantomguard.detect.autoencoder import NumpyAE, train_autoencoder, windows_from_tracks
from phantomguard.detect.pipeline import MODELS_DIR
from phantomguard.detect.replay_fp import build_library
from phantomguard.eval.splits import loso_folds, time_block_segments
from phantomguard.stats.baseline import collect


def tracks_of(cfg, segs):
    return list(collect(cfg, segs).tracks.values())


def train_one(cfg, train_segs, val_segs, baseline_path: Path, tag: str, seed: int) -> dict:
    lc = cfg["learned"]
    n, roi, thr = lc["window_cycles"], cfg["roi"]["max_range"], cfg["motion"]["moving_threshold_mps"]
    q = lc["threshold_quantile"]
    tr_tracks, va_tracks = tracks_of(cfg, train_segs), tracks_of(cfg, val_segs)
    xtr, mtr = windows_from_tracks(tr_tracks, n, roi, thr)
    xva, mva = windows_from_tracks(va_tracks, n, roi, thr)
    print(f"[{tag}] windows: train {len(xtr)} ({mtr.sum()} moving), val {len(xva)} ({mva.sum()} moving)")
    params = train_autoencoder(xtr, cfg, seed=seed)
    ae = NumpyAE(params)
    MODELS_DIR.mkdir(exist_ok=True)
    np.savez(MODELS_DIR / f"ae_{tag}.npz", params=np.array(params, dtype=object))
    e_va = ae.errors(xva)
    e_tr = ae.errors(xtr)
    b = load_baseline(baseline_path)
    rule = f"q{q} of VALIDATION clean AE reconstruction error ({{}} windows, window={n} cycles)"
    b["ae_threshold_static"] = {"value": float(np.quantile(e_va[~mva], q)), "rule": rule.format("static"),
                                "split": "val", "n_windows": int((~mva).sum()),
                                "train_exceedance": float((e_tr[~mtr] > np.quantile(e_va[~mva], q)).mean())}
    b["ae_threshold_moving"] = {"value": float(np.quantile(e_va[mva], q)), "rule": rule.format("moving"),
                                "split": "val", "n_windows": int(mva.sum()),
                                "train_exceedance": float((e_tr[mtr] > np.quantile(e_va[mva], q)).mean())}
    # Isolation forest baseline (same windows, same calibration rule)
    rng = np.random.default_rng(seed)
    z_mean, z_std = params["mean"], params["std"]
    sub = xtr[rng.choice(len(xtr), size=min(len(xtr), 50000), replace=False)]
    iso = IsolationForest(n_estimators=lc["iforest_trees"], random_state=seed).fit((sub - z_mean) / z_std)
    s_va = -iso.score_samples((xva - z_mean) / z_std)
    b["iforest_threshold_static"] = {"value": float(np.quantile(s_va[~mva], q)), "rule": rule.replace("AE reconstruction error", "isolation-forest anomaly score").format("static"), "split": "val"}
    b["iforest_threshold_moving"] = {"value": float(np.quantile(s_va[mva], q)), "rule": rule.replace("AE reconstruction error", "isolation-forest anomaly score").format("moving"), "split": "val"}
    (MODELS_DIR / f"iforest_{tag}.pkl").write_bytes(pickle.dumps({"model": iso, "mean": z_mean, "std": z_std}))
    lib = build_library(cfg, tr_tracks)
    (MODELS_DIR / f"replay_library_{tag}.pkl").write_bytes(pickle.dumps(lib))
    b["replay_library_size"] = {"value": len(lib), "rule": "fingerprints of moving in-ROI train tracks", "split": "train"}
    save_baseline(b, baseline_path)
    print(f"[{tag}] AE train {params['train_seconds']:.0f}s; thresholds static {b['ae_threshold_static']['value']:.4f} "
          f"moving {b['ae_threshold_moving']['value']:.4f}; val error medians static {np.median(e_va[~mva]):.4f} "
          f"moving {np.median(e_va[mva]):.4f}; replay library {len(lib)} fingerprints")
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loso", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    seed = cfg["attack"]["seeds"][0]
    segs = time_block_segments(cfg)
    train_one(cfg, segs["train"], segs["val"], BASELINE_PATH, "timeblock", seed)
    if args.loso:
        for held, fold in loso_folds(cfg).items():
            stem = Path(held).stem
            bp = REPO_ROOT / cfg["data"]["processed_dir"] / f"baseline_loso_{stem}.json"
            if not bp.exists():
                raise SystemExit(f"{bp} missing: run scripts/learn_baseline.py --loso first")
            train_one(cfg, fold["train"], fold["val"], bp, f"loso_{stem}", seed)


if __name__ == "__main__":
    main()
