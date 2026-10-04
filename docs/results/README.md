# Current validation evidence

## Portable prototype validation (2026-10-05)

The fresh full matrix is [portable/summary.md](portable/summary.md), with matching
clean/layer/ablation/instance/exclusion CSVs and provenance. It ran on `e530d8f`
after merging accepted main `f93f283` and regenerating matching artifacts in
baseline -> train -> calibrate order with the unchanged permitted splits.
[portable_validation.json](portable_validation.json) records actual tests,
normal-wheel checks on Windows and Ubuntu 26.04.1 WSL, real browser/API checks
and separate single-worker latency. A subsequent viewer path fix prevents a
caller-directory recording from overriding configured inputs; it does not alter
detector, attacker, features, thresholds or metric calculations.

Results remain 1.42 clean alerts/minute on time-block test and 3.41 on LOSO:
**the under-one target is not met**. There are 2,411 eligible attack runs, 198
no-material attempts, 55 slot-capacity exclusions and 1,296 unsupported requests.
Eighteen completed runs evade scene detection. Scene/instance detection and exact
forged-object identification remain separate. Original data/model hashes and
fixed training/validation policy are retained; no new held-out tuning occurred.
Worst completed attacked-run processing p99 is 8.55 ms with 24 workers; separate
idle single-worker clean p99 is 1.97 ms. Assembly p99 is 33.5–33.6 ms under the
unverified tick-duration assumption. The full sweep took about 33 minutes on
the recorded 32-logical-CPU workstation; use fewer workers on the home server.

Fresh browser screenshots are under `portable/browser`. The checks use Windows
Chromium against Windows, native WSL Ubuntu and Debian container APIs; they do
not claim a native Linux desktop browser or human usability review. Bundle
verification/restoration evidence is [bundle_validation.json](bundle_validation.json).
Historical reports below retain their original source provenance; they are not
the newly executed matrix. Historical source locations in those manifests are
provenance, not deployment configuration. Portable reports canonicalize baseline
and sidecar references; private sidecars are not included in the deployment ZIP.

## Accepted earlier workflow evidence

The accepted upstream programmatic evidence is [summary.md](summary.md),
[clean_eval.md](clean_eval.md), and `attack_eval_*.csv/json`. The CSVs retain every
requested seed, repetition, supported/unsupported outcome, observed instance,
miss, censored delay, equivalent-window AE/IF score availability, and latency.
Aggregate rates pool integer denominators; AUROC summaries are means of valid
per-run AUROCs, with run counts, rather than pooled-score AUROC.

[evidence_inventory.json](evidence_inventory.json) records hashes and sizes of the
archived generated files. [dataset_inventory.json](dataset_inventory.json) records
the unchanged supplied recordings. [workflow_validation.json](workflow_validation.json)
and [validation.xml](validation.xml) describe executed checks. The attack manifest
binds those results to its recorded source commit, implementation hashes, data,
config, split identities and local trained artifacts. Its dirty flag reflects
generated baseline/documentation changes; implementation hashes were checked
before archiving. Models and complete frame sidecars remain ignored local files.

Current demos are the four pairs in [demo_inventory.json](demo_inventory.json):
front/back clean and T1/A2 seed 11, and chaotic clean and T1/A3 seed 11. Each pair
has a detector alert CSV. [interactive_smoke.json](interactive_smoke.json) records
real Tk event-loop checks with automated closure, not human usability testing.

Legacy `attack_matrix.csv`, `attack_ttd.csv`, `attack_layers.csv`,
`attack_ablation.csv`, and demos without the `_test_` identity are historical
upstream evidence. They are preserved and are not the current matrix.

Reproduce using the virtual environment and commands in the repository README.
Preparation order is baseline, train, calibrate, clean evaluation, attack
evaluation. The full fixed-code sweep began with eight workers and resumed from
validated checkpoints with sixteen, all with one default BLAS thread. Per-run
worker counts are retained. Single-worker clean latency is measured separately.

The workflow implementation and integrations are complete. The clean target of
under one alert/minute is **not met**; no new test result tunes thresholds or
selects a model. See the phase plan and decisions for assumptions and limits.
