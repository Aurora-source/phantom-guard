# Detector accuracy hand-off (Agent 2)

Branch `codex/detector-accuracy` (fork `Aurora-source/phantom-guard`), anchor `3813b51`.
Policy frozen at `e81c6b1`; tested commit (frozen policy + bounded evaluation cache) `0870ec6`.
Detector, attacker, calibration and baselines are byte-identical between the two (`git diff e81c6b1 0870ec6`
touches only `commands/run_attack_eval.py`). Generated results: [docs/results/detector-accuracy](../results/detector-accuracy/README.md).

Prototype assumptions stay labelled and configurable: recorded sensor data with simulated CAN injection;
coordinate units, tick duration and the frame layout are provisional. One recording session only.

## What changed

| area | change | where |
|---|---|---|
| profile | detector behaviour selected by the baseline (`detector_contract`); legacy baselines run the original rules bit-identically; unknown profile/schema fails clearly | `detect/common.py` |
| rules | structural/exact-regularity rules hard; empirical tails soft with exceedance thresholds calibrated on out-of-recording clean episodes | `eval/calibration.py`, `commands/calibrate.py --profile v2` |
| RCS | range-conditional envelope + conditional std replace narrow per-range bands | `stats/v2.py`, `detect/kinematic.py` |
| timing | one-sided arrival-vs-burst-position line replaces the arrival window | `detect/protocol.py` |
| motion | DRIFT / DRIFT_STATIC (8/16/32-cycle windows, fitted gain B) and DRIFT_EWMA (lambda 1/32) | `detect/kinematic.py` |
| replay | REPLAY flags on a calibrated run of consecutive matches | `detect/replay_fp.py` |
| association | association status on every verdict; optional predictive gate (off) | `tracks.py` |
| evidence | additive per-reason evidence records, persistence records | `detect/evidence.py`, [interface-evidence](interface-evidence.md) |
| evaluation | lineage sidecar, paired clean control, exact localisation, lifecycle/coverage, rule ablations | `eval/localize.py`, `eval/coverage.py` |
| tooling | ledger, rule audit, latency benchmark, learned comparison, T4 study, equivalence, intake, prepared pools, report | `commands/*.py`, `attack/prepared.py` |

## Results (generated; copied from the report)

All numbers below are copied from [docs/results/detector-accuracy](../results/detector-accuracy/README.md)
(test part; frozen policy; seeds 11/22/33 x 3 runs, instance-paired on identical attacker streams).

**Clean held-out persistent alert episodes** (legacy -> v2): time-block 3 -> 2 in 2.11 min (1.42 -> 0.95/min);
LOSO 36 -> 14 in 10.56 min (3.41 -> 1.33/min; clustered bootstrap 95% [1.09, 6.35] -> [0.37, 2.50]).
The worst recording (chaotic, held out) fell from 30 to 9 episodes. **Target < 1/min: met narrowly on the
time-block split, not met in LOSO.** Residual v2 causes (ledger): ACCEL/RR_RESID/COLOC/RCS_ENV tails in the
busiest scene, DRIFT on the front/back walker, one REPLAY collision.

**Attacks, exact identification** (alert on a forged 0x60B frame of the instance), legacy -> v2:

| cell | legacy | v2 | note |
|---|---|---|---|
| A0-A2, all types | 100% (T4 A2 48.0%) | unchanged | no instance lost |
| T1 A3 | 67.4% | 58.6% | lost 110 / gained 36; legacy relied on RCS_BAND |
| T1 A4 | 77.1% | 76.7% | |
| T2 A3 / A4 | 99.7% / 98.8% | 99.2% / 99.1% | collateral alert frames on real objects 24,981 -> 1,302 and 27,622 -> 1,560 |
| T3 A3 = A4 | 80.1% | 78.0% | REPLAY now needs a run of matches |
| T4 A3 = A4 | 43.5% | 43.1% | drift below clean motion disagreement is indistinguishable |

Held-back seeds 101/202 show the same pattern (T1 A3 73.1% -> 65.6%, T3 80.2% -> 78.2%, T4 A3 46.9% -> 42.9%).
v2 is a large reduction in clean alarms and collateral alerts, bought with a few points of A3 recall;
it is **not** an improvement in recall at an equal clean budget. The relaxed calibration (validation only)
shows the trade-off curve. T1 A3/A4 identification depends mostly on COLOC, an attacker artefact
(phantoms placed on recorded positions of real objects); see the ablation rows.

**Latency** (detector per cycle, serial, BLAS 1 thread): Linux (pinned container, valid ns thread clock) CPU p99
0.96-1.11 ms clean, 1.93 ms worst attacked stream; Windows wall p99 1.97 ms (thread clock only advances in
15.6 ms ticks there, so CPU percentiles are not reported). Rare per-cycle maxima reach ~44 ms. Budget 10 ms p99: met.

**Equivalence**: Windows vs Linux (`python:3.11.17-slim-bookworm`, same artifacts) - identical decisions on 8 streams,
bit-identical scores.

## Restore and verify

```bash
git fetch fork codex/detector-accuracy && git checkout --detach 0870ec6
python -m phantomguard verify-bundle deployment-bundles/phantomguard-detector-0870ec6.zip
python -m phantomguard restore-bundle deployment-bundles/phantomguard-detector-0870ec6.zip
python -m phantomguard doctor --full
```

Offline rebuild of the same artifacts (never on the deployment server): `baseline --profile v2 --loso`
(trained models are unchanged and can be restored from the legacy bundle), then
`calibrate --profile v2 --loso --workers N`. The server must not train, recalibrate, regenerate the
replay library or substitute models.

## Failed or not-adopted experiments

- Replay rarity as unigram self-information: clean collisions look like ordinary motion (43rd-84th percentile).
- Predictive association gate: changes 4 of ~433k links; implemented behind a switch, left off.
- Learned routes: AE independent route +13/2,243 identified at equal clean budget, residual +4,
  ridge predictor and all joint routes 0. Not adopted (threshold could not be calibrated out-of-recording);
  no ML detection claim.
- EWMA alone vs windowed drift: equal at zero clean exceedance; they catch different instances, so both are kept.
- Relaxed calibration (allowance 1): more recall at roughly legacy's clean budget; not frozen because its
  CV clean estimate exceeded 1/min on the chaotic fold. Published as an alternative operating point.

## Fixtures and interfaces

- `docs/parallel/fixtures/evidence_cycle.json`, `paired_localization.json` (generated by `tools/make_evidence_fixtures.py`).
- New reason codes, fields and versioned schemas: [interface-evidence.md](interface-evidence.md) (draft 2).

## Not done / handed on

- Runtime adoption of prepared pool exports and display of `association`/new reason codes: Agent 1 / Agent 3.
- New independent recording sessions (30-60 min clean each, one reserved): owner (see docs/data-intake.md).
