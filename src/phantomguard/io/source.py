"""The FrameSource interface. Pipeline code depends only on this module."""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from phantomguard.frames import Frame


@runtime_checkable
class FrameSource(Protocol):
    """Yields CAN frames in arrival order."""

    name: str

    def __iter__(self) -> Iterator[Frame]: ...
