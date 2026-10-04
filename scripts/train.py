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
from phantomguard.detect.autoencoder import (NumpyAE, build_model_metadata, calibrated_thresholds,
                                             collection_config, score_iforest, train_autoencoder, windows_from_tracks)
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
    b = load_baseline(baseline_path)
    collect_cfg = collection_config(cfg, b)
    tr_tracks, va_tracks = tracks_of(collect_cfg, train_segs), tracks_of(collect_cfg, val_segs)
    xtr, mtr = windows_from_tracks(tr_tracks, n, roi, thr)
    xva, mva = windows_from_tracks(va_tracks, n, roi, thr)
    print(f"[{tag}] windows: train {len(xtr)} ({mtr.sum()} moving), val {len(xva)} ({mva.sum()} moving)")
    if not len(xtr):
        raise ValueError(f"[{tag}] no eligible training windows; learned models unavailable")
    # Validate both calibration populations before training or replacing any artifact.
    calibrated_thresholds(np.zeros(len(xva)), mva, q, "AE", n)
    if b.get("_meta", {}).get("train_segments") != [dict(s.__dict__) for s in train_segs]:
        raise ValueError(f"[{tag}] baseline train segments do not match requested training split")
    if b.get("_meta", {}).get("val_segments") != [dict(s.__dict__) for s in val_segs]:
        raise ValueError(f"[{tag}] baseline validation segments do not match requested calibration split")
    params = train_autoencoder(xtr, cfg, seed=seed)
    ae = NumpyAE(params)
    e_va = ae.errors(xva)
    e_tr = ae.errors(xtr)
    thresholds = calibrated_thresholds(e_va, mva, q, "AE reconstruction error", n)
    for label, mask in (("static", ~mtr), ("moving", mtr)):
        b[f"ae_threshold_{label}"] = dict(thresholds[label],
            train_exceedance=float((e_tr[mask] > thresholds[label]["value"]).mean()) if mask.any() else None,
            train_windows=int(mask.sum()))
    # Isolation forest baseline (same windows, same calibration rule)
    rng = np.random.default_rng(seed)
    z_mean, z_std = params["mean"], params["std"]
    sub = xtr[rng.choice(len(xtr), size=min(len(xtr), 50000), replace=False)]
    iso = IsolationForest(n_estimators=lc["iforest_trees"], random_state=seed).fit((sub - z_mean) / z_std)
    if_artifact = {"model": iso, "mean": z_mean, "std": z_std}
    s_va = score_iforest(if_artifact, xva)
    if_thresholds = calibrated_thresholds(s_va, mva, q, "isolation-forest anomaly", n)
    for label in ("static", "moving"):
        b[f"iforest_threshold_{label}"] = if_thresholds[label]
    lib = build_library(cfg, tr_tracks)
    metadata = {}
    for kind, payload in (("ae", pickle.dumps(params)), ("iforest", pickle.dumps(if_artifact)),
                          ("replay", pickle.dumps(sorted(lib)))):
        metadata[kind] = build_model_metadata(cfg, train_segs, val_segs, tag, seed, b, kind, payload)
        metadata[kind]["train_windows"] = len(xtr)
        metadata[kind]["val_windows"] = len(xva)
        metadata[kind]["train_moving_windows"] = int(mtr.sum())
        metadata[kind]["val_moving_windows"] = int(mva.sum())
    params["metadata"] = metadata["ae"]
    metadata["ae"]["training"] = {"epochs_configured": lc["epochs"],
        "epochs_completed": params["epochs_completed"], "stopping_reason": params["stopping_reason"],
        "train_seconds": params["train_seconds"], "hidden": lc["hidden"], "latent": lc["latent"],
        "batch_size": lc["batch_size"], "learning_rate": lc["lr"], "max_train_seconds": lc["max_train_seconds"]}
    if_artifact["metadata"] = metadata["iforest"]
    if_artifact["metadata"]["fit_windows"] = len(sub)
    if_artifact["metadata"]["trees"] = lc["iforest_trees"]
    if_artifact["metadata"]["max_samples"] = int(iso.max_samples_)
    if_artifact["metadata"]["max_features"] = iso.max_features
    if_artifact["metadata"]["contamination"] = iso.contamination
    if_artifact["metadata"]["bootstrap"] = iso.bootstrap
    b["learned_artifacts"] = metadata
    b["learned_selection"] = {"model": "ae", "rule": metadata["ae"]["selection_rule"],
                              "comparison_windows": "identical eligible windows and train-only normalization"}
    b["replay_library_size"] = {"value": len(lib), "rule": "fingerprints of moving in-ROI train tracks", "split": "train"}
    MODELS_DIR.mkdir(exist_ok=True)
    np.savez(MODELS_DIR / f"ae_{tag}.npz", params=np.array(params, dtype=object))
    (MODELS_DIR / f"iforest_{tag}.pkl").write_bytes(pickle.dumps(if_artifact))
    (MODELS_DIR / f"replay_library_{tag}.pkl").write_bytes(pickle.dumps({"fingerprints": lib, "metadata": metadata["replay"]}))
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
