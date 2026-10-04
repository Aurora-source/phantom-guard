# Trusted trained CPU artifacts (local, ignored)

Only this README is tracked. Restore the owner-supplied verified bundle or build
offline in baseline → train → calibrate order. Serving needs `ae_timeblock.npz`
and `replay_library_timeblock.pkl`, with matching `configs/baseline.json`.
Evaluation also needs `iforest_timeblock.pkl` and the same three artifacts for
each `loso_<recording-stem>` tag. Normalization and provenance are embedded;
calibration is in the baseline JSONs. NPZ/pickle artifacts must be trusted.
