"""Attacker capability levels A0-A4 (cumulative: each level gets everything the lower ones do)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    name: str
    timing_in_window: bool  # A1+: frames arrive inside the learned arrival window
    free_slot: bool  # A1+: slot drawn from slots free in this cycle, from the real slot distribution
    fix_header: bool  # A2+: forges the 0x60A count so header == frames received
    slot_unique: bool  # A2+: avoids slot collisions with real objects over time
    physics: bool  # A3+: smooth constant-velocity paths, velocity consistent with motion, stable real RCS
    order_aware: bool  # A3+: inserts at the range-sorted position, keeps the burst back-to-back
    data_aware: bool  # A4: replays / reshapes real recorded tracks (keeps hold pattern, range-conditional RCS)


LEVELS: dict[str, Level] = {
    "A0": Level("A0", False, False, False, False, False, False, False),
    "A1": Level("A1", True, True, False, False, False, False, False),
    "A2": Level("A2", True, True, True, True, False, False, False),
    "A3": Level("A3", True, True, True, True, True, True, False),
    "A4": Level("A4", True, True, True, True, True, True, True),
}
