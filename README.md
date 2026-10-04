# Phantom Guard

Offline detection of fabricated classic CAN radar frames using protocol integrity,
windowed physics, replay fingerprints, and a clean-trained autoencoder. The source
interface is `Frame(can_id, data, timestamp_ticks)`; attack labels stay in a sidecar.
The hardware capture source is deliberately a stub.

## Run the documented workflow

Use Python 3.11 or newer and a virtual environment local to this checkout:

```text
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Provide these unchanged recordings under the configured `data.raw_dir` in
`configs/default.yaml` (default `data/raw`). A read-only data-only copy or link is
appropriate. Recordings, models, and caches are excluded from Git.

| Recording | Rows | Cycles |
|---|---:|---:|
| emptyRoom.csv | 105377 | 4378 |
| onePersonMovingFrontAndBack.csv | 93424 | 4127 |
| onePersonMovingSideToSide.csv | 113581 | 4993 |
| multiplePeopleChaotic.csv | 130342 | 5597 |

Run preparation in this order. Baseline preparation removes later calibration and
model metadata, so training and calibration must follow it.

```text
python scripts/learn_baseline.py --loso
python scripts/train.py --loso
python scripts/calibrate.py --loso
python scripts/run_clean_eval.py --workers 1
python scripts/run_attack_eval.py --workers 4
python -m pytest -q
```

Use the virtual environment's Python for every command. Compatible existing
artifacts can be reused; strict provenance checks reject changed datasets, split
identities, feature contracts, or mismatched calibration. Training uses only fixed
training blocks; calibration uses clean validation. No new test results choose
models or thresholds. Historical test inspection is disclosed in
[the decisions log](docs/decisions.md).

The evaluator covers all four scenarios, configured seeds and repetitions, the
60/20/20 time-block split, four LOSO folds, static/moving cases, every layer and
ablations. T3 A0–A2 are explicitly unsupported. Source-material and slot-capacity
limitations are recorded separately from completed attacks. Complete labels join
final emitted frame indices, including rewritten headers and overwritten objects.
Scene alarms and identification of the exact forged object have separate rates.

Evaluation defaults each BLAS worker to one thread to avoid multiplying memory
workspaces on Windows. Explicit `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, or
`MKL_NUM_THREADS` environment settings are respected and reported. Reduce
`--workers` if memory is limited. Successful and unsupported attack jobs are
checkpointed under ignored `runs/attack_eval`; code, dataset, or artifact changes
select a new cache. Failed integration checks are retried and are never passes.

## Replay

```text
python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --export
python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --export
python scripts/replay_demo.py --file multiplePeopleChaotic.csv --attack T1 --level A3
```

`--export` writes a PNG, GIF, and detector alert CSV without a display. Omitting it
opens the matplotlib viewer with relative-time pacing. Green means **not flagged**;
it does not prove authenticity. Colors, arrows, reasons, and the alert log use only
detector verdicts. Output names include the recording, split, attack, level, and
seed. `--output-dir runs/my-validation` keeps per-machine output local.

## Evidence and limits

[The generated summary](docs/results/summary.md) and `attack_eval_*.csv` are the
current evaluation evidence. Per-run and per-instance CSVs preserve denominators,
undetected attacks, censoring, timing, and score availability. The manifest records
configuration, source checksums, artifact provenance, calibration settings, and
implementation hashes. Legacy `attack_matrix.csv` and `attack_ttd.csv` came from
the earlier upstream evaluator and are historical evidence.

The clean target is under one false alert per minute. Implementation completion
does not establish that target: consult the fresh clean report and summary for
passing, failing, unsupported, and blocked outcomes. Coordinate units, tick
duration, and reconstructed header DLC remain assumptions. Static phantoms and
complex genuine repeated motion remain difficult. The AE is the fixed NumPy
deployment choice with corroborating-only fusion; the isolation forest is an
offline comparison on equivalent scoring windows.

Under the accepted corroborating policy, AE evidence adds no incremental boolean
alerts when another soft layer already votes in that cycle. Raw AE and IF scores
are still compared. The IF fit uses a fixed-seed training subset capped at 50,000
windows; both models share training-only normalization and the same evaluation
windows. Artifact metadata reports the actual fit counts and calibration values.

See [the phase plan](docs/plan.md), [decisions](docs/decisions.md), and
[the integration contract](docs/phase2-handoff.md) for the acceptance criteria and
documented corrections.
