# Installation and portable paths

Use **CPython 3.11**. The pinned dependencies and existing sklearn artifacts are
tested with this minor version; `pyproject.toml` rejects 3.12+. Ubuntu 26.04's
system Python is not assumed compatible. Docker supplies Python 3.11.17;
native Ubuntu can use an independent Python 3.11 installed by `uv`.

## Dependencies

| File / extra | Purpose | Trains? |
|---|---|---|
| `requirements/runtime.lock` / base package | NumPy detector, recorded replay, Waitress browser API | No |
| `requirements/offline.lock` / `evaluation,viewer` | sklearn comparison, pandas reports, matplotlib/Pillow export | No |
| `requirements/training-cpu.lock` / `training` | CPU PyTorch, offline model creation | Yes, only `train` |
| `requirements/dev.lock` / `dev` | Tests, Playwright browser verification | No |

Lock files pin transitive versions. Install CPU torch separately from its official
index; selecting the generic `training` extra alone may download a GPU-capable
wheel. The browser image contains neither Torch, pandas nor sklearn. Never train
inside a browser request. Only load trusted local NPZ/pickle artifacts.

The supported package is still `src/phantomguard`; installed commands are
`phantomguard` and `python -m phantomguard`. Compatibility scripts remain, but
no `PYTHONPATH` setup is needed. Installation tolerates absent data/models/runs.
The decoder v2 lives once in `phantomguard.decoder`; `tools/decode.py` delegates.

## One path mechanism

All CLI commands, preparation, evaluation, API and bundle builder use
`phantomguard.paths`. Relative paths anchor to the resolved **project root**,
never to the current working directory. The root must be absolute. Quote spaces.
Filename case is significant on Linux; preserve the four filenames exactly.

Precedence: **command line > environment > YAML paths > defaults**. An editable
checkout can infer its root from the installed source location. A wheel requires
`--root` or `PHANTOMGUARD_ROOT`, which also allows invocation from another folder.
Configuration itself: `--config` > `PHANTOMGUARD_CONFIG` > `<root>/configs/default.yaml`.
Packaged defaults allow `init` before a config exists; explicit missing configs fail.

| CLI option | Environment | YAML / default |
|---|---|---|
| `--root` | `PHANTOMGUARD_ROOT` | checkout anchor; explicit for wheels |
| `--config` | `PHANTOMGUARD_CONFIG` | `configs/default.yaml` |
| `--data-dir` | `PHANTOMGUARD_DATA_DIR` | `data.raw_dir`, default `data/raw` |
| `--processed-dir` | `PHANTOMGUARD_PROCESSED_DIR` | `data.processed_dir`, default `data/processed` |
| `--models-dir` | `PHANTOMGUARD_MODELS_DIR` | `paths.models`, default `models` |
| `--output-dir` | `PHANTOMGUARD_OUTPUT_DIR` | `paths.output`, default `runs` |
| `--baseline` | `PHANTOMGUARD_BASELINE` | `paths.baseline`, default `configs/baseline.json` |
| `--reports-dir` | `PHANTOMGUARD_REPORTS_DIR` | `paths.reports`, default `docs/results` |

Evaluators/viewer use `runs/clean-eval`, `runs/attack-eval`, `runs/replay` by default;
an explicit `--output-dir` is the exact destination. Environment/YAML output roots
hold those subdirectories. Browser jobs live under `<output>/browser`; evaluation
checkpoints under `<output>/attack_eval/<contract>`. `init` is idempotent and never
replaces a configuration or input. Import also refuses differing existing data.

PowerShell example for the existing immutable source:

```powershell
$env:PHANTOMGUARD_ROOT = (Get-Location).Path
python -m phantomguard import-data --source 'D:\Hacksprint\phantom-guard-phases-3-6\dataset'
# Alternatively point at the external root without making copies:
$env:PHANTOMGUARD_DATA_DIR = 'D:\Hacksprint\phantom-guard-phases-3-6\dataset'
python -m phantomguard doctor --full
```

Bash external data example (set this to your actual directory):

```bash
export PHANTOMGUARD_ROOT="$(pwd)"
export PHANTOMGUARD_DATA_DIR="/srv/phantomguard-inputs"
python -m phantomguard doctor --full
```

CLI variables are ordinary environment variables. `.env` is used only for Compose
substitution; the Python CLI does not silently load it. `.env` is ignored.

## Troubleshooting

Recording arguments also have explicit anchors: `replay --file NAME.csv` reads
the configured raw data directory; `--file inputs/NAME.csv` reads relative to
the configured workspace root; an absolute file path is used as supplied.
A same-named file in the caller's directory never overrides those choices.

- **Missing recordings:** doctor names each file. Import from a directory containing
  the exact four CSVs, or set `--data-dir`. An extra `dataset` level is not searched.
- **Missing/calibration/stale artifacts:** restore the matching verified bundle, or
  run baseline → train → calibrate offline. Rebuilding baseline deletes later
  calibration entries; rerun the later stages in order. Never adjust thresholds
  to make held-out results pass.
- **Wrong Python:** `python --version` must show 3.11; activate the correct venv or
  use the pinned container. Avoid Ubuntu's default `python3` if it is 3.14.
- **Permissions:** data/models may be read-only; outputs must be writable. Doctor
  probes outputs. `--full` also checks preprocessing output for offline preparation.
  Container serving only probes runs, allowing read-only input/calibration mounts.
- **Port in use:** choose `serve --port 8766` or `PHANTOMGUARD_PORT=8766` for Compose.
  Do not stop unrelated server services. `/healthz` means process alive; `/readyz`
  returns 503 and actionable details when the pipeline is unavailable.
- **Memory:** default BLAS is one thread; full evaluation workers multiply memory.
  Start with one or two on the server. Browser jobs are bounded separately and can
  be cancelled/reset; expired jobs are cleaned. Run one service per output directory.
- **Headless display:** `replay --export` uses Agg; omit `--export` only with a GUI.
- **PowerShell activation blocked:** call `.\.venv\Scripts\python.exe` explicitly
  instead of changing the machine's execution policy. Set `PHANTOMGUARD_ROOT`
  as shown in the quick start.
- **Updated native checkout:** reinstall its wheel with
  `python -m pip install --no-deps --force-reinstall .`; an earlier installed wheel
  does not update just because Git advanced. Container users rebuild the image.
  Browser playback works without a display server.
- **No eligible attack:** some static/short clips lack moving material or free slots.
  This is an explicit unsupported/no-material outcome, not a successful detection.
