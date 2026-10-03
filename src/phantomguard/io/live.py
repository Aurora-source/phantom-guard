"""Live CAN capture. Out of scope for the offline prototype (CLAUDE.md)."""

from __future__ import annotations

from typing import Iterator

from phantomguard.frames import Frame


class LiveSource:
    name = "live"

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("LiveSource is a stub: live capture is out of scope in the offline phase")

    def __iter__(self) -> Iterator[Frame]:  # pragma: no cover
        raise NotImplementedError
