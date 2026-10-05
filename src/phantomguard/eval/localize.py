"""Exact localization and paired clean/attacked measurement (OFFLINE evaluator only).

Nothing here is a detector feature, live oracle, calibration input or source of viewer colours.
Labels and lineage are joined to detector output only after detection, by final emitted frame index.
A clean control stream is matched to an attacked stream through evaluator-only source lineage
(recorded cycle + recorded object index), because injection shifts final frame indices and the two
runs' detector track ids may diverge. Equal final indices or equal numeric slots are never used.

Definitions (per run and layer subset, after that subset's fusion):

* forged object frame      : an object/malformed frame labelled ``is_attack``.
* exact recall             : alerting forged object frames / forged object frames.
* exact precision          : alerting forged object frames / alerting object frames (all).
* excess precision         : alerting forged / (alerting forged + excess real alerts).
* false suspect            : a real (unlabelled) object frame that alerts in the attacked run while the
                             clean control did not alert on its source frame ("excess").
* baseline-coincident      : a real frame alerting in both runs; not credited to the attack.
* paired instance outcome  : ``exact`` (alert on a forged object frame) > ``excess_unlocalized`` (only
                             attack-induced alarms elsewhere in the interval) > ``baseline_coincident_only``
                             (every alarm in the interval also occurs in the clean control) > ``none``.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from phantomguard.eval.metrics import AttackLabel, LiteCycle, apply_layers

LINEAGE_COLUMNS = ("frame_index", "kind", "source_cycle", "source_obj", "source_slot", "attack_id")


def _opt_int(value):
    return int(value) if value not in ("", None) else None


def read_lineage(path: Path | str) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    missing = set(LINEAGE_COLUMNS) - set(rows[0] if rows else LINEAGE_COLUMNS)
    if missing:
        raise ValueError(f"lineage sidecar missing columns: {sorted(missing)}")
    return [{"frame_index": int(r["frame_index"]), "kind": r["kind"], "source_cycle": _opt_int(r["source_cycle"]),
             "source_obj": _opt_int(r["source_obj"]), "source_slot": _opt_int(r["source_slot"]),
             "attack_id": r["attack_id"]} for r in rows]


def replay_lineage(source) -> list[dict]:
    """Lineage of an unmodified ReplaySource, in the exact order the source emits frames."""
    rows, fi = [], 0
    lo, _ = source.cycle_range
    for offset, cycle in enumerate(source.selected()):
        cidx = lo + offset
        if cycle.obj_count_header is not None and cycle.meas_counter is not None and cycle.sync_timestamp is not None:
            rows.append({"frame_index": fi, "kind": "header", "source_cycle": cidx, "source_obj": -1,
                         "source_slot": None, "attack_id": ""})
            fi += 1
        for j, (_, raw) in enumerate(cycle.objects):
            rows.append({"frame_index": fi, "kind": "object", "source_cycle": cidx, "source_obj": j,
                         "source_slot": raw[0] if raw else None, "attack_id": ""})
            fi += 1
    return rows


@dataclass(frozen=True)
class ControlAlerts:
    """Alert state of the clean control run on the same segment, keyed by source lineage."""

    object_alerts: frozenset = frozenset()   # (source_cycle, source_obj)
    object_flagged: frozenset = frozenset()
    cycle_alerts: frozenset = frozenset()    # source cycles with a hard cycle alert
    cycles: int = 0
    alert_events: int = 0
    minutes: float = 0.0


def control_alerts(cycles: list[LiteCycle], lineage: list[dict], cfg: dict, layers: tuple[str, ...]) -> ControlAlerts:
    from phantomguard.eval.metrics import score

    by_frame = {r["frame_index"]: r for r in lineage}
    scored = apply_layers(cycles, cfg, layers)
    alerts, flagged, cyc = set(), set(), set()
    for c in scored:
        for v in c.objects:
            row = by_frame.get(v.frame_index)
            if row is None or row["source_cycle"] is None or row["source_obj"] is None:
                continue
            key = (row["source_cycle"], row["source_obj"])
            if v.flagged:
                flagged.add(key)
            if v.alert:
                alerts.add(key)
        if c.cycle_alert and c.header_frame_index is not None:
            row = by_frame.get(c.header_frame_index)
            if row and row["source_cycle"] is not None:
                cyc.add(row["source_cycle"])
    m = score(cycles, cfg, layers)
    return ControlAlerts(frozenset(alerts), frozenset(flagged), frozenset(cyc), len(cycles), m.alert_events, m.minutes)


def alert_episodes(cycles: list[LiteCycle]) -> list[dict]:
    """Non-alert -> alert transitions per track (plus hard cycle alerts), mirroring metrics.score()."""
    episodes: list[dict] = []
    open_eps: dict = {}
    prev_cycle_alert = False
    for c in cycles:
        now = {}
        for k, v in enumerate(c.objects):
            if not v.alert:
                continue
            key = v.track_id if v.track_id is not None else ("unlinked", v.frame_index, c.index, k)
            now.setdefault(key, []).append(v)
        for key, vs in now.items():
            ep = open_eps.get(key)
            if ep is None:
                ep = {"key": key, "kind": "object", "start_cycle": c.index, "end_cycle": c.index, "frames": [],
                      "reasons": Counter()}
                open_eps[key] = ep
                episodes.append(ep)
            ep["end_cycle"] = c.index
            for v in vs:
                ep["frames"].append((c.index, v.frame_index))
                ep["reasons"].update(v.reasons)
        for key in [k for k in open_eps if k not in now]:
            del open_eps[key]
        if c.cycle_alert and not prev_cycle_alert:
            episodes.append({"key": ("cycle", c.index), "kind": "cycle", "start_cycle": c.index, "end_cycle": c.index,
                             "frames": [(c.index, c.header_frame_index)] if c.header_frame_index is not None else [],
                             "reasons": Counter(c.cycle_reasons)})
        prev_cycle_alert = c.cycle_alert
    return episodes


@dataclass
class LocalizationResult:
    row: dict = field(default_factory=dict)
    instances: dict = field(default_factory=dict)   # attack_id -> additive instance fields


def localization_metrics(cycles: list[LiteCycle], labels: list[AttackLabel], lineage: list[dict],
                         control: ControlAlerts, cfg: dict, layers: tuple[str, ...]) -> LocalizationResult:
    scored = apply_layers(cycles, cfg, layers, copy_records=False)
    lin = {r["frame_index"]: r for r in lineage}
    if set(lin) != {f.frame_index for c in scored for f in c.frames}:
        raise ValueError("lineage coverage differs from the emitted stream")
    lab = {x.frame_index: x for x in labels}
    forged_obj = {f.frame_index for c in scored for f in c.frames if f.kind in {"object", "malformed"}
                  and lab[f.frame_index].is_attack}
    forged_attack = {fi: lab[fi].attack_id for fi in forged_obj}
    verdict = {v.frame_index: v for c in scored for v in c.objects}
    cycle_of = {f.frame_index: c.index for c in scored for f in c.frames}

    def key(fi):
        r = lin[fi]
        return (r["source_cycle"], r["source_obj"]) if r["source_cycle"] is not None else None

    real_objects = [v for fi, v in verdict.items() if fi not in forged_obj]
    alert_forged = [fi for fi in forged_obj if verdict[fi].alert]
    real_alert = [v for v in real_objects if v.alert]
    excess_real = [v for v in real_alert if key(v.frame_index) not in control.object_alerts]
    coincident_real = [v for v in real_alert if key(v.frame_index) in control.object_alerts]
    # cycle alarms (hard header/count/cadence...), compared on source cycles
    attacked_cycle_alarms = {lin[c.header_frame_index]["source_cycle"] for c in scored
                             if c.cycle_alert and c.header_frame_index in lin and lin[c.header_frame_index]["source_cycle"] is not None}
    cycle_excess = attacked_cycle_alarms - control.cycle_alerts
    row = {
        "loc_forged_object_frames": len(forged_obj), "loc_alerting_forged_frames": len(alert_forged),
        "loc_alerting_object_frames": len(alert_forged) + len(real_alert),
        "loc_exact_recall": len(alert_forged) / len(forged_obj) if forged_obj else None,
        "loc_exact_precision": len(alert_forged) / (len(alert_forged) + len(real_alert)) if (alert_forged or real_alert) else None,
        "loc_excess_precision": len(alert_forged) / (len(alert_forged) + len(excess_real)) if (alert_forged or excess_real) else None,
        "loc_real_object_frames": len(real_objects), "loc_false_suspect_frames": len(excess_real),
        "loc_false_suspect_rate": len(excess_real) / len(real_objects) if real_objects else None,
        "loc_baseline_coincident_alert_frames": len(coincident_real),
        "loc_cycle_alarm_cycles_attacked": len(attacked_cycle_alarms), "loc_cycle_alarm_cycles_control": len(control.cycle_alerts),
        "loc_cycle_alarm_excess_cycles": len(cycle_excess),
    }
    episodes = alert_episodes(scored)
    forged_set = set(forged_obj)
    cls = Counter()
    for ep in episodes:
        if ep["kind"] == "cycle":
            sc = lin[ep["frames"][0][1]]["source_cycle"] if ep["frames"] and ep["frames"][0][1] in lin else None
            cls["cycle_excess" if sc not in control.cycle_alerts else "cycle_coincident"] += 1
            continue
        fr = [fi for _, fi in ep["frames"]]
        if any(fi in forged_set for fi in fr):
            cls["episodes_forged"] += 1
            continue
        coin = sum(key(fi) in control.object_alerts for fi in fr)
        cls["episodes_real_coincident" if coin * 2 >= len(fr) else "episodes_real_excess"] += 1
    row.update({"loc_" + k: cls.get(k, 0) for k in ("episodes_forged", "episodes_real_coincident", "episodes_real_excess",
                                                    "cycle_excess", "cycle_coincident")})
    row["loc_attacked_alert_events"] = len(episodes)
    row["loc_control_alert_events"] = control.alert_events
    row["loc_paired_excess_alert_events"] = len(episodes) - control.alert_events

    groups: dict = {}
    for fi, aid in forged_attack.items():
        groups.setdefault(str(aid), []).append(fi)
    inst: dict = {}
    for aid, fis in groups.items():
        cyc_idx = [cycle_of[f] for f in fis]
        first, last = min(cyc_idx), max(cyc_idx)
        in_window = [c for c in scored if first <= c.index <= last]
        forged_alert_cycles = sorted(cycle_of[f] for f in fis if verdict[f].alert)
        window_real_excess = [v for c in in_window for v in c.objects if v.alert and v.frame_index not in forged_obj
                              and key(v.frame_index) not in control.object_alerts]
        window_real_coinc = [v for c in in_window for v in c.objects if v.alert and v.frame_index not in forged_obj
                             and key(v.frame_index) in control.object_alerts]
        window_cycle_excess = [c.index for c in in_window if c.cycle_alert and c.header_frame_index in lin
                               and lin[c.header_frame_index]["source_cycle"] not in control.cycle_alerts]
        window_cycle_coinc = [c.index for c in in_window if c.cycle_alert and c.header_frame_index in lin
                              and lin[c.header_frame_index]["source_cycle"] in control.cycle_alerts]
        if forged_alert_cycles:
            status = "exact"
        elif window_real_excess or window_cycle_excess:
            status = "excess_unlocalized"
        elif window_real_coinc or window_cycle_coinc:
            status = "baseline_coincident_only"
        else:
            status = "none"
        reasons_forged = Counter(r for f in fis for r in verdict[f].reasons)
        excess_cycles = [cycle_of[v.frame_index] for v in window_real_excess] + window_cycle_excess
        inst[aid] = {
            "paired_status": status, "exact_identified": bool(forged_alert_cycles),
            "exact_ttd_cycles": forged_alert_cycles[0] - first if forged_alert_cycles else None,
            "excess_ttd_cycles": (min(excess_cycles + forged_alert_cycles) - first)
            if (excess_cycles or forged_alert_cycles) else None,
            "forged_object_frames_loc": len(fis), "forged_frames_alerting": len(forged_alert_cycles),
            "forged_frames_flagged": sum(bool(verdict[f].flagged) for f in fis),
            "window_real_excess_alert_frames": len(window_real_excess),
            "window_real_baseline_coincident_alert_frames": len(window_real_coinc),
            "window_cycle_excess_alarm_cycles": len(window_cycle_excess),
            "window_cycle_baseline_coincident_alarm_cycles": len(window_cycle_coinc),
            "forged_reason_counts": dict(reasons_forged),
            "forged_track_ids": len({verdict[f].track_id for f in fis if verdict[f].track_id is not None}),
        }
    return LocalizationResult(row, inst)
