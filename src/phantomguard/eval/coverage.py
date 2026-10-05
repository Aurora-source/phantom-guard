"""Versioned attack coverage matrix: what was asked for, what the attacker could do, what was measured.

A detection rate means little without its denominator. For each cell (type x level x motion case x
replay provenance/variant) this table separates:

* ``requested_runs``     every configured job of the cell;
* ``supported_runs``     jobs whose capability exists (``unsupported``: the attacker level/type does not
                         define it, e.g. T3 below A3; ``unsupported_provider``: the adapter declined);
* ``executed_runs``      supported jobs that ran end to end (blocked_* runs are counted separately);
* ``eligible_runs``      executed runs that produced at least one emitted attack instance;
* instance counts        requested -> scheduled -> emitted, with planner outcomes (no source material,
                         exhausted, retried), slot-capacity drops/partials, right-censoring at segment end;
* planning cost          total and p95 planning seconds per run (attacker-side cost, not detector latency).

Rows only aggregate the per-run columns written by run_attack_eval; one detector layer subset is used
(``all`` by default) so instance counts are not multiplied by the number of layer variants.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

COVERAGE_SCHEMA = "phantomguard.coverage/1"
CELL_KEYS = ("attack_type", "level", "motion_case", "replay_provenance", "replay_variant")
INSTANCE_COLUMNS = ("requested_instances", "scheduled_instances", "scheduled_unobserved_instances",
                    "unscheduled_no_material_instances", "planner_no_source_material_instances",
                    "planner_exhausted_instances", "planner_window_exhausted_instances", "planner_retried_instances",
                    "slot_capacity_dropped_instances", "slot_capacity_partial_instances", "attack_instances",
                    "right_censored_instances")


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def coverage_matrix(runs: list[dict], layers: str = "all") -> list[dict]:
    """Aggregate run rows into one coverage row per cell. Runs without a ``layers`` value (unsupported,
    blocked) carry no detector variant and are counted once."""
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("layers") not in (None, "", layers):
            continue
        if "attack_type" not in r:
            continue
        cells[tuple(r.get(k, "") for k in CELL_KEYS)].append(r)
    out = []
    for key, rows in sorted(cells.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        status = defaultdict(int)
        for r in rows:
            status[r.get("status", "")] += 1
        unsupported = status["unsupported"] + status["unsupported_provider"]
        blocked = sum(v for k, v in status.items() if k.startswith("blocked"))
        executed = status["ok"] + status["no_eligible_attack"]
        plan = [_num(r.get("planning_seconds")) for r in rows if r.get("planning_seconds") not in (None, "")]
        row = {"schema": COVERAGE_SCHEMA, **dict(zip(CELL_KEYS, key)), "detector_layers": layers,
               "requested_runs": len(rows), "supported_runs": len(rows) - unsupported, "unsupported_runs": unsupported,
               "blocked_runs": blocked, "not_executed_runs": status["not_executed_preflight"],
               "executed_runs": executed, "eligible_runs": status["ok"],
               "no_eligible_attack_runs": status["no_eligible_attack"],
               "unsupported_reason": next((r.get("reason") for r in rows if r.get("status") == "unsupported"), "")}
        for col in INSTANCE_COLUMNS:
            row[col] = int(sum(_num(r.get(col)) for r in rows))
        row["emitted_instance_fraction"] = (row["attack_instances"] / row["requested_instances"]
                                            if row["requested_instances"] else None)
        row["planning_seconds_total"] = round(float(sum(plan)), 4) if plan else None
        row["planning_seconds_p95"] = round(float(np.percentile(plan, 95)), 4) if plan else None
        planners = sorted({str(r.get("planner_version")) for r in rows if r.get("planner_version") not in (None, "")})
        row["planner_versions"] = ",".join(planners)
        out.append(row)
    return out
