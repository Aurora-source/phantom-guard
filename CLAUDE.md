# Phantom-Guard

Detects fabricated CAN frames injected into an automotive radar stream (Nanoradar SR75, 77 GHz,
classic CAN 500 kbps). Built for Hack Sprint (MAHE, 17-18 Oct 2026), Track 3 / PS 3.1, whose
brief says "innovation is the priority, not simply building another existing security tool".
Owner: Krishna. Solo/small team, 24-hour build window, so favour simple, testable, demoable.

Core idea: a forged frame can copy the format but not a plausible past. Every radar object is
judged against (1) protocol rules, (2) physics/kinematics, (3) learned-normal behaviour. Authentic
objects render green, flagged objects render red, each with a reason.

## Current phase: offline prototype
- Work only from the four recorded CSVs in `data/raw/`. No live sensor, no Tailscale, no hardware.
- Write everything against the `FrameSource` interface so a live source can be dropped in later.
- Out of scope now: live CAN/ZLG capture, Tailscale, RF-layer spoofing, CAN-FD, bus-off/suppression
  attacks (an attacker deleting real frames).
- The current owner assignment expands this scope with a CPU browser prototype around recorded
  replay and simulated CAN injection. Live hardware and actual server deployment remain excluded.

## Hard rules (do not break these)
1. **Causal only.** The detector sees one scan cycle at a time and may use only past cycles.
   No look-ahead, no whole-file statistics at inference time. Training may be offline.
2. **No label leakage.** Ground-truth attack labels live only in the attacker's output files and the
   evaluator. They never enter detector inputs, features, or thresholds.
3. **No simulation artefacts.** Every attacker choice (slot IDs, arrival timing, RCS, speeds,
   positions) must be sampled from distributions measured on real data. If the attacker is
   distinguishable from real data by something unrelated to its attack, results are meaningless.
4. **Thresholds come from data.** Derive every threshold from clean training/validation data,
   record it in `configs/baseline.json` with the percentile or rule used, and report the false-positive
   rate it gives on held-out clean data. No magic numbers in detector code.
5. **Never tune on the test split.** Splits are defined in `eval/splits.py` and are fixed.
6. **Do not silently drop anything.** Count and report malformed, short, duplicate or out-of-ROI rows.
7. **Never modify `data/raw/`.** Treat it as read-only.
8. **Results are generated, not typed.** Every number in `docs/results/` is produced by a script.
   If an attacker level evades every detector, report that; do not hide it.
9. **If the data contradicts this file, stop and report it** instead of silently picking one.
   Add the finding to `docs/decisions.md`.
10. Do not use `cycle_num`, `meas_counter` or the numeric slot ID as a learned feature (see leakage notes).

## Data (`data/raw/`)
Output of our decoder (`tools/decode.py`, v2). One row per radar object. Recorded back to back with the
same sensor mounting; `meas_counter` is continuous across files (43529 to 62623) in this order:

| File | Cycles | Rows | Scenario |
|---|---|---|---|
| emptyRoom.csv | 4378 | 105,377 | No test subject; one person working in the background |
| onePersonMovingFrontAndBack.csv | 4127 | 93,424 | One person walking toward/away from sensor |
| onePersonMovingSideToSide.csv | 4993 | 113,581 | One person moving across the field of view |
| multiplePeopleChaotic.csv | 5597 | 130,342 | Several people moving unscripted |

All four are authentic radar output. "Clean" means "no attack injected", not "empty".

Columns: `source_file, cycle_num, scan_counter, meas_counter, sync_timestamp, sync_status,
obj_count_header, obj_count_actual, slot, obj_timestamp, raw_len, raw_hex, x_m, y_m, range_m,
azimuth_deg, vx_mps, vy_mps, speed_mps, is_moving, dyn_prop, rcs_dbsm`.
`slot` and `sync_status` are hex strings (`"0x04"`). `raw_hex` is the 8 original bytes and is the
ground truth; the decoded columns are rounded to 2 dp.

### Frame model (inferred from the ARS408 protocol; confirmed only by plausibility)
- `0x60A` header, one per scan cycle: byte0 = object count, bytes1-2 = measurement counter (big-endian
  16-bit), byte4 = status (always 0x01). Byte3 is not stored in the CSVs; reconstruct as 0x00.
- `0x60B` object, one per target, 8 bytes, **bit-packed**:
  `slot=b0 | x: 13 bits = (b1<<5)|(b2>>3) | y: 11 bits = ((b2&7)<<8)|b3 | vx: 10 bits = (b4<<2)|(b5>>6) |
  vy: 9 bits = ((b5&0x3F)<<3)|(b6>>5) | dyn: 3 bits = b6&7 | rcs = b7`.
  Scaling: `x=raw*0.2-500`, `y=raw*0.2-204.6`, `vx=raw*0.25-128`, `vy=raw*0.25-64`, `rcs=raw*0.5-64`.
  Bits 4-3 of byte 6 are unused. Position resolution 0.2, velocity resolution 0.25 m/s.
- The attacker must build frames by **encoding** these fields back to bytes, so forged frames take
  the exact same decode path as real ones. `decode(encode(decode(raw)))` must equal `decode(raw)`.
- Sign convention (observed): `vx < 0` means approaching the sensor.
- There is no elevation. "4D" in the pitch means x, y plus the velocity vector (vx, vy).

### Measured facts (verified on the full files)
- Capture integrity is perfect: `raw_len` is always 8, `obj_count_header == obj_count_actual` in every
  cycle, `meas_counter` increases by exactly 1 per cycle, `sync_status` is always 0x01, `dyn_prop` is
  always 0 (the SR75 does not populate it; do not use it).
- Cycle period is 332 ticks (median), usual range 332-338. A few much shorter gaps exist (min 59-73
  ticks); investigate where they occur before setting the cadence rule.
- Object frames arrive 2 to 77 ticks after their cycle's header (median about 30), so every object of a
  cycle sits in roughly the first quarter of the period.
- 11-32 objects per cycle (median 23-24). Slot IDs span 0x00-0x35. A slot never repeats within a
  cycle. A slot is effectively a stable track ID: reassignment (>1.0 position jump between consecutive
  cycles) happens in at most 11 of about 100k appearances per file.
- Most points are static clutter. Moving points (speed >= 0.30 m/s) are 5-7% of rows in three files
  and 1.6% in side-to-side.
- Static-clutter RCS (empty room) is 14-20 dBsm (p5-p95), people are about 16-21, ghosts about 13-14.
  RCS therefore separates classes weakly.
- Two persistent **ghost artefacts** appear in every file including the empty room, near
  (x, y) = (18.6, 6.6) and (35, 13). They report 3.5-4.8 m/s approach, move consistently with that
  speed for about 11 cycles, then respawn at the same place. They are real radar output, not attacks.
  Do not flag them as attacks. Handle by region of interest, not by hard-coding positions.
- Near zone (range <= about 4 units) holds the test subject: movers in 0% / 10.7% / 5.1% / 22.6% of
  cycles (empty / front-back / side / chaotic). Mid zone (4-15) holds people elsewhere in the lab.
- Side-to-side motion has almost no radial velocity, so it looks static in `vx`. Use position change
  over cycles, not only reported velocity.
- Static clutter positions are not stable enough across files to reuse a background map (only 3-12
  persistent static positions per file, Jaccard overlap 0.12-0.5). A background map would need
  per-session calibration. Treat static-phantom detection as an open problem.

### Unverified assumptions (keep them configurable, flag where they matter)
- Distance units. A tape-measure test suggested coordinates are not true metres. Zone limits and ROI
  therefore live in `configs/default.yaml` (default ROI: `range_m <= 15`, provisional). Never hard-code 3 m.
- Tick duration. If one tick is 0.1 ms the cycle is 33.2 ms (about 30 scans/s). Supporting evidence: ghost
  displacement matches speed x 33.2 ms. Keep `tick_seconds` in config.
- The bit-packed layout itself. Plausible and consistent, not yet confirmed on hardware.
- Whether RCS depends on range. The deck currently claims it does; **test this** and report. If it
  does not, demote to a per-track RCS stability check.

## Frame source interface
`FrameSource` yields `Frame(can_id, data: bytes, timestamp_ticks)` in arrival order.
- `ReplaySource(csv_path)`: rebuilds a `0x60A` header per cycle from `meas_counter`,
  `obj_count_header`, `sync_status`, then the `0x60B` frames from `raw_hex` with their `obj_timestamp`.
  The first cycle of a file may lack a full header; handle and report it.
- `LiveSource`: stub only (raises `NotImplementedError`). Pipeline code must not import anything
  replay-specific.
- `AttackedSource(source, attacker)`: wraps any source, injects forged frames, and writes a separate
  labels file (`frame_index, is_attack, attack_id, attack_type, level`). Labels never flow downstream.

## Detector (`src/phantomguard/detect/`)
Each layer returns per-object `reason_codes` and a score so the console can say why an object is red.

1. **Protocol integrity (per cycle)**: header count vs frames received, duplicate slot in a cycle,
   counter continuity, cycle cadence, object arrival offset inside the learned window, frame length/ID.
   Expected to be near-zero false positives on clean data; verify.
2. **Physics / kinematics (per track, keyed by slot)**: reported velocity vs position change over dt
   (tolerance must account for 0.2 m quantisation), acceleration and speed envelopes from real moving
   tracks, plausibility of track birth (learned, a feature not a hard rule; real tracks also start
   abruptly), per-track RCS stability, implausible co-location of two objects.
3. **Replay fingerprint**: exact or near-exact repeat of a multi-cycle trajectory shape. Match on the
   **translation-invariant** sequence (displacements and velocities), not absolute position, so a shifted
   copy is still caught. Static clutter repeats legitimately; only test moving tracks.
4. **Learned normal**: small windowed autoencoder in PyTorch on per-track windows (N cycles of
   position delta, velocity, RCS). Trainable on CPU in minutes. Score = reconstruction error;
   threshold = percentile of validation-clean error. A simple fallback (isolation forest) is acceptable
   if the autoencoder does not beat it, but report both.
5. **Fusion + alerting**: any hard protocol/physics violation flags immediately. Alert on a track when
   at least M of the last N cycles are flagged (default 3 of 5, configurable) to suppress flicker.

Latency budget: a full cycle (about 25 objects) must run well under 10 ms on a laptop CPU (p99, rules +
autoencoder inference). Test it.

## Attacker (`src/phantomguard/attack/`)
This is our red team, used to demo and to measure the detector honestly.

Attack types:
- **T1 phantom**: 1-3 fake objects, static or moving, living 20-150 cycles inside the ROI.
- **T2 flood**: 10-30 short-lived fake objects in a burst (10-60 cycles) to disturb the scene.
- **T3 replay**: re-inject a real moving-track segment (>= 20 cycles) recorded at another time or file;
  exact copy, and a translated copy.
- **T4 shift/overwrite**: take a real moving track and add a growing position offset (drift) for N cycles.

Attacker capability levels (each level also gets everything the lower levels get right):
- **A0 naive**: random frames at random times, random slots and values.
- **A1 timing-aware**: arrives inside the real arrival window, uses a free slot; header count not fixed;
  random kinematics.
- **A2 protocol-clean**: also keeps header count, counter, slot uniqueness and cadence valid. This models an
  attacker that can also forge the header, a deliberately strong worst-case assumption. Kinematics still
  naive (positions jump, velocity fields unrelated to motion).
- **A3 physics-aware**: A2 plus smooth constant-velocity paths, velocity fields consistent with
  position change, RCS stable and sampled from the real distribution.
- **A4 data-aware**: A3 built from real recorded tracks or learned dynamics (T3 is inherently A3/A4).

The headline result is an attack-type x level x detector-layer matrix. It should show which layer
catches which attacker, including the levels that evade us.

## Evaluation (`src/phantomguard/eval/`)
- Two splits, both reported: (a) per file, first 60% train, next 20% validation/threshold calibration,
  last 20% test, split by contiguous time blocks, never random rows; (b) leave-one-scenario-out
  (train on three files, test on the fourth).
- Inject attacks only into validation/test segments, with fixed seeds, several runs each.
- Metrics: false-positive rate on clean test data per object-cycle **and** persistent alerts per minute;
  detection rate by type x level; time-to-detect in cycles; AUROC for the learned score;
  per-layer ablation. Aim for under 1 false alert per minute on clean held-out data after persistence
  filtering; if not reachable, report why.
- Evaluate static and moving tracks separately. Static phantoms are expected to be the hardest.
- Output: `docs/results/summary.md` plus CSVs, all generated by `scripts/run_attack_eval.py`.

## Repo layout
```
CLAUDE.md
pyproject.toml
configs/default.yaml        ROI, tick_seconds, window sizes, M-of-N, seeds
configs/baseline.json       learned thresholds (generated by scripts/learn_baseline.py)
data/raw/                   the four CSVs (gitignored, read-only)
data/processed/             caches (gitignored)
tools/decode.py             existing decoder v2; reuse its decode logic, do not fork the formulas
src/phantomguard/
  frames.py                 Frame, encode_object, decode_object, header build/parse
  io/replay.py, io/live.py  FrameSource, ReplaySource, LiveSource (stub)
  stats/baseline.py         learns cadence, arrival window, kinematic envelopes
  detect/                   protocol.py kinematic.py replay_fp.py autoencoder.py fusion.py pipeline.py
  attack/                   injector.py levels.py scenarios.py
  eval/                     splits.py metrics.py report.py
  viz/console.py            replay viewer: green/red, velocity arrows, reason labels
scripts/                    learn_baseline.py train.py run_attack_eval.py replay_demo.py
tests/
docs/decisions.md           dated log of every judgement call and every data surprise
docs/results/
```

## Commands
- Setup: Python 3.11; pinned dependency/CPU installation is documented in README.md and docs/setup.md.
- Tests: `pytest -q` (must stay green; add tests with each module)
- Baseline: `python scripts/learn_baseline.py`
- Train: `python scripts/train.py`
- Attack evaluation: `python scripts/run_attack_eval.py`
- Demo viewer: `python scripts/replay_demo.py --file onePersonMovingFrontAndBack.csv --attack T1 --level A2`

## Conventions
- Type hints, small pure functions, dataclasses for frames/objects. numpy/pandas/scikit-learn/PyTorch (CPU)/
  matplotlib are fine; ask before adding heavy dependencies.
- Seeds and window sizes in config, never inline. Log counts of everything that is filtered or skipped.
- Notebooks are for exploration only; code that matters lives in `src/`.
- Commit after each phase with a message saying what was verified.

## Leakage and pitfalls to avoid
- Files are consecutive segments of one session, so `cycle_num`, `meas_counter` and `sync_timestamp`
  identify the file. Never feed them to a model.
- Slot ID number is not meaningful physics. Use it only to link a track across cycles.
- Attacker artefacts that must not exist: forged frames outside the real arrival window (below A1),
  slot IDs real data never uses, perfectly round values, RCS outside the real range.
- Flagging the two ghost artefacts, or the lab co-worker, as attacks would be a false positive.

## Deck claims that depend on this work (report back on each)
Slide 4 says: kinematic plausibility, timing signature, RCS-vs-range consistency, learned-normal
autoencoder, "no labelled attack data needed". For each, record in `docs/results/summary.md` whether the
data supports it, at what false-positive rate, and which attacker levels it catches.
