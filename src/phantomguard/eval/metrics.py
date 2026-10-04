"""Clean-data metrics: false positives per object-cycle and persistent alerts per minute.

Fusion is re-applied offline to stored reason codes so every layer subset (ablation) is scored
from one detector run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Iterable

import numpy as np

from phantomguard.detect.common import CycleResult, ObjVerdict
from phantomguard.detect.fusion import Fusion


# The fusion operating point used before 2026-10-04 (3 of 5, autoencoder may alert alone). Reports
# re-apply it offline to the same detector output, so the trade-off of the calibrated point is measured
# like-for-like. Measurement only: it never feeds a threshold.
PREVIOUS_FUSION = {"m": 3, "n": 5, "learned_alone": True}


def with_fusion(cfg: dict, fusion: dict) -> dict:
    return {**cfg, "fusion": {**cfg["fusion"], **fusion}}


@dataclass
class LiteVerdict:
    track_id: int | None
    in_roi: bool
    moving: bool
    reasons: list[str]
    flagged: bool = False
    alert: bool = False
    frame_index: int | None = None
    timestamp_ticks: int | None = None
    slot: int | None = None
    scores: dict[str, float] = field(default_factory=dict)
    score_status: dict[str, str] = field(default_factory=dict)
    window_moving: bool | None = None


@dataclass
class LiteCycle:
    header_t: int | None
    cycle_reasons: list[str]
    objects: list[LiteVerdict]
    cycle_alert: bool = False
    latency_ms: float = 0.0
    index: int = 0
    header_frame_index: int | None = None
    frames: list = field(default_factory=list)
    closed_t: int | None = None
    assembly_delay_ticks: int | None = None
    layer_status: dict[str, str] = field(default_factory=dict)
    detector_cpu_ms: float = 0.0
    assembly_cpu_ms: float = 0.0


def lite(res: CycleResult) -> LiteCycle:
    """Compact geometry, preserving frame identity, timing, scores and detector outcomes.

    Additive fields keep the clean evaluator API intact. Headers and undecodable frames
    are retained in ``frames``; labels are joined by final emitted frame index only.
    """
    return LiteCycle(res.header_t, list(res.cycle_reasons),
                     [LiteVerdict(v.track_id, v.in_roi, v.moving, list(v.reasons), v.flagged, v.alert,
                                  v.frame_index, getattr(v, "timestamp_ticks", None), v.slot,
                                  dict(v.scores), dict(getattr(v, "score_status", {})),
                                  getattr(res, "learned_windows", {}).get(v.frame_index, (None, None))[1])
                      for v in res.objects],
                     cycle_alert=res.cycle_alert, latency_ms=res.latency_ms, index=res.index,
                     header_frame_index=getattr(res, "header_frame_index", None),
                     frames=list(getattr(res, "frames", [])), closed_t=getattr(res, "closed_t", None),
                     assembly_delay_ticks=getattr(res, "assembly_delay_ticks", None),
                     layer_status=dict(getattr(res, "layer_status", {})),
                     detector_cpu_ms=getattr(res, "detector_cpu_ms", res.latency_ms),
                     assembly_cpu_ms=getattr(res, "assembly_cpu_ms", 0.0))


@dataclass
class CleanMetrics:
    cycles: int = 0
    minutes: float = 0.0
    obj_cycles: int = 0
    roi_obj_cycles: int = 0
    roi_moving_obj_cycles: int = 0
    flagged: int = 0
    flagged_roi_moving: int = 0
    flagged_roi_static: int = 0
    alerting: int = 0
    track_alert_events: int = 0
    cycle_alert_events: int = 0
    reasons: Counter = field(default_factory=Counter)
    cycle_reasons: Counter = field(default_factory=Counter)

    @property
    def alert_events(self) -> int:
        return self.track_alert_events + self.cycle_alert_events

    @property
    def alerts_per_minute(self) -> float:
        return self.alert_events / self.minutes if self.minutes else float("nan")

    def row(self) -> dict:
        roi_static = self.roi_obj_cycles - self.roi_moving_obj_cycles
        return {
            "cycles": self.cycles, "minutes": round(self.minutes, 3), "object_cycles": self.obj_cycles,
            "fp_rate_flagged": self.flagged / self.obj_cycles if self.obj_cycles else 0.0,
            "fp_rate_flagged_roi_static": self.flagged_roi_static / roi_static if roi_static else 0.0,
            "fp_rate_flagged_roi_moving": (self.flagged_roi_moving / self.roi_moving_obj_cycles
                                           if self.roi_moving_obj_cycles else 0.0),
            "fp_rate_alerting": self.alerting / self.obj_cycles if self.obj_cycles else 0.0,
            "alert_events": self.alert_events, "alerts_per_minute": self.alerts_per_minute,
        }


def score(cycles: list[LiteCycle], cfg: dict, layers: tuple[str, ...]) -> CleanMetrics:
    fus = Fusion(cfg, layers)
    m = CleanMetrics()
    tick = cfg["units"]["tick_seconds"]
    ts = [c.header_t for c in cycles if c.header_t is not None]
    m.minutes = (ts[-1] - ts[0]) * tick / 60.0 if len(ts) > 1 else 0.0
    alerting_tracks: set = set()
    prev_cycle_alert = False
    for c in cycles:
        fus.apply(c)  # duck-typed: LiteCycle has the fields Fusion uses
        m.cycles += 1
        act = fus.active(c.cycle_reasons)
        m.cycle_reasons.update(act)
        if c.cycle_alert and not prev_cycle_alert:
            m.cycle_alert_events += 1
        prev_cycle_alert = c.cycle_alert
        now_alerting = set()
        for object_index, v in enumerate(c.objects):
            m.obj_cycles += 1
            m.reasons.update(fus.active(v.reasons))
            if v.in_roi:
                m.roi_obj_cycles += 1
                m.roi_moving_obj_cycles += v.moving
            if v.flagged:
                m.flagged += 1
                if v.in_roi:
                    if v.moving:
                        m.flagged_roi_moving += 1
                    else:
                        m.flagged_roi_static += 1
            if v.alert:
                m.alerting += 1
                key = v.track_id if v.track_id is not None else ("unlinked", v.frame_index, c.index, object_index)
                if key not in alerting_tracks and key not in now_alerting:
                    m.track_alert_events += 1
                now_alerting.add(key)
        alerting_tracks = now_alerting
    return m


def latency_stats(cycles: list[LiteCycle]) -> dict:
    lat = np.array([c.latency_ms for c in cycles])
    if not len(lat):
        return {"p50_ms": None, "p99_ms": None, "max_ms": None, "n": 0}
    return {"p50_ms": float(np.percentile(lat, 50)), "p99_ms": float(np.percentile(lat, 99)),
            "max_ms": float(lat.max()), "n": int(len(lat)),
            "detector_p99_ms": float(np.percentile([c.detector_cpu_ms for c in cycles], 99)),
            "assembly_cpu_p99_ms": float(np.percentile([c.assembly_cpu_ms for c in cycles], 99))}


def assembly_stats(cycles: list[LiteCycle], cfg: dict) -> dict:
    """Capture assembly delay is not CPU processing time. EOF closure is unmeasured."""
    delays = [c.assembly_delay_ticks * cfg["units"]["tick_seconds"] * 1000
              for c in cycles if c.assembly_delay_ticks is not None]
    return {"assembly_n": len(delays), "assembly_eof_unmeasured": sum(c.closed_t is None for c in cycles),
            "assembly_p50_ms": float(np.percentile(delays, 50)) if delays else None,
            "assembly_p99_ms": float(np.percentile(delays, 99)) if delays else None}


@dataclass(frozen=True)
class AttackLabel:
    frame_index: int
    is_attack: bool
    attack_id: str = ""
    attack_type: str = ""
    level: str = ""


def parse_labels(rows: Iterable[dict]) -> list[AttackLabel]:
    out = []
    for row in rows:
        missing = {"frame_index", "is_attack", "attack_id", "attack_type", "level"} - row.keys()
        if missing:
            raise ValueError(f"label sidecar missing columns: {sorted(missing)}")
        flag = str(row["is_attack"]).lower()
        if flag not in {"true", "false", "1", "0"}:
            raise ValueError(f"invalid is_attack at frame {row['frame_index']}: {flag}")
        fi = int(row["frame_index"])
        if fi < 0:
            raise ValueError("frame_index must be nonnegative")
        label = AttackLabel(fi, flag in {"true", "1"}, str(row["attack_id"] or ""),
                            str(row["attack_type"] or ""), str(row["level"] or ""))
        if label.is_attack and not all((label.attack_id, label.attack_type, label.level)):
            raise ValueError(f"attack frame {fi} needs attack_id, attack_type and level")
        out.append(label)
    return out


def align_labels(cycles: list[LiteCycle], labels: list[AttackLabel]) -> dict[int, AttackLabel]:
    """Require one sidecar row per emitted frame, including headers and malformed frames."""
    frames = [f.frame_index for c in cycles for f in c.frames]
    if len(frames) != len(set(frames)):
        raise ValueError("detector emitted duplicate frame indices")
    if set(frames) != set(range(len(frames))):
        raise ValueError("detector frame records are incomplete or not final-stream indices")
    objects = [v.frame_index for c in cycles for v in c.objects]
    object_frames = {f.frame_index for c in cycles for f in c.frames if f.kind in {"object", "malformed"}}
    if len(objects) != len(set(objects)) or set(objects) != object_frames:
        raise ValueError("object verdicts have duplicate indices or incomplete frame coverage")
    indices = [label.frame_index for label in labels]
    if len(indices) != len(set(indices)):
        raise ValueError("duplicate label frame_index")
    if set(indices) != set(frames):
        raise ValueError(f"label coverage differs from emitted stream: missing={len(set(frames)-set(indices))}, "
                         f"extra={len(set(indices)-set(frames))}")
    return {label.frame_index: label for label in labels}


def apply_layers(cycles: list[LiteCycle], cfg: dict, layers: tuple[str, ...]) -> list[LiteCycle]:
    out = deepcopy(cycles)
    fus = Fusion(cfg, layers)
    for cycle in out:
        fus.apply(cycle)
    return out


def attack_metrics(cycles: list[LiteCycle], labels: list[AttackLabel], cfg: dict,
                   layers: tuple[str, ...]) -> tuple[dict, list[dict]]:
    """Cycle detection and direct forged-frame identification have separate denominators.

    An instance is observable from its first to last labelled cycle, inclusive. Any
    persistent object alert or hard cycle alert in that interval detects the instance.
    Object identification requires an alert on the exact labelled 0x60B frame, never
    a matching slot or a broadcast header violation. Undetected instances remain in
    the rate denominator. Those touching EOF are additionally marked right-censored.
    """
    aligned = align_labels(cycles, labels)
    scored = apply_layers(cycles, cfg, layers)
    fi_cycle = {f.frame_index: c for c in scored for f in c.frames}
    fi_frame = {f.frame_index: f for c in scored for f in c.frames}
    verdicts = {v.frame_index: v for c in scored for v in c.objects}
    groups: dict[str, list[AttackLabel]] = {}
    for label in labels:
        if label.is_attack:
            groups.setdefault(label.attack_id, []).append(label)
    instances = []
    for attack_id, group in groups.items():
        if len({(g.attack_type, g.level) for g in group}) != 1:
            raise ValueError(f"inconsistent metadata for attack_id {attack_id}")
        first = min(fi_cycle[g.frame_index].index for g in group)
        last = max(fi_cycle[g.frame_index].index for g in group)
        onset_t = min(fi_frame[g.frame_index].timestamp_ticks for g in group)
        detected = next((c for c in scored if first <= c.index <= last and
                         (c.cycle_alert or any(v.alert for v in c.objects))), None)
        obj_labels = [g for g in group if fi_frame[g.frame_index].kind in {"object", "malformed"}]
        identified = sum(bool(verdicts.get(g.frame_index) and verdicts[g.frame_index].alert) for g in obj_labels)
        identification_cycle = min((fi_cycle[g.frame_index].index for g in obj_labels
                                    if verdicts.get(g.frame_index) and verdicts[g.frame_index].alert), default=None)
        states = {"moving" if verdicts[g.frame_index].moving else "static" for g in obj_labels
                  if g.frame_index in verdicts and fi_frame[g.frame_index].kind == "object"}
        decision_t = detected.closed_t if detected else None
        instances.append({"attack_id": attack_id, "attack_type": group[0].attack_type, "level": group[0].level,
                          "first_cycle": first, "last_cycle": last, "observed_attack_frames": len(group),
                          "forged_object_frames": len(obj_labels), "identified_object_frames": identified,
                          "detected": detected is not None, "identified": identified > 0,
                          "detected_by_cycle_alert": bool(detected and detected.cycle_alert),
                          "identification_ttd_cycles": identification_cycle-first
                          if identification_cycle is not None else None,
                          "motion": next(iter(states)) if len(states) == 1 else ("mixed" if states else "unknown"),
                          "ttd_cycles": detected.index - first if detected else None,
                          "ttd_seconds": max(0, decision_t-onset_t)*cfg["units"]["tick_seconds"]
                          if decision_t is not None else None,
                          "right_censored": bool(scored and last == scored[-1].index),
                          "eof_decision_time_unmeasured": bool(detected and decision_t is None)})
    attacked_objects = [v for c in scored for v in c.objects if aligned[v.frame_index].is_attack]
    all_objects = [v for c in scored for v in c.objects]
    valid_scores = [v for v in all_objects if v.in_roi and v.frame_index in aligned]
    row = {"attack_instances": len(instances), "detected_instances": sum(i["detected"] for i in instances),
           "identified_instances": sum(i["identified"] for i in instances),
           "undetected_instances": sum(not i["detected"] for i in instances),
           "right_censored_instances": sum(i["right_censored"] for i in instances),
           "attack_instance_detection_rate": np.mean([i["detected"] for i in instances]).item() if instances else None,
           "forged_object_frames": len(attacked_objects),
           "identified_object_frames": sum(v.alert for v in attacked_objects),
           "object_detection_rate": np.mean([v.alert for v in attacked_objects]).item() if attacked_objects else None}
    detected_times = [i["ttd_cycles"] for i in instances if i["ttd_cycles"] is not None]
    row["median_ttd_cycles_detected"] = float(np.median(detected_times)) if detected_times else None
    row["ttd_measured_instances"] = len(detected_times)
    for reason in ("RANGE_ORDER", "BURST_GAP"):
        n = sum(reason in v.reasons for v in all_objects) if "protocol" in layers else 0
        row[f"{reason.lower()}_object_frames"] = n
        row[f"{reason.lower()}_per_object_frame"] = n/len(all_objects) if all_objects else None
    for cls in ("static", "moving"):
        vs = [v for v in attacked_objects if v.in_roi and v.moving == (cls == "moving")]
        row[f"{cls}_forged_roi_objects"] = len(vs)
        row[f"{cls}_object_detection_rate"] = float(np.mean([v.alert for v in vs])) if vs else None
    for model in ("ae", "iforest"):
        vs = [v for v in valid_scores if model in v.scores and np.isfinite(v.scores[model])]
        y = [aligned[v.frame_index].is_attack for v in vs]
        row[f"{model}_valid_scores"] = len(vs)
        row[f"{model}_attack_scores"] = sum(y)
        row[f"{model}_auroc"] = None
        row[f"{model}_auroc_status"] = "both_classes_required"
        if len(set(y)) == 2:
            from sklearn.metrics import roc_auc_score
            row[f"{model}_auroc"] = float(roc_auc_score(y, [v.scores[model] for v in vs]))
            row[f"{model}_auroc_status"] = "ok"
        for cls in ("static", "moving"):
            cv = [v for v in vs if (v.window_moving if v.window_moving is not None else v.moving) == (cls == "moving")]
            cy = [aligned[v.frame_index].is_attack for v in cv]
            row[f"{model}_{cls}_valid_scores"] = len(cv)
            row[f"{model}_{cls}_attack_scores"] = sum(cy)
            row[f"{model}_{cls}_auroc"] = None
            row[f"{model}_{cls}_auroc_status"] = "both_classes_required"
            if len(set(cy)) == 2:
                from sklearn.metrics import roc_auc_score
                row[f"{model}_{cls}_auroc"] = float(roc_auc_score(cy, [v.scores[model] for v in cv]))
                row[f"{model}_{cls}_auroc_status"] = "ok"
    return row, instances
