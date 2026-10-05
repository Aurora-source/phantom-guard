from phantomguard.eval.coverage import COVERAGE_SCHEMA, coverage_matrix


def _run(status, layers="all", **extra):
    base = {"attack_type": "T4", "level": "A3", "motion_case": "moving", "replay_provenance": "not_applicable",
            "replay_variant": "not_applicable", "status": status, "layers": layers}
    return {**base, **extra}


def test_coverage_separates_requested_supported_eligible_and_counts_instances_once():
    runs = [
        _run("ok", requested_instances=6, scheduled_instances=4, attack_instances=4, planner_retried_instances=1,
             planning_seconds=0.5, planner_version=2),
        _run("ok", layers="protocol", requested_instances=6, attack_instances=4),      # other variant: ignored
        _run("no_eligible_attack", requested_instances=6, scheduled_instances=0, attack_instances=0,
             planning_seconds="1.5", planner_version=2),
        _run("blocked_integration", layers=None),
        {**_run("unsupported", layers=None, reason="T4 needs a real moving track"), "level": "A0"},
    ]
    rows = {(r["level"]): r for r in coverage_matrix(runs)}
    a3 = rows["A3"]
    assert a3["schema"] == COVERAGE_SCHEMA
    assert (a3["requested_runs"], a3["supported_runs"], a3["executed_runs"], a3["eligible_runs"], a3["blocked_runs"]) \
        == (3, 3, 2, 1, 1)
    assert (a3["requested_instances"], a3["scheduled_instances"], a3["attack_instances"]) == (12, 4, 4)
    assert a3["emitted_instance_fraction"] == 4 / 12 and a3["planning_seconds_total"] == 2.0
    assert a3["planner_versions"] == "2" and a3["planner_retried_instances"] == 1
    a0 = rows["A0"]
    assert a0["supported_runs"] == 0 and a0["unsupported_reason"] == "T4 needs a real moving track"
