"""Group a causal frame stream into scan cycles.

A cycle opens at a 0x60A header and closes when the next header arrives (or at end of stream).
This is causal: the detector judges cycle k once cycle k+1 has started, i.e. one cycle of
latency. Frames are indexed by their position in the stream (``frame_index``) so verdicts can
later be joined with attack labels by the evaluator only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from phantomguard.frames import (
    CAN_ID_HEADER,
    CAN_ID_OBJECT,
    Frame,
    Header,
    MalformedFrame,
    RadarObject,
    decode_object,
    parse_header,
)


@dataclass
class ObjObs:
    frame_index: int
    t: int  # arrival timestamp (ticks)
    data: bytes
    obj: RadarObject | None  # None when the frame could not be decoded
    offset: int | None  # ticks after the cycle header, None without header
    position: int  # order of arrival inside the cycle (0-based)


@dataclass
class OtherFrame:
    frame_index: int
    frame: Frame
    reason: str  # BAD_ID, SHORT_HEADER


@dataclass
class Cycle:
    index: int  # sequential within the stream
    header: Header | None
    header_frame_index: int | None
    header_t: int | None
    objects: list[ObjObs] = field(default_factory=list)
    other: list[OtherFrame] = field(default_factory=list)
    malformed_objects: list[ObjObs] = field(default_factory=list)

    @property
    def n_received(self) -> int:
        return len(self.objects) + len(self.malformed_objects)


class CycleAssembler:
    def __init__(self) -> None:
        self._cur: Cycle | None = None
        self._n = 0
        self._next_frame_index = 0

    def push(self, frame: Frame) -> Cycle | None:
        """Feed one frame; returns the previous cycle when a header closes it."""
        fi = self._next_frame_index
        self._next_frame_index += 1
        done = None
        if frame.can_id == CAN_ID_HEADER:
            try:
                hdr = parse_header(frame.data)
            except MalformedFrame:
                self._ensure_cycle().other.append(OtherFrame(fi, frame, "SHORT_HEADER"))
                return None
            done = self._close()
            self._cur = Cycle(self._n, hdr, fi, frame.timestamp_ticks)
            self._n += 1
            return done
        cyc = self._ensure_cycle()
        if frame.can_id != CAN_ID_OBJECT:
            cyc.other.append(OtherFrame(fi, frame, "BAD_ID"))
            return None
        off = frame.timestamp_ticks - cyc.header_t if cyc.header_t is not None else None
        pos = cyc.n_received
        try:
            obj = decode_object(frame.data)
            cyc.objects.append(ObjObs(fi, frame.timestamp_ticks, frame.data, obj, off, pos))
        except MalformedFrame:
            cyc.malformed_objects.append(ObjObs(fi, frame.timestamp_ticks, frame.data, None, off, pos))
        return None

    def flush(self) -> Cycle | None:
        return self._close()

    def _ensure_cycle(self) -> Cycle:
        if self._cur is None:  # objects before any header: header-less leading cycle
            self._cur = Cycle(self._n, None, None, None)
            self._n += 1
        return self._cur

    def _close(self) -> Cycle | None:
        c, self._cur = self._cur, None
        return c


def iter_cycles(source):
    asm = CycleAssembler()
    for fr in source:
        c = asm.push(fr)
        if c is not None:
            yield c
    c = asm.flush()
    if c is not None:
        yield c
