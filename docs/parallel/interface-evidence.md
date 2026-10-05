# Detector evidence / artifact interface (Agent 2) — additive, backward compatible

Status: **draft 2** (section 4 adds the v2 reason codes and fields; draft 1 content unchanged).
Branch `codex/detector-accuracy`, anchor `3813b51`. Everything below is *additive*: no existing field,
score, metric or file is repurposed. Consumers that ignore these fields keep working unchanged.

Scope reminder: evidence can localise a suspicious **frame, object, track or scene** when its scope
supports that. It cannot identify a human attacker, authenticate a sensor, or prove physical reality.
Green still means "not flagged", never "authentic". Nothing here is an attack probability.

## 1. Per-reason evidence records (detector output)

`ObjVerdict.evidence: list[dict]` (default `[]`) and `CycleResult.cycle_evidence: list[dict]`
(default `[]`). Plain JSON-compatible dicts, so `dataclasses.asdict(verdict)` — the call the runtime
already makes when exporting a cycle — carries them automatically. Only flagged/alerting objects
have evidence (typically 0–3 short records), so payload growth is small.

```jsonc
{
  "schema": 1,
  "reason": "RCS_BAND",          // reason code, or "PERSISTENCE" for the fusion record
  "class": "empirical_tail",     // structural | exact_regularity | empirical_tail | learned | replay |
                                 // motion_evidence | fusion
  "scope": "object",             // cycle | object | track
  "status": "trigger",           // trigger = this observation violates the bound
                                 // persistent = alert continues from earlier flagged cycles (M-of-N)
  "frames": [8370, 8371],        // final emitted frame indices that contribute (ints)
  "cycles": [327, 328],          // detector cycle-index interval of the history used, or null
  "observed": 22.0, "expected": null, "lo": 16.0, "hi": 21.0,   // numbers or null
  "normalized": 1.4,             // residual in units of the calibrated scale, or null. NOT a probability
  "support": {"n": 14129, "status": "supported"},   // clean samples behind the bound; or null
  "suspects": {"frames": [8371], "track_id": 12, "basis": "exact_frame"},
  "note": "free text, safe to display"
}
```

`suspects.basis` states how far attribution goes — **render accordingly, never colour more than the
basis supports**:

| basis | meaning |
|---|---|
| `exact_frame` | the listed frame(s) violate the bound themselves |
| `duplicate_slot_both` | both frames sharing the slot are candidates; neither is proven forged |
| `colocated_pair` | both objects of a too-close pair are candidates; the younger *track* is flagged, age proves nothing |
| `header_or_unknown_object` | count/cadence/counter/status/format problem; the responsible object is **unknown** |
| `scene` | scene-level statement only |

`status: "persistent"` records (`reason: "PERSISTENCE"`) list the earlier flagged frames/cycles that
produced the current M-of-N alert, so a UI can separate "current trigger" from "continuing alert".

### Reason-code catalogue and hardness

`reason → (layer, class)` is exported by `phantomguard.detect.evidence.REASON_CLASS` and
`detect.common.REASONS`. New reason codes may be added by later commits of this branch; a consumer must
treat an unknown code as a displayable string and look up its layer/class from the record, not from a
hard-coded list. A reason's `class` tells you whether it is a protocol/format violation or merely an
unusual-but-valid observation (`empirical_tail`).

## 2. Offline evaluator-only artifacts (never a detector input)

* **Attack label sidecar** (existing): `labels.csv` with `frame_index,is_attack,attack_id,attack_type,level`.
* **Lineage sidecar** (new): `<labels>.lineage.csv`, `frame_index,kind,source_cycle,source_obj,source_slot,attack_id`
  - `kind` ∈ `header | object | other` (recorded frame: `source_cycle`/`source_obj` locate it in the
    recording), `forged` (no recorded counterpart), `replacement` (forged frame that overwrote the recorded
    object at `source_cycle/source_obj`). `source_slot` is diagnostic only.
  - Injection shifts final frame indices; clean-control and attacked streams are matched **only** through this
    lineage, never by equal indices or numeric slots.
* **Lifecycle sidecar** (extended): `<labels>.instances.json` gains `planner_version`, `plan_log`
  (per scheduled index: `scheduled | no_source_material | planner_exhausted | window_exhausted`, attempts),
  `dropped_slot_capacity`, `planning_seconds`, `lineage_version`. Existing keys unchanged.
* **Paired localization** (offline measurement; `eval/localize.py`): added columns in `attack_eval_runs.csv`
  (`loc_*`) and `attack_eval_instances.csv` (`paired_status`, `exact_*`, `window_*`, `forged_reason_counts`).
  `paired_status` ∈ `exact | excess_unlocalized | baseline_coincident_only | none`.
  Fixture: `docs/parallel/fixtures/paired_localization.json`.
* Ground-truth overlay for presentation, if ever shown, must be a **separate artifact with its own
  access and label** ("evaluation ground truth, not detector output"). It must never colour the live
  detector view. A contract for that artifact will be published in the final hand-off once the paired
  evaluator is frozen.

## 3. Fixtures (generated by `tools/make_evidence_fixtures.py` from real runs; not hand-edited)

* `docs/parallel/fixtures/evidence_cycle.json` — one detector cycle with evidence-bearing verdicts.
* `docs/parallel/fixtures/paired_localization.json` — one attacked run's paired localization rows.

## 4. Draft 2 additions (additive; legacy baselines never emit them)

Profile v2 is selected by the baseline (`detector_contract.value.profile == "v2"`), never by a UI switch.

| code | layer | class | meaning |
|---|---|---|---|
| `ARRIVAL_POS` | protocol | empirical_tail | object arrived later than its position in the burst predicts (one-sided) |
| `RCS_ENV` | kinematic | empirical_tail | RCS outside the range-conditional clean envelope |
| `DRIFT` | kinematic | motion_evidence | position change over 8/16/32 cycles disagrees with integrated reported velocity (moving windows) |
| `DRIFT_STATIC` | kinematic | motion_evidence | the same for windows below the moving speed (heavy-tailed; high threshold) |

`REPLAY` keeps its code and class. In v2 it flags only when the run of consecutively matched fingerprint
windows exceeds a calibrated length; `scores.replay_run` (float) gives the run length on every hit, flagged
or not. `RCS_BAND`, `RCS_RANGE` and `ARRIVAL` are not evaluated in v2 (superseded by `RCS_ENV` and
`ARRIVAL_POS`); consumers must not assume a code appears.

Other additive fields:

* `ObjVerdict.score_status["association"]` (all profiles): `born | born_by_jump | duplicate_slot | continued |
  gap_bridged | ambiguous_continued | ambiguous_reset`. Diagnostic only: how the slot link was made, with
  `ambiguous_*` marking links within 0.7-1.3x of the reassignment gate. Never an attack indication on its own.
* `JUMP` evidence `support` carries `association` and `predecessor_track` (the track this one replaced).
* `CycleResult.detector_thread_cpu_ms` (float): thread CPU of the cycle. Coarse on Windows (~15.6 ms ticks);
  use `detector_cpu_ms` (wall) for per-cycle display.
* Calibrated per-rule thresholds live in the baseline under `rule_thresholds.value` (`null` = rule inactive),
  with the out-of-recording CV table, curves and episode detail under `rule_thresholds.cv` / `.table`.

## 5. Still to come (do not depend on it yet)

* Artifact schema 2 identifiers, bundle manifest additions, and the bounded prepared-data loading contract.
* `docs/parallel/detector-handoff.md`: tested commit, bundle path/checksum, restore commands, results.
