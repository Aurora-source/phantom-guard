# Decisions and data surprises

## Final portable validation (2026-10-05)

- Upstream PR #5 merged at `fc88afe` during validation. Merged it with both
  histories preserved (`7eb3ec2`), retained its plain-git/relative-path tests and
  one-argument reporter interface, and moved its Arch guidance into setup-arch.md
  using the tested Python 3.11 pins. No contributor branch or original worktree changed.
- Full fixed-code matrix at `7eb3ec2`: 2,411 eligible runs; 198 no-material,
  55 stable-slot capacity exclusions, 1,296 unsupported T3/A0-A2 requests. Matching
  checkpoints from twelve workers were resumed with twenty-four after resource
  sampling (39% CPU and over 10 GiB free on 32 logical CPUs). Per-run concurrency
  remains recorded. Interrupted/migrating runs are not successful validations.
- Final code `91a79bb` fixes explicit `--root` provenance when no environment root
  exists, canonical baseline/sidecar report references, and explicit UTF-8 text
  ingestion/export. Detector, attacker, features, metrics and thresholds are
  unchanged. Fresh clean CSVs and all 36 smoke layer results match the full matrix
  exactly; generated evidence preserves its actual benchmark SHA and records the
  metadata-only report conversion rather than pretending it ran at another SHA.
- Final suites: 168 passed, zero skipped on Windows 11/Python 3.11.9 and native
  Ubuntu 26.04.1 WSL/Python 3.11.16, including normal wheel installations in paths
  with spaces. Root-only CLI from outside each checkout, import, doctor, PNG/GIF,
  full clean evaluation, four-cell real attack smoke, browser reset/cancel/session
  isolation and pinned Python 3.11.17 Debian container checks passed. One WSL PyPI
  build request timed out; rebuilding the same pinned cached packages offline
  resolved it. Initial missing-Torch, migration and harness failures remain failures
  in history; successful retries are identified separately.
- Clean results: 1.42/min time-block, 3.41/min LOSO. Ablations/reasons implicate
  genuine RCS/envelope variability and repeated motion, plus cross-scenario
  protocol/envelope generalization. No held-out threshold tuning follows. The
  under-one performance criterion remains NOT MET. Worst completed attacked-run
  processing p99 is 5.41 ms; separate single-worker clean p99 max is 1.98 ms.
  Assembly p99 remains 33.5-33.6 ms under the assumed tick duration; eighteen
  completed runs fully evade scene detection. All misses/denominators stay visible.
- Local private bundle contains all four unchanged CSVs, fifteen artifacts, four
  fold baselines, portable configuration and required real reports; no environment,
  credentials or temporary jobs. Payload text metadata is LF while original input
  bytes and model binaries are unchanged. Exact final checksum/restoration checks
  and future server instructions are recorded in server-handoff.md. Actual home
  server deployment, live hardware, Arch, public proxy integration and human
  usability are not claimed.

## Portable hosted prototype assignment (2026-10-04)

- PR #4 was accepted at upstream `89b182ff5777fe11c1676443085b36a52df0e94c`.
  Created sibling `phantom-guard-portable-hosted-prototype` on
  `codex/portable-hosted-prototype` from that SHA. Original main checkout and
  prior implementation worktree/datasets remain unchanged. Fork main was still
  `1f5d2ee`; preserve it and publish a new feature branch. Contributor branch
  `1175a2b` has an in-progress Arch README/report portability correction; inspected
  read-only, not taken over. The accepted main has no newer detector policy.
- Owner explicitly expands the old web-UI exclusion with a recorded-data browser
  prototype. Live capture/hardware and actual home-server deployment remain out
  of scope. Existing Frame/attacker/detector and label-sidecar contracts are retained.
- One portable path mechanism anchors to an explicit absolute workspace or editable
  checkout location, never cwd. CLI overrides environment then YAML/defaults.
  Normal wheel installation includes decoder v2 and preparation/viewer commands;
  compatibility tools/scripts delegate. No duplicated decoding formulas or
  undocumented PYTHONPATH. Runtime excludes Torch/sklearn/pandas; offline extras
  and CPU training locks are separate. Supported interpreter is explicitly 3.11.
- `dataset` is an external import source, not another pipeline root. Byte-verified
  immutable import targets `data/raw`; generated fold baselines stay in processed,
  models in models, disposable outputs in runs and archives in ignored bundles.
  Tracked READMEs make empty directories visible; initialization is idempotent.
- Existing five trained/calibrated bundles pass strict feature/data/split/baseline
  provenance checks; reuse them. No retraining, calibration or threshold/model
  selection follows these new held-out evaluations. Fixed 60/20/20 splits cannot
  be reconfigured into training data. Prior test-inspection disclosure remains.
- Browser jobs use bounded spawned processes and fresh detector/RNG state. They
  show causal test-segment verdicts; seeking displays immutable computed outputs.
  Offline attack generation finalizes ordinary-frame sidecars separately. Browser
  requests never read labels, train models or claim an attack evaluation rate.
  Real published report filters retain recording/type/level/seed/run provenance.
- Windows progress polling exposed a transient sharing violation when atomically
  replacing JSON snapshots. Bounded retries preserve complete old/new snapshots;
  the regression explicitly simulates this failure. Cancellation also handles a
  job-creation request still in flight. Unsupported options have stable values,
  are disabled in the UI and rejected independently by the API.
- WSL initially failed mounting its VHD with E_ACCESSDENIED and Docker was stopped.
  The owner repaired those services; subsequent ordinary calls run Ubuntu 26.04.1
  WSL and Docker Desktop Linux. System Python is 3.14.4, so validation uses isolated
  CPython 3.11 instead. Container runtime is pinned Debian/Python 3.11.17, not an
  Ubuntu image. Native WSL and container checks are reported separately.
- Initial accepted-source test attempt: 138 passed, one failed because the new
  environment lacked Torch. CPU Torch installation resolves that dependency.
  Intermediate migration/UI failures are fixed and are not counted as passing.
  Latest full suite before the final split regression: 162 passed, no skips.
  Fresh clean evaluation still gives 1.42/min time-block and 3.41/min LOSO;
  under-one target is not met. Final clean-clone/evaluation/bundle evidence follows
  in the generated portability report and server handoff, not invented here.

Dated log of every judgement call and every place the data disagreed with CLAUDE.md.
Newest entries are appended at the bottom of each day.

## 2026-10-03

### Data checks before any code (stdlib scan of all 442,724 rows)
- **Confirmed**: raw_len is always 8; header count == actual count in every cycle; meas_counter steps by exactly 1
  and is continuous across files; sync_status is always 0x01; dyn_prop is always 0; tools/decode.py reproduces
  the CSV's decoded columns for every row.
- **Short sync gaps = capture start.** The 59-193 tick gaps are exactly the first two inter-cycle gaps of every
  file and nowhere else (the next smallest is 304, once). Decision: the cadence check skips the first
  `cadence_warmup_cycles: 2` gaps of a stream. Learned cadence limits come from the remaining gaps.
- **meas_counter is not a clock.** Between consecutive files sync_timestamp jumps 159k-208k ticks (16-21 s at the
  assumed 0.1 ms per tick) while meas_counter advances by exactly 1. Most likely explanation: with no node ACKing
  on the bus between captures, the sensor's pending frame is retransmitted and its scan loop does not advance.
  When capture resumes, the queued frames flush, which also explains the short gaps above. Consequence: counter
  continuity and cadence are separate checks. Not verified on hardware.
- **CLAUDE.md says "the first cycle of a file may lack a full header"; it does not here.** Every file's cycle 1 has
  a full header and a matching count. ReplaySource still handles and counts header-less leading objects.
- **Slot reassignment:** emptyRoom has 12 jumps > 1.0 out of 104,007 appearances (CLAUDE.md: "at most 11").
  The others are 3, 2, 9. Minor; reported.
- **source_file column is "live" in every row**, not the file name. ReplaySource labels streams by file name.
- **data/raw was committed to git**, but CLAUDE.md says gitignored. Owner chose to untrack it (`git rm --cached`).
  The files on disk were not touched.
- **ROI vs ghosts:** only 41-49% of rows have range <= 15. Both ghost artefacts (ranges ~19.7 and ~37) fall
  outside the default ROI, so ROI handling covers them as CLAUDE.md intends.
- **New: frames within a cycle are sent back to back.** Consecutive 0x60B frames in a cycle are exactly 2 or 3 ticks
  apart (one 5-tick gap per file). That is the bus time of one 8-byte classic CAN frame at 500 kbps
  (~111-130 bits ≈ 0.22-0.26 ms), which independently supports tick = 0.1 ms. It is a strong timing signature:
  a frame that arrives after the burst has ended leaves a visible gap. Physical consequence for the attacker
  model: injected frames must be **serialised** onto the bus (a frame sent mid-burst delays the real frames
  after it). AttackedSource models this; anything else would be a simulation artefact (hard rule 3).
- **New: objects are transmitted in ascending range order.** 78-96% of cycles are strictly range-sorted on the
  decoded (quantised) values, and every violation is <= 0.26, which is within position quantisation (0.2 in x and y).
  So the sensor sorts by its internal unquantised range. This is a protocol-level order signature. The tolerance is
  learned from train.
- **RCS is always an integer** (raw byte always even). Forged RCS must lie on the integer grid, otherwise it is an
  attacker artefact.
- **vy is populated** in 3-9% of rows, so the sensor does report a lateral component. The kinematic check
  compares range-rate against the reported *radial* velocity, which covers side-to-side motion (no radial velocity,
  no range change).

### Judgement calls
- **T4 shift/overwrite (owner decision):** level-dependent. A0/A1 send the forged frame alongside the real one with
  the same slot (the real frame stays, so it shows as a duplicate slot). A2+ replace the real frame in place. That
  is the same capability as A2's header forging and is logged as a deliberately strong worst-case assumption. It
  does not delete tracks, so it is not the out-of-scope suppression attack.
- **Range-order and burst contiguity belong to A3+.** CLAUDE.md lists A2 as keeping header count, counter, slot
  uniqueness and cadence. Transmit order depends on the forged range value, so we treat it as part of the
  "physics-aware" A3. A1/A2 insert at a time-based position (serialised); A3+ insert at the range-sorted position.
  The matrix reports the order/contiguity checks separately, so the effect of this choice is visible.
- **dyn_prop and the reserved bits are used only as constant-field checks** (always 0 in real data). They are not
  used as motion features (CLAUDE.md: "do not use it" for motion).
- **Header DLC is unknown** (not stored in the CSVs). Rebuilt as 5 bytes (bytes 0-4) and kept configurable. The header
  length check therefore only requires >= 5 bytes.
- **Decoder reuse:** `frames.decode_object` loads `tools/decode.py` by path and calls its `decode_object`.
  `encode_object` is the exact inverse, checked byte for byte over every recorded row.
- **PyTorch:** the CPU wheel index (download.pytorch.org) is blocked by the environment proxy (HTTP 403), so torch
  was installed from PyPI (the CUDA build, which runs on CPU). No code impact.

### Phase 1 findings (baseline from the train split; full-file claim checks in docs/results/baseline_claims.md)
- **CONTRADICTION: the ghosts do not move with their reported speed.** CLAUDE.md says the ghosts "move consistently
  with that speed for about 11 cycles". Measured: ghost-like tracks report a median 4.2-4.6 m/s approach, but their
  median net displacement is 0.04 per cycle, where the reported speed implies 0.14-0.15. Each appearance lasts a
  median of 5-6 cycles, then the ghost respawns at the same spot with a new slot. Raw dump: at (18.6, 6.8) with
  vx = -5.0 the position holds for 4-5 cycles. No impact on the detector, because the ghosts are outside the
  default ROI. If the ROI is ever widened past ~19, they would fail the range-rate check.
- **Values hold for several cycles.** On moving in-ROI tracks, 36-76% of consecutive cycles (p5-p95 across tracks,
  median 57%) repeat x, y, vx and vy exactly. Position changes on a median 21% of steps, velocity on 39%. The
  sensor's tracker does not refresh every object every cycle. A constant-velocity forgery (A3) will not show this
  pattern; the learned layer sees it through per-window features.
- **Track fragmentation:** a slot disappears for one cycle and returns at nearly the same position 250-900 times per
  file. Decision: the tracker bridges gaps of up to `max_gap_cycles: 1`.
- **Range-rate scale is 0.79, not 1.** Over 15-cycle windows of moving people, range-rate from positions is
  0.79 × reported radial velocity (corr 0.93; first reported as 0.81 before the merge bug below was fixed). The CAN frame spacing supports tick = 0.1 ms, so the most likely
  cause is a distance unit about 1.24 m long (consistent with the tape-measure doubt), though a velocity scale error
  cannot be excluded offline. Decision: learn `rr_scale` from train and use it in both the kinematic check and the
  A3/A4 attacker. Assuming 1 would make real people look inconsistent and make the attacker unrealistic.
- **RCS vs range: SUPPORTED at population and same-person level, untestable per track.** In-ROI Spearman rho is
  -0.87. The same person walking toward/away gives rho -0.64 (p ≈ 1e-129, about -0.15 dB per unit range). The
  per-track test has only 5 qualifying tracks (median rho 0.04): underpowered, reported anyway. RCS is tight within
  a 1-unit range bin (3-7 dB band). Decision: add a range-conditional RCS band (`rcs_by_range`) to the
  kinematic layer, besides per-track RCS stability. A3 samples RCS from the in-ROI marginal distribution
  (CLAUDE.md: "sampled from the real distribution"). A4 samples conditionally on range. The matrix shows the difference.
- **Thresholds: the rule, and the val exceedance it gives,** are recorded for every entry in configs/baseline.json.
  Timing limits use train [min, max] ± 2 ticks (physical envelopes, near-zero val exceedance). Kinematic limits use the
  train q0.999 (val exceedance 0.04-0.3% per window).
- **Moving training data is thin:** only 95 of 1,259 in-ROI tracks in train are "moving" (>= 10 moving cycles;
  first reported as 53 of 575 before the merge bug below was fixed).
  Kinematic envelopes and the learned layer's view of motion rest on this small set. This limits how much the
  learned layer can say about moving tracks.

### Clean-data detector work (Phases 3, 4, 6 without an attacker)
- **Bug found and fixed: `SegmentStats.merge` lost tracks.** It keyed merged tracks by `id()` of a temporary
  object. CPython reuses those ids, so tracks from different segments overwrote each other (time-block train
  windows came out as 58,907 in one run and 32,789 in another). It is now re-keyed sequentially, with a
  regression test (merged track count == sum over segments). Every baseline, model and result was regenerated
  afterwards. Corrected Phase 1 numbers: `rr_scale` 0.789 (was 0.806); moving train tracks 95/1,259 (was 53/575).
  The RCS-vs-range verdict is unchanged.
- **Attacker not built.** Phase 2 scenario/injection code was not written, so there is no attacked data.
  Detection rate by type x level, time-to-detect, AUROC and the type x level x layer matrix are **not reported**.
  Everything in `docs/results/clean_eval.md` is false-positive behaviour on clean data only.
- **Operating point calibrated on validation, after one look at test.** The first full run used 99.9th-percentile
  soft thresholds. Clean alert rates were 15.6/min (val), 22.7/min (test) and 21.0/min (LOSO); the target is < 1/min.
  On validation, 27 of 33 alert episodes were on objects with zero *reported* velocity, mostly `RR_RESID`:
  real people move in position while reporting zero velocity. Decision: choose the soft quantile (kinematic and
  AE) as the smallest candidate in {0.999, 0.9995, 0.9999, 0.99995, 1.0} with validation alerts/min < 1
  (`scripts/calibrate.py`; the table is recorded in the baseline JSON). This is calibration on the validation split,
  which CLAUDE.md prescribes. It was prompted by validation numbers, but test had already been evaluated once
  under the old setting, and that is recorded here. Time-block chose q0.9999 (val 0.47/min = 1 event in 2.1 min).
  Three of the four LOSO folds do not reach < 1/min even at q = 1.0 (the train maximum), so the loosest value was kept.
- **Result after calibration: target NOT met on held-out data.** Time-block test 4.7 alerts/min (10 events in
  2.1 min); LOSO 5.9/min (62 in 10.6 min). The learned layer contributes most on test (1.4/min without it). Two
  minutes of validation is too little to calibrate a rate this low (each rate rests on 0-5 events), and the
  front/back test block alone has 7 events. No further tuning was done against test.
- **Protocol layer:** 0 false positives on time-block val and test. Under LOSO it raises 14 events (1.3/min):
  `ARRIVAL` (offsets up to 77 when the fold never saw > 74), `SLOT_RANGE` (slot 0x35 unseen in the fold), `CADENCE`.
  These [min, max] ± margin limits do not fully generalise to an unseen scenario. Widening them is a follow-up
  decision for the owner, not something tuned here.
- **Thresholds loosened by calibration cost sensitivity that cannot be measured yet.** For example `rr_resid_hard`
  went from 0.61 to 1.06 and `accel_hard` from 16.8 to 27.2. Without attacked data, how much detection this costs
  is unknown.
- **Learned layer:** the AE is used online with a numpy forward pass (torch is only used for training). The
  isolation forest is scored offline in batch, because one sklearn call per cycle would break the latency budget.
  The two cannot be ranked without attacked data; both are reported at the same validation-calibrated rule.
  Thresholds are calibrated separately for static and moving windows, because moving windows reconstruct far
  worse (median error 0.89 vs 0.012).
- **Replay fingerprint:** two families, translation-invariant (dx, dy, vx, vy) per CLAUDE.md and rotation-invariant
  (d_range, |d|, v_r, speed). Windows need >= 4 distinct symbols, because real tracks hold values about 57% of the
  time. On clean data it fires on 2-13 object-cycles per evaluation. Its value against replays is unmeasured.
- **Latency:** p99 2.6 ms on the chaotic test segment (pytest) and 3.2 ms pooled in parallel workers. The budget is 10 ms.
- **Viewer technology:** matplotlib only (an interactive window with `plt.pause` pacing at about real time, or
  headless Agg export to PNG, plus GIF via PillowWriter). It adds no new dependency; a web UI is out of scope.
- **Script order:** `learn_baseline.py [--loso]` -> `train.py [--loso]` -> `calibrate.py [--loso]` ->
  `run_clean_eval.py`. Re-running `learn_baseline.py` rewrites `configs/baseline.json` and drops the AE and calibration
  entries, so the later steps must be re-run after it.

## 2026-10-04 — isolated Phases 3–6 completion

- **Isolation and authorization:** fetched `origin` without switching/pulling the original checkout; latest accepted
  main remained `7cdfa584edd20c8653991a8fbe8d6023c7b55493`. Created sibling worktree
  `D:\Hacksprint\phantom-guard-phases-3-6` on `codex/phases-3-6`, with its own Python 3.11 environment.
  The user's latest instruction explicitly authorizes pushing this branch; main is never merged or modified.
- **Raw recordings recovered locally:** no working-copy CSVs were found. All four are present as blobs in initial
  commit `24a987f`; copied their exact bytes into this worktree's ignored `data/raw` and set the Windows read-only
  attribute. No original-checkout or raw recording was edited. Models, caches and generated outputs are local.
- **Baseline before edits:** `python -m pytest -q` gave 34 passed, 1 skipped in 14.19 seconds. The skipped latency
  check needed absent trained artifacts; imports resolved to this worktree. Phase 2 remains levels/pools only.
- **Cadence contradiction fixed:** baseline collection/documentation skipped the first two header gaps, while
  runtime checked the second gap. Runtime now skips exactly gaps 1 and 2, checking gap 3 onward.
- **Malformed headers are boundaries:** every configured header CAN ID closes the preceding cycle, including
  malformed payloads. Its identity/timestamp opens a new unparsed-header cycle; the missing counter interrupts
  counter continuity until valid headers resume. Changing a future header payload cannot change the preceding
  cycle verdict. Header length remains a minimum because recorded header DLC is unknown.
- **ROI contradiction resolved explicitly:** plan text said both “protocol checks every frame” and “out-of-ROI,
  never flagged.” Protocol checks every frame and can flag out-of-ROI objects. Physics/replay/learned checks apply
  only within ROI; all contributing rolling samples must be in ROI. Out-of-ROI history no longer contaminates
  windows or physics baseline envelopes. Entering ROI is not a new linked-track birth. Viewer colors reflect
  actual protocol verdicts outside ROI. Green means “not flagged,” not proof of authenticity.
- **Track/window policy:** physics can use actual elapsed time across the configured one-cycle bridged gap.
  Learned/replay fingerprints require consecutive observations and reject gap-crossing windows. Feature order
  is `[dx, dy, vx, vy, radial_velocity, rcs, range]`; identities/timestamps are used only for linking/eligibility.
  Offline collection uses the same baseline reassignment threshold as online tracking. Fusion advances every
  scan, treats missing observations as unflagged votes, retires ended tracks, and casts one vote per linked
  track per scan even when duplicate verdicts exist.
- **Replay evidence:** expiry occurs before lookup. Matches distinguish training library, earlier stream and
  concurrent tracks. Complex nonoverlapping repeats on the same linked track can be earlier-stream evidence;
  overlapping windows, static clutter and simple constant motion are excluded by eligibility/complexity gates.
  Genuine complex repeated trajectories can still collide: fingerprints are suspicion, not authenticity proof.
- **Learned availability/provenance:** missing models/calibration are explicit layer/score outcomes. Reports and
  demos require matching schema, features, config, split identities, baseline signature, artifact IDs and source
  hashes. Empty training/class-calibration subsets fail clearly. No fallback `rr_scale=1` is invented when moving
  training evidence is insufficient. Recording bytes are hashed for provenance, never fed into learned features.
- **Selection/calibration:** AE remains the fixed online NumPy choice; IF is an offline comparator, not selected
  by test AUROC. Both use identical eligible windows/train normalization and the same selected validation
  quantile with separate static/moving thresholds. Preparation was run once in baseline -> train -> calibrate
  order for time-block and all LOSO folds. Fresh time-block validation selected q0.9999; three LOSO folds fail
  <1 alert/min even at q1.0. Historical test-inspection disclosure above is retained; no new test tuning occurred.
- **Evaluation identities and denominators:** compact records now preserve every emitted frame, timestamps,
  scores, verdicts and layer availability. Labels join only final emitted indices. Forged headers/unknown IDs
  can detect a cycle without identifying an object; malformed object attempts count in the overall object
  denominator with unknown physical class. Scene detection counts any alert during observed attack cycles,
  including incidental clean alarms; direct object identification is separate. Undetected and EOF-censored
  observed IDs stay in denominators; schedule-only instances require a future lifecycle sidecar.
- **Protocol levels preserved:** A2 is not required to pass range order/burst contiguity; those remain A3+.
  Generated run rows expose those reasons separately. T4 overwrite remains level dependent. Phase 2 code is
  untouched; optional provider integration lives in eval/demo. Config/baseline passed to providers are copies.
- **Replay provenance scopes:** time-block unseen means unseen portions of another seen recording; LOSO unseen
  means the wholly held-out recording. They are distinguished in run rows. Unseen replay material is never
  loaded into defender training libraries. Earlier-stream sources must be past-only within the attacker.
- **Latency:** CPU processing now includes frame grouping/decoding plus detector computation; source I/O and
  capture waiting are excluded. Detector CPU, assembly CPU and timestamp-based cycle-assembly delay are separate.
  IF batch scoring is excluded from online latency because it is an offline comparison. All p99 measurements
  report the configured 10 ms budget, observed environment and sample count; no latency claim is fabricated.
- **Reporting/viewer:** generated summary/CSVs include blocked/unsupported cells, per-run seeds, configurations,
  splits, artifact hashes/calibration and excluded data. Missing Phase 2 is a nonzero blocked outcome. Viewer
  retains clean/interactive/headless PNG/GIF/velocity/reason/log modes, distinguishes filenames, logs malformed
  frames by index and avoids duplicate entries when animation redraws a cycle. Labels never set colors.
- **Executed final validation:** full suite 109 passed, 0 skipped (20.67 s); generated JUnit evidence is
  `docs/results/validation.xml`. Baseline/train/calibrate with `--loso` and clean evaluation all completed.
  The attack evaluator completed eight independent clean segments and generated 2,664 blocked Phase 2 run cells
  plus 1,296 unsupported cells, returning a blocked nonzero outcome. No real attack metrics or attacked demo
  were fabricated. Final generated clean rates are 3.314/min time-block and 5.586/min LOSO; both miss the target.
  Per-segment total processing p99 is 1.486–1.565 ms, with capture assembly p99 33.5–33.6 ms reported separately.
- **Real viewer verification:** front/back first-alert and chaotic midpoint PNGs/GIFs/alert CSVs were exported
  successfully. Pillow verified both GIFs at 840×378 with 75 frames (150-cycle clip, stride 2). A cropped title
  was corrected and the exports regenerated. The exact `--attack T1 --level A2 --export` command fails clearly
  on missing Phase 2. Raw file blob hashes still match initial Git data exactly and all four remain read-only.
- **Final review:** detector/evaluation/viewer commits are focused; `attack/` has no diff from base; the original
  checkout remains clean on the original main SHA. Generated model details report actual AE dimensions/training
  seconds and IF settings (200 trees, max_samples 256); optional epoch-completion metadata was not present in
  these already-trained artifacts and stays explicitly null rather than being invented. Future train runs record it.
## 2026-10-04

### Phase 2: synthetic fabricated-frame generator (the "attacker" / red team)
Built as a **test-fixture generator**, not a live adversary: it runs only on the recorded CSVs and
produces an alternative input stream of fabricated radar frames so the detector can be measured. For
the showcase the stream can simply be switched in front of the judges (`replay_demo.py --attack`).
No bus, no live capture, no hardware (CLAUDE.md scope).

- **Design:** `attack/scenarios.py` (T1-T4 trajectory generators, A0-A4 realism), `attack/injector.py`
  (`MixedSource` wraps a `ReplaySource`, inserts fabricated 0x60B frames, rewrites the 0x60A count for
  A2+, and writes a side labels file `frame_index,is_attack,attack_id,attack_type,level`). Labels are a
  side output only; the detector sees plain `Frame`s and never the labels (hard rule 2). Every fabricated
  value is sampled from real-data pools / `baseline.json` (hard rule 3), all frames built via
  `encode_object` so they take the real decode path.
- **A2 vs A3 boundary (judgement call, matches the earlier decision):** CLAUDE.md's A2 is "header count,
  counter, slot uniqueness, cadence valid". The sensor *also* transmits objects back-to-back in ascending
  range, so the detector additionally checks `BURST_GAP` and `RANGE_ORDER`; those belong to A3 (the attacker
  must replicate transmit order to pass them). A2 therefore fails `BURST_GAP`/`RANGE_ORDER`/`COUNT_RANGE`
  by design - the detector is deliberately stronger than CLAUDE.md's A2 threat model, which the matrix shows.
- **New protocol check `COUNT_RANGE`:** header count outside the learned [min,max] objects-per-cycle,
  widened by `protocol.count_margin` (3). 0 false positives on clean time-block val/test and, with the
  margin, 0 on clean LOSO too. Without the margin it fired 52 times on clean LOSO: a held-out file's busy
  cycles (up to 32 objects) exceed the other three files' maximum (29), so the raw [min,max] does not
  generalise across scenarios. The margin covers that cross-file spread; a flood adds 10-30 objects and
  still clears it. T2 floods are caught at every level regardless (co-location among the flood objects and
  kinematics), so the margin costs no flood detection.
- **Generation artefacts removed (hard rule 3):** (a) a fabricated track must keep a *stable* slot across
  its life, else the injector reshuffles its slot on collisions and fakes a per-cycle jump that trips
  `JUMP`; fixed by reserving one free slot per fabricated object. (b) A4 must keep a *stable* RCS (real
  moving tracks hold RCS to ~0.3 dB std); an earlier version re-sampled RCS each cycle and tripped
  `RCS_STD`. Both fixes lowered detection of A3/A4, which is the honest number.
- **T4 (owner decision, level-dependent):** A0/A1 add the drifting object alongside the real one (same slot
  -> `DUP_SLOT`); A2+ overwrite the real frame in place (the injector drops the real frame carrying that
  slot). Not deletion of a track - the object remains, with a growing position offset.

### Phase 2 results (held-out test segments, 3 seeds, all four files; numbers generated in docs/results/summary.md)
- Instance detection (a persistent alert ever fires on the fabricated track): A0-A2 ~100% across T1-T4;
  A3 drops (roughly 70-90% on T1/T3/T4); A4 is the hard case (T1 ~60%, T3 ~40%, T4 ~70%); T2 floods 100%
  at every level. The exact matrix is in summary.md / attack_matrix.csv and is regenerated by the script.
- **Layer story (ablation):** protocol carries A0-A2 (timing/order/count); removing the kinematic layer
  collapses A3/A4 detection (54%->3%, 39%->1% of fabricated object-cycles), so co-location, the
  range-conditional RCS band, and range-rate carry the realistic levels. The replay fingerprint carries T3
  (34% of its object-cycles at A3) but only matches recordings it has seen - a T3 copy built from data the
  detector never trained on (A4) legitimately evades it. The autoencoder adds little at A4 (AUROC 0.51).
- **Hardest cases (as CLAUDE.md predicts):** static A4 phantoms at a plausible position with a
  range-appropriate RCS, and replays of unseen recordings. These are reported, not hidden.
- **Deck-claim check** is generated at the end of `summary.md`. "No labelled attack data needed" holds:
  no threshold uses the generator or its labels; they are measurement only.

### Enhancement #1: cutting clean false alarms (operating point)
- **Evidence gathered on validation only.** At the old operating point (3 of 5, autoencoder alerting alone), clean
  validation had a single alert episode (`REPLAY`) in 2.1 min. Fabricated frames mixed into the validation
  segments (A2-A4) showed no instance-detection change between the fusion variants tried. That was used as a
  check only: hard rule 2 forbids attack labels from choosing a threshold.
- **Change 1 (design rule, not tuned): the autoencoder is corroborating evidence.** `fusion.learned_alone: false`:
  an object-cycle whose only reason is `LEARNED` does not count toward an alert. The code and score stay on the
  verdict for display. Rationale: its AUROC on fabricated vs real windows is about 0.6 (A3) and about 0.5 (A4,
  chance), so on its own it is the least specific layer. The deck-claim row for it now reads "weak", computed.
- **Change 2 (calibrated on clean validation): persistence M of N.** `calibrate.py` stage 2 picks from
  `fusion.mn_candidates` [[3,5],[4,6],[5,8]] the candidate with the fewest *clean validation* alert events, with
  ties going to the smallest N (fastest detection). It is recorded as `fusion_mn` in the baseline JSON with its
  table. Time-block chose 4/6 (1 -> 0 validation events). The LOSO folds chose 4/6 or 5/8.
- **Test had been seen before this change** (it was the reason the autoencoder was suspected: it was the largest
  clean-alert contributor on test). The change is justified by the validation evidence and the design rule above,
  but the test numbers below are not blind.
- **Results, like-for-like** (same detector output, fusion re-applied offline; `clean_eval_operating_point.csv`,
  summary.md "Operating point trade-off"):
  - clean false alerts: time-block test 10 -> 3 events (4.73 -> 1.42/min); LOSO 62 -> 42 (5.87 -> 3.98/min).
  - detection cost: 2-6 points of instance detection at A2-A4 (for example T1/A4 60% -> 54%, T3/A4 38% -> 33%),
    and 0 at A0-A1 and on T2.
  - **The < 1 alert/min target is still not met** (1.42/min on test, from 3 kinematic-layer events in 2.1 min).
    Validation now has zero events, so nothing further can be learned without tuning on test, and I stopped there.
- **Bug found and fixed: the attack evaluation was not reproducible.** The per-job generator seed used `hash(file)`;
  Python randomises string hashes per process, so two runs with identical code gave different matrices (T1/A3 66%
  vs 72%). It now uses `zlib.crc32` via `job_seed()`, with a regression test across two PYTHONHASHSEED values.
  A first before/after comparison that mixed this randomness into the trade-off (it suggested a 4-13 point cost)
  was discarded in favour of the like-for-like comparison above.

## 2026-10-04 — complete-workflow integration and real-data audit

- **Accepted versus contributor work:** merged `origin/main` at `1f5d2ee` into the
  existing `codex/phases-3-6` history. Since the original base `7cdfa58`, main accepted
  `38f9693` (Phase 2 planners/injection/evaluation/viewer), `62a9db8` (validation
  M/N, corroborating AE, deterministic legacy seeds), and PR #3's merge. Phase 0
  frames and Phase 1 ingestion/baseline were already accepted. No contributor branch
  or original-checkout file was edited. The expanded assignment authorizes Phase 2
  contract corrections in this feature branch.
- **Real supplied inputs:** inventoried all four `dataset/*.csv` files, their 22
  decoder-v2 columns, SHA256, rows and cycles. Their existing read-only `data/raw`
  copies match byte for byte. All `source_file` values are `live`, so recording
  identities come from filenames. Models, processed data, checkpoints, environment,
  and source datasets remain local and ignored. Dataset inventory accompanies the
  generated reports.
- **Compatible preparation:** learned median cadence as a baseline value rather
  than baking 332 ticks into scheduling or display. This required regeneration in
  baseline -> train -> calibrate order, with `--loso` at every stage. All five model
  bundles have strict source/split/config/feature/baseline provenance. Model selection
  remains fixed AE for NumPy deployment; IF is an offline comparator with the same
  normalization and scoring windows (fixed-seed training subset capped at 50,000).
- **Contract extension:** reused the accepted scenario planners and `MixedSource`.
  `AttackedSource` now emits ordinary frames and a complete final-index sidecar,
  including rewritten count headers. Generic sources are buffered for offline
  scheduling and retain malformed/unknown frames. Legacy sparse object labels remain
  available through `MixedSource`. Lifecycle metadata records requested/planned/
  emitted instances and EOF truncation; missing plans are not invented attacks.
- **Attacker corrections:** T4 A0/A1 retain the genuine slot and append alongside it;
  A2+ overwrite. Stable-slot exhaustion is explicit unsupported behavior, never a
  substitute slot outside the observed distribution. Separate instances honor the
  configured gap after their actual end, preventing overlapping T4 duplicate slots.
  Minimum source lengths/counts are enforced. Flood copies may be shorter than T3's
  20-cycle replay minimum. A1/A2 unrelated velocities come from real training pools.
- **Naive-byte wording:** the original phase plan says A0 has random bytes, while
  the frame contract requires forged objects to use `encode_object`. The accepted
  attacker encodes random/unrelated decoded values into structurally valid object
  payloads; it does not fuzz reserved bits or length. This implementation retains
  that behavior. Malformed-byte/header/ID cases are separate regression fixtures,
  not claimed as additional real attack-performance cells.
- **Timing and level boundary:** every level serializes CAN frames using learned
  spacing; inserted frames can delay subsequent authentic traffic and headers. A3+
  also sorts by range and closes burst gaps. A2 repairs counts/counters/uniqueness
  and retains nominal cadence except physical bus overrun; it is not required to
  pass range order, burst contiguity, or scene count-envelope checks. This bus-overrun
  limitation is disclosed rather than teleporting authentic frames backwards in time.
- **Replay scopes:** earlier-stream material is restricted to the victim prefix
  before onset. Training and unseen material are separate explicit choices at A3/A4,
  independent of capability. Unseen segments never enter defender libraries. A
  translated copy uses a nonzero quantized offset sampled from training positions,
  with the whole trajectory inside the scene; failed material requests stay explicit.
- **Header DLC contradiction:** recorded header DLC remains unknown and rebuilt
  headers default to five bytes. Classic CAN still imposes an eight-byte maximum.
  Overlong headers are hard `HEADER_LEN` violations and causal cycle boundaries;
  they cannot change the preceding closed-cycle verdict.
- **Fusion and performance investigation:** both evaluators now apply the same
  calibrated per-fold M/N as the online detector. Gap aging, reassignment, duplicate
  votes, and offline/online equivalence have regressions. Fresh clean test results
  are 1.42/min time-block (3 events) and 3.41/min LOSO (36 events), compared with the
  old 3/5 AE-alone policy's 3.31/5.59 on the same outputs. Neither meets <1/min.
  The remaining time-block events are physical plausibility tails. LOSO protocol
  alone contributes 11 events (1.04/min); `RCS_BAND` is the largest physical reason
  count (171 object-cycles). These are short-sample/generalization limitations, not
  justification to enlarge thresholds on test. Historical test inspection remains
  disclosed; this run makes no new test-based operating-point choice.
- **Learned policy limitation:** accepted `learned_alone: false` suppresses a lone
  learned vote. Because a cycle already votes when another soft layer flags it,
  corroborating AE adds no incremental boolean alerts under this OR/M-of-N rule.
  Reports state this and retain raw-score AE/IF AUROC, rather than claiming an
  independent learned detection gain. The accepted policy is preserved.
- **Resource failure and correction:** an intermediate 12-worker evaluation failed
  with MemoryError and OpenBLAS workspace errors (24 BLAS threads per process).
  Evaluation now defaults each BLAS worker to one thread and reports explicit
  environment overrides. The full matrix began with eight workers and resumed
  unchanged from validated checkpoints with sixteen once memory use was stable;
  each run records its measured worker count. Clean latency uses one worker.
  Successful/unsupported jobs are content-addressed and resumable; code,
  artifact, data or config changes invalidate them. Blocked checks are retried.
  Intermediate matrices affected by overlapping T4 or timing defects were discarded.
- **Publication:** authenticated GitHub account verified as `Aurora-source`; the
  verified fork is `https://github.com/Aurora-source/phantom-guard`. Preserve `origin`
  as the original upstream and add `fork` for publication. Push only the feature
  branch and create/update a cross-fork PR to upstream main; no force push or direct
  main update is part of this assignment's final publication path.
- **Final executed evidence:** 139 tests passed with zero skips (49.82 s); compileall,
  pip check, local import and checksum checks passed. Real Tk clean and attacked
  event loops completed with timed automated closure. Four real-data PNG/GIF pairs
  and logs were regenerated, each GIF 75 frames. The full matrix completed 2,411
  observed attack runs plus 198 no-material and 55 capacity exclusions, with no
  integration blocker. Unsupported T3/A0–A2 requests remain explicit. Worst completed
  attacked p99 is 5.76 ms (none over 10 ms); single-worker clean p99 is 1.52 ms.
  Assembly p99 is separately 33.5–33.6 ms. Eighteen observed runs fully evade scene
  detection. Zero final T4/A2–A4 duplicate-slot violations were found across the
  completed streams. Raw per-run/instance results and actual AUROCs are archived;
  no model choice follows those AUROCs. The under-1 clean target still fails.

### Portability fixes for Linux (Arch) runs
- **`eval/report.py` crashed without `rtk`.** `provenance()` shelled out to `rtk proxy git ...`. `rtk` is a tool on
  the machine that produced PR #4, and a missing executable raises `FileNotFoundError` even with `check=False`, so
  `run_attack_eval.py` failed while writing the manifest on a stock install. It now calls `git` directly and records
  `None` if git is unavailable. Covered by `tests/test_portability.py`.
- **Manifest paths were machine-specific.** Recording and artifact paths were absolute (`D:\Hacksprint\...` in the
  committed manifest), and implementation-hash keys used the OS separator (`src\phantomguard\...` on Windows). Both
  feed the checkpoint cache id, so the same checkout got a different identity per OS and directory. Paths are now
  repo-relative POSIX (`portable_path()`), or absolute POSIX when outside the checkout. The committed
  `attack_eval_manifest.json` is generated evidence from that run; it was left untouched, not hand-edited
  (hard rule 8), and will be replaced on the next full evaluation.
- **README rewritten for Arch Linux:** pacman packages (`tk` for the interactive viewer), a mandatory venv (PEP 668),
  the CPU-only PyTorch wheel index, a `uv` fallback when Arch's Python is newer than PyTorch's wheels, and
  case-sensitive recording names. Verified here: the full test suite, the data check one-liner, and
  `run_attack_eval.py --preflight` end to end.

### Latest accepted baseline integration (2026-10-05)

- Accepted upstream `f93f283` changes baseline metadata and generated reports,
  without changing detector, attacker, features or split policy. Its referenced
  trained weights are not supplied in Git and do not match any available local
  bundle. The commit is merged with both histories preserved; source datasets,
  the original checkout and the prior implementation worktree are unchanged.
- Preserve the prior compatible artifacts and upstream baseline in ignored local
  backups. Regenerate offline in the documented baseline -> train -> calibrate
  order using the unchanged fixed training/validation policy. Re-evaluate this
  coherent artifact set separately; the earlier full matrix is historical evidence
  until the new sweep finishes. No held-out result selects weights or thresholds.
- Canonicalize tracked generated CSV line endings to LF for reproducible restoration
  on Windows and Linux. CSV field values are unchanged by this normalization.
  Selected fresh reports live under `docs/results/portable`; root reports retain
  the accepted upstream evidence and its original provenance.
- The full regenerated-artifact sweep completed on `e530d8f` with no integration
  blocker. Fresh clean rates remain 1.42/min and 3.41/min; no policy changed in
  response. Final metrics, exclusions and evading runs are generated evidence.
- Remaining CRLF source files in the development worktree differed from tracked
  LF sources in normal clones. Every difference was verified to be newline-only.
  Preserve the benchmark's original hashes and record canonical LF hashes in the
  portability evidence; normalize working source files without altering code.
- A viewer lookup preferred an existing caller-directory filename over configured
  raw data. Recording names now resolve under raw data, explicit relative paths
  under the workspace, and absolute paths remain explicit. Two fixture regressions
  pass. Detector/attacker/feature/calibration/metric behavior is unchanged.

## 2026-10-05 — detector accuracy, evidence and artifacts (Agent 2, `codex/detector-accuracy`)

Anchor `3813b51`. Detector changes are a **profile** stored in the baseline (`detector_contract`):
a legacy baseline (no contract) runs the original rules bit-identically (clean evaluation outputs
re-verified byte-identical after every detector change); a v2 baseline selects the reworked rules.
Code that sees an unknown profile/schema refuses to run it.

### Incident: a test run overwrote an external dataset copy (restored)
- Running `pytest` in a shell that exported `PHANTOMGUARD_DATA_DIR` (pointing at the owner's external
  dataset directory used for analysis) let `test_missing_and_case_sensitive_data_are_actionable`
  write a lowercase `emptyroom.csv` fixture into that directory; Windows is case-insensitive, so the
  10-byte fixture replaced `emptyRoom.csv`. It was restored from the verified bundle copy and its
  SHA-256 re-checked (`13fb666d…2ebc`, docs/data.md). No other file changed (all four hashes verified).
- Fix: `tests/conftest.py` removes every `PHANTOMGUARD_*` variable before configuration loads, so a
  test session can no longer inherit path overrides. Tests needing overrides use monkeypatch.

### Measurement before changes
- Failure ledger of clean persistent-alert episodes (legacy, held-out parts): the largest single cause
  was `RCS_BAND` (narrow per-range bands fitted to the training clutter), then `ARRIVAL` (late offsets
  in busy cycles: offsets grow ~2.4 ticks per position in the burst), `SPEED`/`SLOT_RANGE`/`CADENCE`
  empirical tails, and `REPLAY` chance collisions between real tracks.
- A rule-level attribution run of the legacy detector on A3/A4 attacks used the **test** part (seed 11);
  it was diagnostic of the existing system only. Every v2 design comparison since uses the
  **validation** part; the test part is reserved for the frozen final matrix.
- Attack identification at A3/A4 leaned on `COLOC` and `RCS_BAND` (and `REPLAY` for A4/T3). Forged
  objects that land near real clutter are a property of the attacker's sampling, so rule ablations
  (`--ablate-codes`) report the dependence instead of changing the attacker.

### Profile v2 rules (all thresholds from clean data; none from attacks)
- Structural and exact-regularity rules stay hard (format, counter, count, duplicate slot, status,
  fixed bits, RCS grid, range order, burst contiguity). Empirical tails become soft evidence with an
  exceedance threshold calibrated per rule.
- Calibration = episodes, not marginal percentiles: inner leave-one-recording-out CV over the
  train+validation portions; each rule gets the most sensitive grid value at which it alone adds no
  clean alert episode over the system without tunable rules. Recordings are consecutive segments of
  one session; no IID or distribution-free guarantee is claimed.
- `RCS_BAND`/`RCS_RANGE` replaced by a range-conditional RCS envelope (kernel-weighted quantiles,
  per-track weights, shrinkage by effective independent tracks); `ARRIVAL` replaced by a one-sided
  offset-vs-burst-position line (`ARRIVAL_POS`).
- Drift evidence (`DRIFT`): position change over 8/16/32 cycles vs integrated reported velocity in the
  line-of-sight frame, with the gain B **fitted** per horizon/regime (not assumed 1) and MAD scales
  floored at the 0.2 position quantum. Static-regime windows are heavy tailed (creeping far-field
  clutter: z up to ~25 vs ~8 when moving), so they are a separate rule (`DRIFT_STATIC`) with its own
  threshold instead of inflating the moving one.
- `REPLAY` scores the run of consecutively matched windows. Clean chance collisions produced at most
  5 consecutive matches; a replayed segment of >=20 cycles yields >=9. The run threshold is calibrated
  like the other rules (legacy/uncalibrated: every hit flags, as before).
- Failed experiment: replay rarity as fingerprint self-information under a unigram symbol model of the
  library. Clean chance collisions sat at the 43rd-84th percentile of real moving windows, i.e. they
  look like ordinary motion (real motion is strongly autocorrelated, which a unigram model ignores).
  Rejected; output kept under `runs/experiments/replay_unigram_rarity.txt` (ignored).

### Association
- A velocity-predictive association gate was measured before adoption: it changes 4 of ~433k slot
  links on the four recordings and never cuts a link the plain 1.0 gate keeps. It is implemented as a
  single switch `tracks.predictive_gate` (default off; training, attacker pools and detector read the
  same switch). Every verdict now reports its association status (`continued`, `gap_bridged`,
  `ambiguous_continued`/`ambiguous_reset` within 0.7-1.3x of the gate, `born_by_jump` with the
  predecessor track, `duplicate_slot`), so near-gate decisions are visible in evidence.

### Data intake
- `phantomguard.commands.intake` hashes new session folders read-only, assigns roles and keeps one
  group reserved (hash and header only) until the policy is frozen. The four existing recordings are
  `development`: all were inspected. 30-60 min of clean recording per new session is recommended
  (docs/data-intake.md); no recording is synthesised.

### Development comparisons (validation part, seed 11; generated under runs/, summarised in the final report)
- Legacy vs v2 on identical attacker streams: v2 had 0 clean episodes in 8.4 validation minutes (legacy 5);
  no instance lost at A0-A2; collateral alert frames on real objects in T2 A3/A4 fell from ~3,200-4,000 to
  ~160-220; T1 A3/A4 identification fell 11/10 instances. 39 of the 75 instances v2 lost had been
  caught by legacy `RCS_BAND`, the rule behind most legacy clean false alarms, so this is an operating-point
  trade, not a lost capability. A relaxed v2 (each rule may add 1 clean CV episode; clean data only) is
  evaluated on the same streams to compare at a similar clean budget.
- COLOC dependence is an attacker artefact: static phantoms are placed at recorded positions of real
  in-ROI objects (`sample_pos_roi`), and static clutter persists, so many phantoms sit on a live
  reflector. Without COLOC, v2 T1 A3 identification drops from 108 to 12 instances (legacy 119 to 65). The
  attacker is unchanged (shared benchmark); `all-minus-COLOC` rows are published as the estimate for an
  attacker that avoids occupied positions.
- T4 (constant-direction offset of 0.02-0.08 per cycle on a real moving track, real velocity kept):
  at zero clean exceedance the windowed DRIFT z separates 39/135 validation instances, a vector EWMA of
  the per-step residual (lambda 1/32) another, partly different 39; together 51 (37.8%). The rest are not
  separable from clean position/velocity disagreement with these statistics at this clean budget and are
  reported as indistinguishable. `DRIFT_EWMA` was added with lambda fixed at 1/32 (memory matching the
  longest drift horizon); lambda was examined on validation attacks, so only the untouched test part can
  confirm the gain. Its threshold is calibrated on clean data like every rule.
- Fitted drift gain B is 0.46-0.61 radially and 0.79-0.81 tangentially for moving tracks: positions and
  reported velocities do not share one metre/second scale, consistent with the unverified-units note.
- Learned layer (same clean budget, thresholds from clean validation windows): window AE with an
  independent 4-of-6 route adds 13 of 2,243 identified instances (T4 A3/A4 +4 each, T3 +2 each, T4 A2 +1),
  a fitted simple residual adds 4, a causal ridge predictor and every joint (monotone) route add 0.
  The AE threshold could only be set on the same ~8 validation minutes that measure its clean cost
  (inner-CV retraining of the AE per held recording was not done), so the route is **not adopted**:
  the learned layer stays corroborating-only and no ML detection claim is made. Re-evaluate on new sessions.
