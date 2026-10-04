# Command reference and validation workflows

Commands below assume an installed Python 3.11 package and `PHANTOMGUARD_ROOT`
set to the absolute checkout/workspace. Then they work from **any directory**.
Direct `python scripts/*.py` compatibility commands require the checkout as cwd.
All commands accept the shared path options described in [setup](setup.md).

| Command | Prerequisites / reads | Writes | Purpose / runtime |
|---|---|---|---|
| `python -m phantomguard init` | package, project root | missing directories/default YAML only | idempotent setup; <1s |
| `python -m phantomguard import-data --source DIRECTORY` | four decoder-v2 CSVs | exact copies in configured raw dir; runs/data-import.json | immutable import/checksums; seconds |
| `python -m phantomguard doctor [--full]` | configured files/artifacts | temporary write probes only | paths, versions, schema/calibration/provenance; seconds |
| `python -m pytest -q` | dev + training CPU dependencies; optional data for marked tests | local test caches / optional JUnit | unit/integration checks, not training or performance evidence |
| `python -m phantomguard baseline --loso` | all recordings, offline extras | baseline JSON and four processed fold JSONs; runs/baseline reports | clean training statistics; tens of seconds; erases later calibration |
| `python -m phantomguard train --loso` | learned baselines, recordings, CPU training extras | fifteen models/libraries; baseline metadata/initial thresholds | CPU AE/IF training; 30 epochs or configured 300s cap per AE fold |
| `python -m phantomguard calibrate --loso` | matching baseline/models, recordings, offline extras | calibrated baseline/fold JSONs | clean validation only; several minutes |
| `python -m phantomguard clean-eval --workers 1` | all five artifact bundles, all recordings, offline extras | runs/clean-eval/clean_eval*.csv/md | independent clean FP/layer comparison, one CPU worker |
| `python -m phantomguard attack-eval --workers 2` | all artifacts/recordings, real attacker | runs/attack-eval reports + checkpoints/sidecars | full configured sweep; thousands of runs; resource dependent |
| `python -m phantomguard attack-eval --smoke --workers 1` | same artifacts/data | same report formats, explicitly smoke scope | four representative T1–T4 cells; not a headline benchmark |
| `python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --export` | timeblock model/library/calibration, viewer extras | runs/replay PNG/GIF/alert CSV | CPU inference + headless rendering; seconds to tens of seconds |
| `python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --seed 11 --export` | same + training recordings for real attacker pools | replay outputs + separate labels/lifecycle | supported actual injection through same decode/detect path |
| `python -m phantomguard serve --port 8765` | runtime only, data/AE/library/calibration | bounded runs/browser jobs | browser/API, no training, CPU only |
| `python -m phantomguard bundle` | doctor --full passes, selected generated reports | ignored deployment-bundles ZIP + checksum | portable deterministic archive, source SHA and payload manifest |
| `python -m phantomguard verify-bundle --archive ARCHIVE` | ZIP + .sha256 sidecar | none | archive + every payload SHA256, safe paths/size |
| `python -m phantomguard restore-bundle --archive ARCHIVE` | matching tested Git SHA, verified archive | absent payload files only | never replaces differing files; then doctor --full |
| `python tools/check_browser.py --url http://127.0.0.1:8765 --output runs/browser-check` | running API, dev extras + Chromium | screenshots, browser-validation.json | actual UI workflow including reset/cancel/session isolation |

Use `COMMAND --help` for options. `--output-dir` selects exact report/export
destination. `--part val/test`, `--split all/timeblock/loso`, `--workers`,
`--preflight` and `--smoke` are evaluator options. Viewer also supports `--start`,
`--cycles`, `--gif-cycles`, `--stride`, and `--around-first-alert`.

## Fast smoke (reuse compatible artifacts)

```text
python -m phantomguard doctor --full
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/clean
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --seed 11 --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/attack
python -m phantomguard attack-eval --smoke --workers 1 --output-dir runs/smoke/evaluation
python -m phantomguard serve --port 8765
```

Open `http://127.0.0.1:8765`. A readiness 503 is not a pass. Playback can select a
recording, attack/type/level/seed, duration, play/pause/step/seek/reset, and inspect
actual verdicts/reasons/arrows/logs. T3/A0–A2 are disabled/rejected. Clips lacking
material/slot capacity produce clear errors; not an invented successful replay.
Green means not flagged. No ground-truth color overlay is implemented.

## Full real-data validation

When compatible artifacts exist, **reuse them**: doctor --full, clean-eval, then
attack-eval and exports/tests. Do not regenerate just to repeat measurements.
When data/feature/baseline contracts change or artifacts are absent, install the
offline/training dependencies and execute exactly this preparation order:

```text
python -m phantomguard baseline --loso
python -m phantomguard train --loso
python -m phantomguard calibrate --loso
python -m phantomguard clean-eval --workers 1 --output-dir runs/validation
python -m phantomguard attack-eval --workers 2 --output-dir runs/validation
python -m pytest -q --junitxml=runs/validation/tests.xml
```

There is no additional mandatory preprocessing step: ReplaySource reconstructs
frames directly from original CSVs. Processed fold baselines are generated during
preparation. Training/calibration never cross file/split boundaries. Time blocks
are fixed 60/20/20; LOSO uses first 80% of three recordings for training, their last
20% for validation, and the entire fourth for testing. No held-out tuning.

Evaluation uses seeds 11/22/33 × three repetitions; static/moving cases, T1–T4,
supported A0–A4, T3 provenance (earlier/training/unseen) and exact/translated
copies. All layers/fusion/leave-one-out use the same emitted stream. Labels join
final frame indices, never slots. Scene alarms and identification of a forged
object are distinct. Misses/censoring/exclusions remain in generated outputs.

Historical full sweep: 2,411 observed runs, 198 no-material attempts, 55 capacity
exclusions, 1,296 unsupported T3 requests. Fresh measurements are linked from
`docs/results/portable_validation.json`; historical values are not claimed as new.
The accepted clean under-one-alert/minute target failed (1.42 timeblock, 3.41 LOSO).
No UI/path work changes detector thresholds to make it pass. Historical prior
test inspection remains disclosed in `docs/decisions.md`.

Processing p99 and capture assembly delay are separate; the latter is about one
scan and depends on assumed ticks. Source I/O, offline IF scoring, JSON/network
and browser rendering are not detector latency. Resource/runtime measurements
in the final handoff identify machine, OS, workers and scope.

Selected report evidence is tracked in `docs/results`; disposable jobs stay in
runs. For publication copy only completed script outputs deliberately, preserve
their manifest/config/artifact/source hashes, and inspect staging before commit.

## Measured preparation and serving cost

The latest Windows validation on a 32-logical-CPU workstation completed baseline,
all five model bundles and calibration in about 232 seconds (30, 70 and 132 seconds).
Single-worker clean evaluation took 47 seconds; the full 24-worker attack sweep
took 1,987 seconds. These are observed workstation timings, not server guarantees.
The documented server commands use one or two evaluation workers to bound memory.
Full unit suites took 58–87 seconds on the tested Windows/WSL environments.

In the 2-CPU/2-GiB container, representative 600-cycle T1–T4 jobs took 9.3–9.8
seconds. A concurrent activity sample used 145 MiB; this is an observation, not a
peak-memory bound. The container and scheduler enforce their documented limits.
Serving uses restored models; these jobs never perform training or calibration.
