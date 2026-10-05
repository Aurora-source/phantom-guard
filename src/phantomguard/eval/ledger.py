"""Failure ledgers: why each clean alert episode happened, and how each attack instance fared.

Offline diagnostics only. The clean ledger uses detector output alone (no labels exist for clean data).
The attack ledger joins labels/lineage/lifecycle to detector output after detection; those joins never
reach the detector, its features, thresholds or calibration.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from phantomguard.config import bval, effective_cfg
from phantomguard.detect.common import REASONS, is_hard
from phantomguard.detect.evidence import REASON_CLASS
from phantomguard.io.replay import ReplaySource


def _brief(ev: dict) -> dict:
    keep = ("reason", "class", "scope", "status", "observed", "expected", "lo", "hi", "normalized")
    out = {k: ev.get(k) for k in keep}
    out["support"] = ev.get("support")
    out["basis"] = (ev.get("suspects") or {}).get("basis")
    return out


def identity(baseline: dict, ae, tag: str, scoring_cfg: dict) -> dict:
    meta = getattr(ae, "metadata", None) or {}
    fusion = scoring_cfg["fusion"]
    return {"tag": tag, "ae_artifact_id": meta.get("artifact_id"),
            "detector_profile": (baseline.get("detector_contract") or {}).get("value", "legacy"),
            "fusion_m": fusion["m"], "fusion_n": fusion["n"], "learned_alone": fusion.get("learned_alone", True),
            "soft_quantile": (baseline.get("soft_quantile") or {}).get("value")}


def clean_episode_ledger(detector, source: ReplaySource, scoring_cfg: dict, ident: dict, *, split: str, part: str) -> tuple[list[dict], dict]:
    """Run ``detector`` over a clean segment and describe every alert episode (non-alert -> alert).

    Returns (episodes, totals). Slot numbers are diagnostic labels only and are never compared or used
    as a feature. ``vote_*`` columns describe the persistence window that produced the alert.
    """
    tick = scoring_cfg["units"]["tick_seconds"]
    m, n = scoring_cfg["fusion"]["m"], scoring_cfg["fusion"]["n"]
    lo, _ = source.cycle_range
    episodes: list[dict] = []
    active: dict = {}
    recent: dict = defaultdict(list)           # track -> [(cycle, flagged codes)] for the vote window
    first_t = None
    prev_cycle_alert = False
    cycles = objects = 0
    for res in detector.run(source):
        cycles += 1
        objects += len(res.objects)
        if first_t is None and res.header_t is not None:
            first_t = res.header_t
        t_s = ((res.header_t - first_t) * tick) if (res.header_t is not None and first_t is not None) else None
        now = {}
        for k, v in enumerate(res.objects):
            key = v.track_id if v.track_id is not None else ("unlinked", v.frame_index)
            if v.flagged:
                recent[key].append((res.index, tuple(v.reasons)))
                recent[key] = recent[key][-n:]
            if v.alert:
                now.setdefault(key, []).append(v)
        live_tracks = {tr.track_id: tr for tr in detector.tracks.active.values()}
        for key, vs in now.items():
            if key in active:
                ep = active[key]
                ep["last_cycle"] = res.index
                ep["alert_object_cycles"] += 1
                continue
            v = vs[0]
            tr = live_tracks.get(key)
            window = recent.get(key, [])
            reasons_now = [r for r in v.reasons]
            hard_now = [r for r in reasons_now if is_hard(r)]
            union = Counter(r for _, codes in window for r in codes)
            ev = {e["reason"]: e for e in v.evidence}
            ep = {**ident, "split": split, "part": part, "file": source.path.name, "segment_lo": lo,
                  "kind": "object", "start_cycle": res.index, "recording_cycle": lo + res.index,
                  "start_time_s": t_s, "start_frame_index": v.frame_index, "last_cycle": res.index,
                  "alert_object_cycles": 1, "track_id": v.track_id,
                  "track_age_cycles": tr.total_points if tr else None,
                  "track_born_cycle": tr.born_cycle if tr else None,
                  "track_born_by_jump": bool(tr.born_by_jump) if tr else None,
                  "track_moving_points": tr.moving_points if tr else None,
                  "slot_diagnostic_only": v.slot, "in_roi": v.in_roi, "moving": v.moving,
                  "x": v.x, "y": v.y,
                  "trigger": "hard" if hard_now else "persistence",
                  "reasons_now": ";".join(reasons_now), "reasons_hard_now": ";".join(hard_now),
                  "reason_classes_now": ";".join(sorted({REASON_CLASS.get(r, "?") for r in reasons_now})),
                  "vote_window_reasons": json.dumps(dict(union), sort_keys=True),
                  "votes_flagged": len(window), "votes_required": m, "votes_window": n,
                  "scores": json.dumps({k2: round(x, 4) for k2, x in v.scores.items()}, sort_keys=True),
                  "evidence": json.dumps([_brief(e) for e in v.evidence if e["reason"] != "PERSISTENCE"][:6], sort_keys=True),
                  "has_persistence_record": "PERSISTENCE" in ev}
            active[key] = ep
            episodes.append(ep)
        for key in [k for k in active if k not in now]:
            del active[key]
            recent.pop(key, None)
        if res.cycle_alert and not prev_cycle_alert:
            ep = {**ident, "split": split, "part": part, "file": source.path.name, "segment_lo": lo, "kind": "cycle",
                  "start_cycle": res.index, "recording_cycle": lo + res.index, "start_time_s": t_s,
                  "start_frame_index": res.header_frame_index, "last_cycle": res.index, "alert_object_cycles": 0,
                  "trigger": "hard", "reasons_now": ";".join(res.cycle_reasons),
                  "reasons_hard_now": ";".join(r for r in res.cycle_reasons if is_hard(r)),
                  "reason_classes_now": ";".join(sorted({REASON_CLASS.get(r, "?") for r in res.cycle_reasons})),
                  "evidence": json.dumps([_brief(e) for e in res.cycle_evidence][:6], sort_keys=True)}
            episodes.append(ep)
        prev_cycle_alert = res.cycle_alert
    minutes = (res.header_t - first_t) * tick / 60.0 if cycles > 1 and first_t is not None and res.header_t is not None else 0.0
    return episodes, {"cycles": cycles, "object_cycles": objects, "minutes": minutes, "episodes": len(episodes)}


def summarize_clean(episodes: list[dict], totals: dict) -> dict:
    """Group episodes by dominant cause so the ledger answers 'what caused the alerts'."""
    by_reason = Counter()
    by_class = Counter()
    for ep in episodes:
        codes = [c for c in (ep.get("reasons_now") or "").split(";") if c] or ["PERSISTENCE_ONLY"]
        by_reason[codes[0]] += 1
        by_class[(ep.get("reason_classes_now") or "persistence").split(";")[0]] += 1
    return {"episodes": len(episodes), **totals, "by_first_reason": dict(by_reason), "by_first_class": dict(by_class),
            "episodes_per_minute": len(episodes) / totals["minutes"] if totals.get("minutes") else None}
