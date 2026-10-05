"""Episode-level calibration of the v2 soft rules from OUT-OF-RECORDING clean data.

Marginal per-window percentiles do not control persistent alert episodes per minute: one object can
exceed on many overlapping windows (temporal dependence) and many objects/rules multiply the chances.
This module therefore counts *episodes* (non-alert -> alert transitions per track, after fusion) on
clean recordings that were NOT used to fit the model that scores them (inner leave-one-recording-out
CV over the permitted train/validation recordings). Each tunable rule gets the least sensitive
threshold at which the rule alone produces no more than its allowance of episodes there.

Limits that every report must repeat: a handful of minutes of clean data from one session; episodes
within a recording are correlated; recordings are consecutive segments of one session, so nothing here
is an IID or distribution-free guarantee, and new independent sessions are the real test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from phantomguard.eval.metrics import LiteCycle, LiteVerdict, score

TUNABLE: dict[str, dict] = {
    # code: (grid in the rule's natural exceedance units). 0 = the clean-train bound itself.
    "RCS_ENV": {"unit": "RCS grid steps beyond the envelope", "grid": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0]},
    "ARRIVAL_POS": {"unit": "ticks later than the burst position predicts", "grid": [0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]},
    "SPEED": {"unit": "m/s above the clean-train speed bound", "grid": [0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0]},
    "SLOT_RANGE": {"unit": "slot ids above the clean-train maximum", "grid": [0.0, 1.0, 2.0, 4.0, 8.0, 16.0]},
    "ACCEL": {"unit": "m/s^2 above the clean-train bound", "grid": [0.0, 2.0, 5.0, 10.0, 20.0, 40.0, 80.0]},
    "RR_RESID": {"unit": "units/s above the clean-train bound", "grid": [0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0]},
    "POS_SPEED": {"unit": "units/s above the clean-train bound", "grid": [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]},
    "RCS_STD": {"unit": "dB above the conditional std bound", "grid": [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]},
    "COLOC": {"unit": "position units below the co-location minimum", "grid": [0.0, 0.05, 0.1, 0.2]},
    "DRIFT": {"unit": "scaled residual (z) of the position/velocity consistency model",
              "grid": [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 24.0, 40.0, 80.0]},
}
NEVER = float("inf")


def poisson_ci(events: int, minutes: float, alpha: float = 0.05) -> tuple[float, float]:
    """Exact (Garwood) interval for a rate, assuming Poisson events. A guide only: episodes are clustered."""
    from scipy.stats import chi2

    if minutes <= 0:
        return (float("nan"), float("nan"))
    lo = 0.0 if events == 0 else chi2.ppf(alpha / 2, 2 * events) / 2 / minutes
    hi = chi2.ppf(1 - alpha / 2, 2 * events + 2) / 2 / minutes
    return float(lo), float(hi)


def rethreshold(cycles: list[LiteCycle], thresholds: dict[str, float], active: set[str]) -> list[LiteCycle]:
    """Copy of ``cycles`` where the tunable codes are re-derived from stored exceedances ``scores['z:CODE']``.

    Only codes in ``active`` can flag; every other tunable code is removed. Non-tunable reasons (hard,
    JUMP, REPLAY, ...) are untouched. Cycles/frames are shared; only verdicts that change are copied.
    """
    out = []
    tun = set(TUNABLE)
    for c in cycles:
        objs = []
        for v in c.objects:
            keep = [r for r in v.reasons if r not in tun]
            add = [code for code in active if v.scores.get("z:" + code, -NEVER) > thresholds.get(code, NEVER)]
            if add or len(keep) != len(v.reasons):
                v = replace(v, reasons=keep + add, scores=v.scores)
            objs.append(v)
        out.append(replace(c, objects=objs))
    return out


def prune_quiet_tracks(cycles: list[LiteCycle], floor: dict[str, float]) -> list[LiteCycle]:
    """Drop verdicts of tracks that can never alert at any grid value >= ``floor`` (no flags, no exceedance).

    Fusion keeps independent per-track votes, so removing tracks that never flag cannot change another
    track's episodes. Cycle structure (and therefore minutes) is preserved.
    """
    interesting: set = set()
    for c in cycles:
        for v in c.objects:
            if v.track_id is None:
                continue
            if any(r not in TUNABLE for r in v.reasons) or any(
                    v.scores.get("z:" + code, -NEVER) > floor.get(code, NEVER) for code in TUNABLE):
                interesting.add(v.track_id)
    return [replace(c, objects=[v for v in c.objects if v.track_id is None or v.track_id in interesting])
            for c in cycles]


@dataclass
class RuleChoice:
    code: str
    threshold: float
    episodes_at_choice: int
    allowance: int
    curve: list  # [(threshold, episodes)] over the grid, for the audit table


def episodes_for(cycles_by_run: list[list[LiteCycle]], cfg: dict, layers: tuple[str, ...]) -> tuple[int, float]:
    events, minutes = 0, 0.0
    for cycles in cycles_by_run:
        m = score(cycles, cfg, layers)
        events += m.alert_events
        minutes += m.minutes
    return events, minutes


def choose_rule_thresholds(runs: list[list[LiteCycle]], cfg: dict, layers: tuple[str, ...], codes: list[str],
                           allowance: dict[str, int] | int = 0, log=print) -> tuple[dict, list[RuleChoice], dict]:
    """For each tunable rule: the smallest grid threshold whose rule-only system adds <= allowance episodes.

    The reference is the system with every tunable rule inactive (hard + non-tunable soft rules only), so
    episodes owed to other mechanisms are not blamed on a rule. If no grid value meets the allowance the
    rule is left inactive (``inf``) and that is stated, never silently relaxed.
    """
    base_events, minutes = episodes_for([rethreshold(r, {}, set()) for r in runs], cfg, layers)
    choices, thresholds = [], {}
    for code in codes:
        allow = allowance if isinstance(allowance, int) else allowance.get(code, 0)
        curve, pick = [], None
        for c in TUNABLE[code]["grid"]:
            ev, _ = episodes_for([rethreshold(r, {code: c}, {code}) for r in runs], cfg, layers)
            curve.append((c, ev - base_events))
            if pick is None and ev - base_events <= allow:
                pick = (c, ev - base_events)
        threshold = pick[0] if pick else NEVER
        thresholds[code] = threshold
        choices.append(RuleChoice(code, threshold, pick[1] if pick else curve[-1][1], allow, curve))
        log(f"  {code:12s} threshold {threshold if math.isfinite(threshold) else 'inactive'}  "
            f"curve {[(c, e) for c, e in curve]}")
    return thresholds, choices, {"reference_events": base_events, "minutes": minutes}
