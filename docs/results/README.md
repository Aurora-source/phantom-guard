# Current validation evidence

The current programmatic evidence is [summary.md](summary.md),
[clean_eval.md](clean_eval.md), and `attack_eval_*.csv/json`. The CSVs retain every
requested seed, repetition, supported/unsupported outcome, observed instance,
miss, censored delay, equivalent-window AE/IF score availability, and latency.
Aggregate rates pool integer denominators; AUROC summaries are means of valid
per-run AUROCs, with run counts, rather than pooled-score AUROC.

[evidence_inventory.json](evidence_inventory.json) records hashes and sizes of the
archived generated files. [dataset_inventory.json](dataset_inventory.json) records
the unchanged supplied recordings. [workflow_validation.json](workflow_validation.json)
and [validation.xml](validation.xml) describe executed checks. The attack manifest
binds the results to source commit `6d77465`, exact implementation hashes, data,
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
