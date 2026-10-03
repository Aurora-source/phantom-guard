# Decisions and data surprises

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
