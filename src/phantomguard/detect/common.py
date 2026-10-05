"""Verdict types shared by the detector layers.

Each layer adds reason codes (and scores) to per-object verdicts or to the cycle. A reason is
*hard* (flags immediately and alerts) or *soft* (flags the object-cycle; alerting needs M of the
last N cycles of the track to be flagged, see fusion.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from phantomguard.detect.evidence import make_evidence

# Reason code -> (layer, hard?)
REASONS: dict[str, tuple[str, bool]] = {
    # protocol, cycle level
    "COUNT_MISMATCH": ("protocol", True),
    "COUNT_RANGE": ("protocol", True),
    "COUNTER": ("protocol", True),
    "CADENCE": ("protocol", True),
    "STATUS": ("protocol", True),
    "BAD_ID": ("protocol", True),
    "SHORT_HEADER": ("protocol", True),
    "HEADER_LEN": ("protocol", True),
    "NO_HEADER": ("protocol", True),
    # protocol, object level
    "FRAME_LEN": ("protocol", True),
    "ARRIVAL": ("protocol", True),
    "BURST_GAP": ("protocol", True),
    "RANGE_ORDER": ("protocol", True),
    "DUP_SLOT": ("protocol", True),
    "SLOT_RANGE": ("protocol", True),
    "FIXED_FIELD": ("protocol", True),
    # kinematic / physics
    "RCS_GRID": ("kinematic", True),
    "RCS_RANGE": ("kinematic", True),
    "SPEED": ("kinematic", True),
    "ACCEL": ("kinematic", False),
    "RR_RESID": ("kinematic", False),
    "POS_SPEED": ("kinematic", False),
    "RCS_STD": ("kinematic", False),
    "RCS_BAND": ("kinematic", False),
    "JUMP": ("kinematic", False),
    "COLOC": ("kinematic", False),
    # detector profile v2 (soft by default; hardness is set by fusion.hard_codes)
    "ARRIVAL_POS": ("protocol", False),   # late arrival relative to the frame's position in the burst
    "RCS_ENV": ("kinematic", False),      # outside the smooth range-conditional RCS envelope
    "DRIFT": ("kinematic", False),        # position change inconsistent with reported velocity (moving regime)
    "DRIFT_STATIC": ("kinematic", False),  # the same for windows whose mean reported speed is below the moving threshold
    "DRIFT_EWMA": ("kinematic", False),    # EWMA of the per-step position/velocity residual (moving steps)
    # replay fingerprint
    "REPLAY": ("replay", False),
    # learned normal
    "LEARNED": ("learned", False),
}

LAYERS = ("protocol", "kinematic", "replay", "learned")


def rule_thresholds_of(baseline: dict) -> dict[str, float]:
    """Calibrated exceedance offsets per soft rule (profile v2). ``null`` in JSON means the rule is inactive."""
    entry = baseline.get("rule_thresholds")
    values = entry["value"] if isinstance(entry, dict) and "value" in entry else (entry or {})
    return {code: (float("inf") if value is None else float(value)) for code, value in values.items()}


SUPPORTED_CONTRACTS = {"v2": 1}   # profile -> newest contract schema this code can run


def contract_of(baseline: dict) -> dict | None:
    """Detector profile contract stored in the baseline (profile v2+), or None for the legacy profile.

    A baseline written by newer code (unknown profile or a newer schema) fails here with a clear message
    instead of being scored with rules it was not calibrated for.
    """
    c = baseline.get("detector_contract")
    value = (c["value"] if isinstance(c, dict) and "value" in c else c) if c else None
    if value is not None:
        profile, schema = value.get("profile"), value.get("schema")
        if profile not in SUPPORTED_CONTRACTS or not isinstance(schema, int) or schema > SUPPORTED_CONTRACTS[profile]:
            raise ValueError(f"baseline detector contract profile={profile!r} schema={schema!r} is not supported by this "
                             f"code (supports {SUPPORTED_CONTRACTS}); restore the bundle matching this commit or "
                             f"re-run baseline/calibrate with this version")
    return value


def layer_of(code: str) -> str:
    return REASONS[code][0]


def is_hard(code: str) -> bool:
    return REASONS[code][1]


@dataclass
class ObjVerdict:
    frame_index: int
    slot: int | None
    x: float | None
    y: float | None
    vx: float | None
    vy: float | None
    in_roi: bool
    moving: bool
    track_id: int | None = None
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    flagged: bool = False
    alert: bool = False
    timestamp_ticks: int | None = None
    score_status: dict[str, str] = field(default_factory=dict)
    # Additive, JSON-compatible per-reason evidence (see detect/evidence.py). Optional for consumers.
    evidence: list[dict] = field(default_factory=list)

    def add(self, code: str) -> None:
        if code not in self.reasons:
            self.reasons.append(code)

    def note(self, code: str, **fields) -> None:
        """Add ``code`` (once) and attach an evidence record for it."""
        self.add(code)
        self.evidence.append(make_evidence(code, **fields))


@dataclass(frozen=True)
class FrameRecord:
    """One final emitted frame, retained for evaluator-only label joins by index."""

    frame_index: int
    timestamp_ticks: int
    can_id: int
    kind: str
    reasons: tuple[str, ...] = ()

@dataclass
class CycleResult:
    index: int
    header_t: int | None
    objects: list[ObjVerdict]
    cycle_reasons: list[str] = field(default_factory=list)
    cycle_alert: bool = False
    latency_ms: float = 0.0
    frames: list[FrameRecord] = field(default_factory=list)
    header_frame_index: int | None = None
    closed_t: int | None = None
    assembly_delay_ticks: int | None = None
    layer_status: dict[str, str] = field(default_factory=dict)
    learned_windows: dict[int, tuple[tuple[float, ...], bool]] = field(default_factory=dict)
    assembly_cpu_ms: float = 0.0
    detector_cpu_ms: float = 0.0
    cycle_evidence: list[dict] = field(default_factory=list)
    # Thread CPU time of process_cycle (time.thread_time). Its resolution is platform dependent (about
    # 15.6 ms on Windows, ns on Linux); per-cycle percentiles are only meaningful where it is fine.
    detector_thread_cpu_ms: float = 0.0
