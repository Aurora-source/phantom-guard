# Recordings and artifact supply

The inspected external source is a flat directory containing exactly these four
original decoder-v2 CSVs. No companion file is required to reconstruct recordings.
`source_file` is `live` in all rows; recording identity comes from the filename.

```text
<external source>/                    # existing Windows dataset directory
  emptyRoom.csv
  onePersonMovingFrontAndBack.csv
  onePersonMovingSideToSide.csv
  multiplePeopleChaotic.csv
<project>/data/raw/                   # canonical imported inputs, same bytes/names
  README.md                          # tracked; CSVs ignored
  <the same four CSVs>
```

`dataset` is only the owner's external import location, not a second pipeline
root. The loader reads `data/raw` or the explicitly configured external directory.
It does not recurse or normalize Linux case. `import-data` validates all files and
existing destinations before copying, hashes both sides, and never changes sources.
Files can remain read-only. No preprocessing changes the original bytes.

| Recording | Rows | Cycles | Bytes | SHA-256 |
|---|---:|---:|---:|---|
| emptyRoom.csv | 105377 | 4378 | 13523660 | `13fb666d6525e90f42bf823c7a938ee96d5819f5b541a834edda631bf6be2ebc` |
| onePersonMovingFrontAndBack.csv | 93424 | 4127 | 11969541 | `fe4fb020719c47686dce840a80f51f6b2ee7f3d69c50cf0d872015319e9c414e` |
| onePersonMovingSideToSide.csv | 113581 | 4993 | 14556505 | `04f105a1e412abfe4ce0153d8f2787a9b1ee79aba04a0a362b1d44ad4c2a6340` |
| multiplePeopleChaotic.csv | 130342 | 5597 | 16709635 | `2e08ed3c26c9cb64a5039878b1ef371fc2138ad0aa717efddc2eca1bea952f52` |

These counts/hashes are generated in `docs/results/dataset_inventory.json`.
CSV format: UTF-8, header row, one row per object, exactly this column order:

```text
source_file,cycle_num,scan_counter,meas_counter,sync_timestamp,sync_status,
obj_count_header,obj_count_actual,slot,obj_timestamp,raw_len,raw_hex,x_m,y_m,
range_m,azimuth_deg,vx_mps,vy_mps,speed_mps,is_moving,dyn_prop,rcs_dbsm
```

`raw_hex` holds the original eight bytes; decoded values are rounded. Slot/status
are hex strings. The replay source reconstructs headers from the stored count,
counter/status and timestamp; header byte 3/DLC are assumptions, recorded in config.
`tick_seconds=1e-4` and coordinate units remain unverified. Cadence and `rr_scale`
are learned. Real and forged frames use the same packaged decoder.

## Inference versus complete evaluation artifacts

Serving/clean replay needs:

```text
configs/default.yaml
configs/baseline.json                 # includes calibrated static/moving thresholds, fusion, provenance
models/ae_timeblock.npz               # NumPy weights + train-only normalization + feature/split metadata
models/replay_library_timeblock.pkl   # permitted training trajectory fingerprints + metadata
data/raw/<all four CSVs>              # strict provenance verifies train/calibration sources too
```

Full evaluation also needs `models/iforest_timeblock.pkl` and each of:

```text
models/ae_loso_<recording-stem>.npz
models/iforest_loso_<recording-stem>.pkl
models/replay_library_loso_<recording-stem>.pkl
data/processed/baseline_loso_<recording-stem>.json
```

There are five tags (timeblock + four LOSO), fifteen model/library files and four
fold baselines. No standalone scaler file is missing: normalization is embedded
in AE/IF files, calibration in the baseline JSONs. Doctor checks schema, feature
ordering, dataset hashes, split identities, baseline signatures and artifact IDs.
Artifacts do not silently fall back to an unavailable detector layer.

Raw recordings and trained files are intentionally absent from Git. Obtain the
owner's private verified deployment bundle or supply legally available recordings
and build offline. The project does not download recordings from another service.
See [pipeline](pipeline.md) and [server handoff](server-handoff.md) for rebuilding
and verified restoration. Attacker label/lifecycle sidecars are generated outputs
under runs; they are never required inputs to clean inference.
