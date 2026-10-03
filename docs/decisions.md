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
