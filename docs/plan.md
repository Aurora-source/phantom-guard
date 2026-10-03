# Phantom-Guard offline prototype: plan

## Context
The repo contains only `CLAUDE.md` (the spec), `tools/decode.py` (decoder v2) and the four recorded CSVs. The
task is to build the offline prototype that CLAUDE.md describes (frame layer, replay source, baseline, attacker,
detector layers 1-5, evaluation and viewer) under its hard rules: causal only, no label leakage, attacker sampled
from real distributions, thresholds learned from data, test split never used for tuning. After approval, the first
step is to write this plan into `docs/plan.md`. The repo has no python deps yet; PyPI is reachable; 4 CPUs, 15 GB RAM.

## What I checked before planning (stdlib, read-only, all four files)
Matches CLAUDE.md: raw_len is always 8; header count == actual count; meas_counter steps by +1 everywhere and is
continuous across files; sync_status is always 0x01; dyn_prop is always 0; the reserved bits (b6 bits 4-3) are
always 0; `tools/decode.decode_object(raw_hex)` reproduces x/y/vx/vy/rcs in the CSV for **every** row (0
mismatches); median period is 332 ticks (p1-p99 is 328-336); arrival offset is 2-77; slots run 0x00-0x35 and never
repeat within a cycle; moving fraction is 5.0 / 6.7 / 1.6 / 6.3 %; the ghost at (18.6, 6.6) has vx ≈ -4.5 to
-4.75 in every file.

Contradictions and surprises (to go into `docs/plan.md` and `docs/decisions.md`):
1. **The short sync gaps are capture-start artefacts.** In every file they are exactly gaps 0 and 1 (59-193
   ticks). After that the minimum is ≥304 (304 appears only once, in front-back).
2. **meas_counter is not a clock.** Between files `sync_timestamp` jumps 159k-208k ticks (16-21 s at 0.1 ms per
   tick) while meas_counter advances by exactly 1. Hypothesis: with no node ACKing on the bus, the sensor stops
   making progress (CAN retransmits the pending frame). When capture resumes, its queued frames flush, which
   explains finding 1. Consequence: the cadence rule must ignore the first 2 gaps after a stream starts (a
   "warm-up" rule taken from data), and counter continuity must be checked separately from timing.
3. "The first cycle of a file may lack a full header" is **false** for these files: every cycle 1 has a full
   header. The handling stays in the code anyway and the count is reported.
4. Slot reassignments (jump > 1.0): emptyRoom has **12**, while CLAUDE.md says "at most 11". Minor; I'll report it.
5. The `source_file` column is `"live"` in every row. ReplaySource will take the label from the file name instead.
6. `data/raw/` is tracked in git, but CLAUDE.md says it is gitignored. **Your decision: untrack it**
   (`git rm --cached`, files stay on disk untouched, add `data/raw/` and `data/processed/` to `.gitignore`).
7. Only 41-49% of rows fall inside the default ROI (range ≤ 15). Both ghosts are outside it (ranges ≈ 19.7 and
   ≈ 37), so the ROI handles them as CLAUDE.md intends.
8. T4 overwrite conflicts with "no deletion of real frames". **Your decision: level-dependent.** A0/A1 send the
   forged frame alongside the real one with the same slot. A2+ replace the real frame in place, which is the same
   strong capability as A2 header forging. Logged as a deliberate worst-case assumption.
9. To verify in Phase 1: whether vy is ever meaningfully non-zero (the sensor may report only radial/Doppler
   velocity, which matches the side-to-side note); whether RCS only takes integer values (raw always even), in
   which case the attacker must match it; and RCS vs range.

## Cross-cutting design decisions
- **Package**: src layout, `pyproject.toml` (numpy, pandas, scikit-learn, torch CPU, matplotlib, pyyaml; dev:
  pytest). Torch is installed from the CPU wheel index (`--index-url https://download.pytorch.org/whl/cpu`). If
  that index is blocked, I fall back to PyPI and report it.
- **Reusing the decoder**: `frames.decode_object` loads `tools/decode.py` with `importlib` (by path) and calls its
  `decode_object`, so the formulas are not forked. `encode_object` is the exact inverse, using the scale/offset
  table in `frames.py`. A test checks `decode(encode(decode(raw)))` and the byte equality of
  `encode(decode(raw))` on all 442,724 rows.
- **Cycle assembly** (`detect/pipeline.py`): frames come in, and a cycle closes when the next 0x60A arrives (this
  is causal and adds one cycle of latency, documented). Detector state is per slot. Every frame gets a
  `frame_index` (its position in the stream). Verdicts are emitted per frame_index, and only the evaluator joins
  them with the labels file.
- **Config**: `configs/default.yaml` holds ROI, zones, tick_seconds=1e-4 (flagged as an assumption), window sizes,
  M-of-N 3/5, seeds, moving threshold 0.30, split fractions, and attack parameter ranges from CLAUDE.md (lifetimes,
  counts). `configs/baseline.json` is generated, and every entry is `{value, rule, split, fp_rate_val}`.
- **Splits** (`eval/splits.py`): per-file contiguous cycle blocks 60/20/20, plus LOSO (4 folds). Baseline and
  learned thresholds use train (and validation for calibration). Test is used only by `run_attack_eval.py`.
- **ROI**: the protocol layer checks every frame. The kinematic, replay and AE layers judge only objects inside
  the ROI; objects outside it are counted and reported as "out-of-ROI", never flagged. The attacker places
  T1/T2 objects inside the ROI.

## Phases (pytest, commit and push after each, plus a short summary printed each time)

**Phase 0: scaffold + frames.** Layout from CLAUDE.md, `pyproject.toml`, `.gitignore`, `configs/default.yaml`,
`docs/plan.md`, `docs/decisions.md`; untrack data/raw. `frames.py`: `Frame(can_id, data, timestamp_ticks)`,
`RadarObject` dataclass, `decode_object`, `encode_object`, `build_header(count, meas_counter, status)` (byte3 =
0x00), `parse_header`. Tests: all-rows round trip, CSV columns vs decoder, header round trip, and a report of any
byte differences (the reserved bits are expected to be the only possible source).

**Phase 1: sources + baseline.** `io/source.py` (`FrameSource` protocol), `io/replay.py` (`ReplaySource(csv,
cycle_range=None)`, which rebuilds headers and reports malformed/short/first-cycle counts), `io/live.py` stub.
`stats/tracks.py`: per-slot track linking with reassignment detection (jump threshold learned from data).
`stats/baseline.py` + `scripts/learn_baseline.py` (train split only): period/jitter with the warm-up rule,
arrival-offset window (p0.1-p99.9 plus margin rule), objects/cycle, distribution of slots taken by new tracks,
speed/acceleration envelopes of moving in-ROI tracks, birth stats (position, initial speed), per-track RCS std,
range-rate vs reported radial velocity residual over a window (so the check survives 0.2 quantisation: 1 m/s ×
33 ms = 0.033 per cycle, so a one-cycle check is meaningless). Prints each value next to the CLAUDE.md claim and
flags mismatches. RCS-vs-range analysis: overall Spearman correlation plus per-track regression slopes, written
to `docs/results/rcs_vs_range.md`.

**Phase 2: attacker.** `attack/levels.py` (A0-A4 capability flags), `attack/scenarios.py` (T1-T4 generators,
seeded from config), `attack/injector.py` (`AttackedSource(source, attacker, labels_path)`). Everything forged is
built with `encode_object`/`build_header`, and slots, offsets, RCS and speeds come from `baseline.json`. A0:
random times/slots/bytes. A1: offsets inside the window, free slot, header not fixed. A2: rewrites the header
count (forged header labelled). A3: constant-velocity path with consistent velocity fields, RCS sampled per track
with real jitter, and an integer RCS grid if the data has one. A4: T3 replay segments, or T1 paths built from real
recorded moving tracks. T3 sources are reported separately: (a) earlier in the same stream, (b) the training
portion, (c) another file's unseen portion. T4 follows the level-dependent rule above. Tests: A0 fails protocol
checks; A2+ passes them; forged slot range, offsets and RCS fall inside the real ranges (KS-style tests against
train); labels never appear in the frames or the pipeline.

**Phase 3: physics gate.** `detect/protocol.py` (count mismatch, duplicate slot, counter continuity, cadence with
warm-up, arrival offset, length/ID), `detect/kinematic.py` (windowed range-rate vs radial velocity, speed/accel
envelope, implausible birth (scored), RCS stability, co-location), `detect/replay_fp.py` (quantised
translation-invariant displacement+velocity k-gram hashes for moving tracks only, matched against a library from
training plus the stream's own past and concurrent tracks; a minimum-motion-complexity gate stops constant-velocity
real walks from matching). All thresholds come from train, and their FP rate on validation goes into
baseline.json. Clean FP is reported per object-cycle and per minute, then detection by type × level. Latency test:
p99 per cycle < 10 ms on about 25 objects.

**Phase 4: learned layer.** `detect/autoencoder.py`: per-track windows (N from config, default 10) of [dx, dy,
radial v, vx, vy, rcs, range] normalised with training stats; small MLP AE; CPU training with an epoch/time cap in
`scripts/train.py`. Threshold = validation-clean percentile. Isolation-forest baseline on the same windows; report
AUROC for both and keep the better one as the layer. `detect/fusion.py`: hard violations flag immediately,
otherwise M-of-N per track. Both splits evaluated; time-to-detect measured.

**Phase 5: report.** `eval/metrics.py`, `eval/report.py`, `scripts/run_attack_eval.py` (multiprocessing, fixed
seeds, several runs) write CSVs plus `docs/results/summary.md`: the type × level × layer matrix, ablations
(leave-one-layer-out), static vs moving, time-split vs LOSO, alerts/min, time-to-detect, evading levels stated
explicitly, and the deck-claims section (kinematic plausibility, timing signature, RCS-vs-range, AE, "no
labelled attack data").

**Phase 6: viewer.** `viz/console.py` + `scripts/replay_demo.py`: matplotlib (interactive window at about real
time, or headless Agg export), top-down x/y scatter, quiver velocity arrows, green/red with reason codes, alert
log panel. PNG and GIF (PillowWriter) go to `docs/results/`. The choice is logged in decisions.md.

## Verification
- `pytest -q` after every phase (data tests skip if the CSVs are absent; a latency test is included).
- `python scripts/learn_baseline.py`, `python scripts/train.py` and `python scripts/run_attack_eval.py` all run end
  to end. Every number in `docs/results/` is produced by them.
- `python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --export` produces
  the PNG and GIF.
- Final report: what works, what doesn't, the headline table, and the next 3 steps.

## Post-approval updates
Findings during implementation changed some details (gap bridging in the tracker, learned `rr_scale`, a
range-conditional RCS band, burst-contiguity and range-order protocol checks). Each is logged with its evidence in
`docs/decisions.md`.

## Status (2026-10-03, second round)
- Done: Phases 0-1; detector layers 1-5 (protocol, kinematic, replay fingerprint, autoencoder plus offline isolation
  forest, M-of-N fusion); validation calibration; clean-data evaluation (`scripts/run_clean_eval.py` ->
  `docs/results/clean_eval.md`); latency test; matplotlib viewer with PNG/GIF export.
- Not done: the Phase 2 attacker (only level flags and data pools exist), so there are no attacked-data results,
  no type x level x layer matrix, and no `run_attack_eval.py` / `summary.md`.
- Commands: `python scripts/learn_baseline.py --loso && python scripts/train.py --loso && python scripts/calibrate.py --loso
  && python scripts/run_clean_eval.py && python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --export --around-first-alert`
