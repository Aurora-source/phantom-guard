"""Verdict types shared by the detector layers.

Each layer adds reason codes (and scores) to per-object verdicts or to the cycle. A reason is
*hard* (flags immediately and alerts) or *soft* (flags the object-cycle; alerting needs M of the
last N cycles of the track to be flagged, see fusion.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    # replay fingerprint
    "REPLAY": ("replay", False),
    # learned normal
    "LEARNED": ("learned", False),
}

LAYERS = ("protocol", "kinematic", "replay", "learned")


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

    def add(self, code: str) -> None:
        if code not in self.reasons:
            self.reasons.append(code)


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
