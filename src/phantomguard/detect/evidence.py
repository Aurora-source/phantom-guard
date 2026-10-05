"""Additive per-reason evidence records (schema 1).

An evidence record explains *why* one reason code fired, using only observed input and frozen
calibration. It is JSON-compatible (plain dicts of numbers/strings/lists) so it can ride along
with existing verdict exports. It never carries a probability: ``normalized`` is a residual in
units of the calibrated scale, and ``support`` states how much clean data stood behind the bound.

Evidence localises a suspicious frame, object, track or scene as far as its scope supports. It
cannot identify a human attacker, authenticate a sensor or prove physical reality. A forgery
that is observationally indistinguishable in every available field stays unflagged.
"""

from __future__ import annotations

import math
from typing import Iterable

EVIDENCE_SCHEMA = 1

# How trustworthy a rule's bound is as a statement about the radar protocol or physics.
#   structural        - violation is impossible in a well-formed stream (format, count, uniqueness)
#   exact_regularity  - zero violations in all clean data, but not proven by the protocol itself
#   empirical_tail    - a quantile/maximum of a continuous clean distribution; unusual != forged
#   learned           - learned-normal reconstruction/innovation score
#   replay            - fingerprint match against a clean library or the stream's own past
#   motion_evidence   - residual of an observed-motion consistency model
REASON_CLASS: dict[str, str] = {
    "COUNT_MISMATCH": "structural", "COUNTER": "structural", "STATUS": "exact_regularity",
    "BAD_ID": "structural", "SHORT_HEADER": "structural", "HEADER_LEN": "structural",
    "NO_HEADER": "structural", "FRAME_LEN": "structural", "DUP_SLOT": "structural",
    "FIXED_FIELD": "exact_regularity", "RANGE_ORDER": "exact_regularity",
    "COUNT_RANGE": "empirical_tail", "CADENCE": "empirical_tail", "ARRIVAL": "empirical_tail",
    "BURST_GAP": "exact_regularity", "SLOT_RANGE": "empirical_tail",
    "RCS_GRID": "exact_regularity", "RCS_RANGE": "empirical_tail", "SPEED": "empirical_tail",
    "ACCEL": "empirical_tail", "RR_RESID": "empirical_tail", "POS_SPEED": "empirical_tail",
    "RCS_STD": "empirical_tail", "RCS_BAND": "empirical_tail", "JUMP": "empirical_tail",
    "COLOC": "empirical_tail", "REPLAY": "replay", "LEARNED": "learned",
    "DRIFT": "motion_evidence", "DRIFT_STATIC": "motion_evidence", "RCS_INNOV": "empirical_tail", "ARRIVAL_POS": "empirical_tail",
}

STATUS_TRIGGER = "trigger"          # the current observation itself violates the bound
STATUS_PERSISTENT = "persistent"    # alert continues from earlier flagged cycles (M-of-N votes)


def _num(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def make_evidence(reason: str, *, scope: str = "object", status: str = STATUS_TRIGGER,
                  frames: Iterable[int] = (), cycles: tuple[int, int] | None = None,
                  observed=None, expected=None, lo=None, hi=None, normalized=None,
                  support: dict | None = None, suspect_frames: Iterable[int] = (),
                  suspect_track: int | None = None, suspect_basis: str = "exact_frame",
                  note: str | None = None, rule_class: str | None = None) -> dict:
    """Build one JSON-compatible evidence record. ``suspect_basis`` states how far the
    attribution goes: ``exact_frame``, ``duplicate_slot_both``, ``colocated_pair``,
    ``header_or_unknown_object`` (count/cadence/counter) or ``scene``."""
    return {
        "schema": EVIDENCE_SCHEMA, "reason": reason, "class": rule_class or REASON_CLASS.get(reason, "unknown"),
        "scope": scope, "status": status, "frames": [int(f) for f in frames],
        "cycles": [int(cycles[0]), int(cycles[1])] if cycles is not None else None,
        "observed": _num(observed), "expected": _num(expected), "lo": _num(lo), "hi": _num(hi),
        "normalized": _num(normalized), "support": support,
        "suspects": {"frames": [int(f) for f in suspect_frames], "track_id": suspect_track,
                     "basis": suspect_basis},
        "note": note,
    }


def support_record(n: int | None, status: str, **extra) -> dict:
    """``status`` is one of supported, low_support, global_fallback, quantization_floor, unscored."""
    return {"n": None if n is None else int(n), "status": status, **extra}
