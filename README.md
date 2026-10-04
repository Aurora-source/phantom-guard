# Phantom Guard

**Recorded radar replay. Simulated CAN attacks. Causal detection on CPU.**

Hack Sprint P31 prototype for SR75 radar streams. Explore clean recordings and
T1 phantom, T2 flood, T3 replay and T4 shift/overwrite in a browser or matplotlib
viewer. Protocol, physics, replay fingerprints and a trained NumPy autoencoder
produce actual verdicts. Green means **not flagged**, not proven authentic.

The offline implementation covers Phases 0–6. The clean performance target
**remains unmet**: accepted measurements are 1.42 false alerts/minute on time-block
test and 3.41 on LOSO. Static phantoms, sparse moving clips, slot capacity and
genuine repeated motion remain limitations. Coordinate units, tick duration and
reconstructed header layout are assumptions. This is recorded-data research,
not live CAN protection or a safety certification. See [generated evidence](docs/results/README.md).

## Project layout

| Directory | In Git? / supplied by | Reads / writes |
|---|---|---|
| `src/phantomguard` | tracked implementation | installed CLI, decoder, sources, attacker, detector, evaluator, viewers/API |
| `configs` | tracked default/example YAML and learned baseline metadata | baseline/train/calibrate; all inference commands read matching config |
| `data/raw` | README tracked; four immutable CSVs ignored, supplied by user | import-data copies; preparation/replay/evaluation read only |
| `data/processed` | README tracked; generated fold baselines/caches ignored | baseline/train/calibrate write; LOSO evaluation reads |
| `models` | README tracked; trusted trained artifacts ignored | train writes; inference/evaluation read only |
| `runs` | README tracked; disposable results/logs/sidecars/jobs ignored | commands create writable outputs automatically |
| `docs/results` | selected generated evidence tracked | publication summaries/CSVs/provenance; browser reads actual reports |
| `deployment-bundles` | README tracked; archives/checksums ignored | bundle creates; verify/restore read |
| `scripts`, `tools` | tracked compatibility commands and browser verifier | installed package required; prefer CLI entry points |
| `tests`, `requirements`, `.github` | tracked tests, dependency locks, CI | validation/installation |

The owner's `dataset` directory is an **external import location**. There is one
configured raw data root: `data/raw` by default, or an explicit external directory.
Raw files and models are intentionally not downloadable from this Git repository.

Read [data/files/artifacts](docs/data.md), [installation/paths](docs/setup.md),
[pipeline/command reference](docs/pipeline.md), [deployment](docs/deployment.md),
and [tested server handoff](docs/server-handoff.md). They explain what a clone needs.

## Windows PowerShell quick start

Use installed **Python 3.11**, not an arbitrary default Python. These commands
install a normal wheel into a new venv; no editable install or PYTHONPATH required.

```powershell
git clone https://github.com/Aurora-source/phantom-guard.git
Set-Location phantom-guard
git checkout codex/portable-hosted-prototype
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements/offline.lock
python -m pip install --no-deps .
$env:PHANTOMGUARD_ROOT = (Get-Location).Path
python -m phantomguard init
python -m phantomguard import-data --source 'D:\Hacksprint\phantom-guard-phases-3-6\dataset'
```

That last path is the inspected owner's source. Another user sets `--source` to
their own directory with the exact four CSVs listed in [data.md](docs/data.md).
Alternatively set `PHANTOMGUARD_DATA_DIR` to read external inputs without copying.

Supply matching artifacts: use the verified bundle in the server handoff, or build
them offline. To build (CPU only), run:

```powershell
python -m pip install -r requirements/training-cpu.lock --index-url https://download.pytorch.org/whl/cpu
python -m phantomguard baseline --loso
python -m phantomguard train --loso
python -m phantomguard calibrate --loso
```

With matching artifacts restored/built:

```powershell
python -m phantomguard doctor --full
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/clean
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --seed 11 --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/attack
python -m phantomguard attack-eval --smoke --workers 1 --output-dir runs/smoke/evaluation
python -m phantomguard serve --port 8765
```

Open **http://127.0.0.1:8765**. Stop with Ctrl+C. Exports are PNG/GIF plus detector
alert CSV and a separate attacker sidecar. Tests are separate from evaluation;
full validation commands are in [pipeline.md](docs/pipeline.md).

## Linux Bash quick start

On Ubuntu 26.04 use a dedicated Python 3.11 environment; its system Python may
be 3.14. The container workflow in [deployment.md](docs/deployment.md) supplies
the pinned runtime. For native Linux, install `uv` via its official installer if
it is absent, then create the explicit 3.11 venv:

```bash
git clone https://github.com/Aurora-source/phantom-guard.git
cd phantom-guard
git checkout codex/portable-hosted-prototype
# Only if uv is absent:
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.11
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install --python .venv/bin/python -r requirements/offline.lock
uv pip install --python .venv/bin/python --no-deps .
export PHANTOMGUARD_ROOT="$(pwd)"
python -m phantomguard init
```

Supply inputs/artifacts using the bundle, or set your real source directory:

```bash
DATA_SOURCE="/srv/phantomguard-inputs"  # replace with your directory containing all four CSVs
python -m phantomguard import-data --source "$DATA_SOURCE"
uv pip install --python .venv/bin/python -r requirements/training-cpu.lock --index-url https://download.pytorch.org/whl/cpu
python -m phantomguard baseline --loso
python -m phantomguard train --loso
python -m phantomguard calibrate --loso
python -m phantomguard doctor --full
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/clean
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --seed 11 --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/attack
python -m phantomguard attack-eval --smoke --workers 1 --output-dir runs/smoke/evaluation
python -m phantomguard serve --port 8765
```

When restoring compatible artifacts, skip the installation/training/calibration
steps. Do not rebuild baselines after restoring trained models. Open localhost
in a browser; on a remote server use the documented SSH tunnel/reverse proxy.

## Full checks and evidence

```text
python -m phantomguard clean-eval --workers 1 --output-dir runs/validation
python -m phantomguard attack-eval --workers 2 --output-dir runs/validation
python -m pip install -r requirements/dev.lock
python -m pip install -r requirements/training-cpu.lock --index-url https://download.pytorch.org/whl/cpu
python -m pytest -q --junitxml=runs/validation/tests.xml
python -m playwright install chromium
python tools/check_browser.py --output runs/browser-check
```

The last command needs `serve` running in another terminal. Full evaluation
includes configured seeds/repetitions, fixed time-block and LOSO splits,
static/moving cases, supported levels, replay-source provenance, layer comparisons
and ablations. It takes substantially longer than smoke. Never choose a threshold
or model from held-out results; unsupported/missed/censored attacks remain visible.

For the Ubuntu home server, follow [server-handoff.md](docs/server-handoff.md):
matching tested Git SHA, verified ZIP/checksum, read-only dataset/model mounts,
bounded outputs, exact start/health commands, and measured/unverified limitations.
Actual server deployment is intentionally left to the server Codex session.
