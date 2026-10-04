# Generated preprocessing and fold calibration

Only this README is tracked. `phantomguard baseline --loso` writes
`baseline_loso_<recording-stem>.json`; training and calibration update those
files. They are required alongside the matching LOSO models for full evaluation.
Caches may be removed only when no job uses them. Inputs remain in `data/raw`.
