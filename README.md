# Phantom Guard

Offline detection of fabricated classic CAN radar frames using protocol integrity,
windowed physics, replay fingerprints, and a clean-trained autoencoder. The source
interface is `Frame(can_id, data, timestamp_ticks)`; attack labels stay in a sidecar.
The hardware capture source is deliberately a stub.

All commands below are for **Arch Linux** (bash/zsh) and are run from the repository root.

## 1. Install system packages

```bash
sudo pacman -Syu --needed python git tk
```

- `python` is Arch's current Python; the project needs 3.11 or newer.
- `tk` provides the Tk libraries matplotlib uses for the interactive viewer window. It is not
  needed for headless `--export` runs.
- No GPU or CUDA is needed. Everything runs on the CPU.

## 2. Create the virtual environment

Arch marks the system Python as externally managed (PEP 668), so `pip install` must run
inside a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate          # fish: source .venv/bin/activate.fish
python -m pip install --upgrade pip
# CPU-only PyTorch; a plain `pip install torch` pulls several GB of CUDA libraries
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[dev]"
```

If pip reports `No matching distribution found for torch`, Arch's rolling Python is
newer than the published PyTorch wheels. Create the environment with an older Python
through `uv`, then run the same install commands:

```bash
sudo pacman -S --needed uv
rm -rf .venv
uv venv --python 3.12 --seed .venv
source .venv/bin/activate
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[dev]"
```

All later commands assume the environment is active (`source .venv/bin/activate`).
Without activating it, call `.venv/bin/python` instead of `python`.

## 3. Provide the recordings

Put the four unchanged recordings under the configured `data.raw_dir` in
`configs/default.yaml` (default `data/raw`). A read-only copy or a symlink is fine.
Recordings, models, and caches are excluded from Git.

```bash
mkdir -p data/raw
# copy ...
cp /path/to/recordings/{emptyRoom,onePersonMovingFrontAndBack,onePersonMovingSideToSide,multiplePeopleChaotic}.csv data/raw/
chmod a-w data/raw/*.csv
# ... or link instead of copying
# ln -s /path/to/recordings/*.csv data/raw/
```

File names are **case-sensitive** on Linux and must match `configs/default.yaml` exactly.
Watch for this when the files come from a Windows or macOS machine:

| Recording | Rows | Cycles |
|---|---:|---:|
| emptyRoom.csv | 105377 | 4378 |
| onePersonMovingFrontAndBack.csv | 93424 | 4127 |
| onePersonMovingSideToSide.csv | 113581 | 4993 |
| multiplePeopleChaotic.csv | 130342 | 5597 |

Quick check that every file is present:

```bash
python -c "from phantomguard.config import load_config, raw_path; c = load_config(); [print(f, raw_path(c, f).is_file()) for f in c['data']['files']]"
```

## 4. Run the documented workflow

Run the preparation steps in this order. Baseline preparation removes later calibration
and model metadata, so training and calibration must follow it.

```bash
python scripts/learn_baseline.py --loso
python scripts/train.py --loso
python scripts/calibrate.py --loso
python scripts/run_clean_eval.py --workers 1
python scripts/run_attack_eval.py --preflight          # optional: availability/provenance check, no detector runs
python scripts/run_attack_eval.py --workers "$(nproc)"
python -m pytest -q
```

Compatible existing artifacts can be reused. Strict provenance checks reject changed
datasets, split identities, feature contracts, or mismatched calibration. Training uses
only fixed training blocks, and calibration uses clean validation data. No new test
results choose models or thresholds. Earlier inspection of test results is disclosed in
[the decisions log](docs/decisions.md).

The evaluator covers all four scenarios, the configured seeds and repetitions, the
60/20/20 time-block split, the four LOSO folds, static and moving cases, every layer, and
the ablations. T3 at levels A0–A2 is explicitly unsupported. Limits caused by source
material or slot capacity are recorded separately from completed attacks. The labels are
joined on the final emitted frame indices, including rewritten headers and overwritten
objects. Scene alarms and identification of the exact forged object are reported as
separate rates.

Evaluation pins each BLAS worker to one thread so that parallel workers do not multiply
memory workspaces. Explicit `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, or
`MKL_NUM_THREADS` settings are respected and reported. If memory is limited, use a
smaller `--workers` value than `$(nproc)`. Successful and unsupported attack jobs are
checkpointed under the ignored `runs/attack_eval`, and changes to code, dataset, or
artifacts select a new cache. Failed integration checks are retried and never count as
passes.

## Replay

```bash
python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --export
python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --export
python scripts/replay_demo.py --file multiplePeopleChaotic.csv --attack T1 --level A3
```

`--export` writes a PNG, a GIF, and a detector alert CSV without needing a display.
Without it, the matplotlib viewer opens with relative-time pacing; this needs a graphical
session and the `tk` package from step 1. Tk runs through XWayland on Wayland desktops.
Green means **not flagged**; it does not prove authenticity. Colours, arrows, reasons, and
the alert log use only detector verdicts. Output names include the recording, split,
attack, level, and seed. Use `--output-dir runs/my-validation` to keep per-machine output
local.

## Evidence and limits

[The generated summary](docs/results/summary.md) and `attack_eval_*.csv` are the
current evaluation evidence. The per-run and per-instance CSVs preserve denominators,
undetected attacks, censoring, timing, and score availability. The manifest records the
configuration, source checksums, artifact provenance, calibration settings, and
implementation hashes. Paths in it are repo-relative POSIX paths. The committed
`attack_eval_manifest.json` predates that change and still shows the generating
machine's absolute Windows paths. Legacy `attack_matrix.csv` and `attack_ttd.csv` came
from the earlier upstream evaluator and are historical evidence.

The clean target is under one false alert per minute. Having the implementation in place
does not show that the target is met: check the fresh clean report and summary for
passing, failing, unsupported, and blocked outcomes. Coordinate units, tick duration, and
the reconstructed header DLC remain assumptions. Static phantoms and complex genuine
repeated motion remain difficult. The autoencoder is the fixed NumPy deployment choice
and only corroborates other layers in fusion. The isolation forest is an offline
comparison on equivalent scoring windows.

Under this corroborating policy, autoencoder evidence adds no extra boolean alerts when
another soft layer already votes in that cycle. Raw autoencoder and isolation-forest
scores are still compared. The isolation forest is fitted on a fixed-seed training
subset capped at 50,000 windows. Both models share training-only normalization and the
same evaluation windows. Artifact metadata reports the actual fit counts and calibration
values.

See [the phase plan](docs/plan.md), [decisions](docs/decisions.md), and
[the integration contract](docs/phase2-handoff.md) for the acceptance criteria and
documented corrections.
