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
