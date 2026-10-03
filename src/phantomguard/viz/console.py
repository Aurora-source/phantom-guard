"""Replay viewer: top-down scene with detector verdicts (matplotlib).

Green = authentic, orange = flagged but not (yet) alerting, red = alerting (with reason codes),
grey = outside the ROI (protocol-checked only). Arrows are reported velocity vectors. A running
alert log sits on the right. Coordinates: lateral y to the right, longitudinal x up (sensor at 0, 0).
"""

from __future__ import annotations

from collections import deque

import matplotlib.pyplot as plt
from matplotlib.patches import Circle

from phantomguard.detect.common import CycleResult

COLORS = {"ok": "#2e9d4f", "flag": "#e69500", "alert": "#d62728", "out": "#9a9a9a"}


class ConsoleView:
    def __init__(self, roi: float, tick_seconds: float, view_range: float | None = None, log_lines: int = 14):
        self.roi = roi
        self.tick = tick_seconds
        self.lim = view_range or roi + 1.0
        self.log: deque = deque(maxlen=log_lines)
        self.fig = plt.figure(figsize=(12, 5.4))
        self.ax = self.fig.add_axes([0.05, 0.1, 0.6, 0.82])
        self.axlog = self.fig.add_axes([0.67, 0.1, 0.32, 0.82])
        self.t0 = None

    def _status(self, v) -> str:
        if not v.in_roi:
            return "out"
        return "alert" if v.alert else ("flag" if v.flagged else "ok")

    def render(self, res: CycleResult, title: str = "") -> None:
        ax = self.ax
        ax.clear()
        ax.set_xlim(-self.lim, self.lim)
        ax.set_ylim(-0.5, self.lim)
        ax.set_aspect("equal")
        ax.add_patch(Circle((0, 0), self.roi, fill=False, ls="--", lw=0.8, color="#555555"))
        ax.plot([0], [0], marker="^", color="black", ms=9)
        ax.set_xlabel("y (lateral, units)")
        ax.set_ylabel("x (longitudinal, units)")
        if self.t0 is None and res.header_t is not None:
            self.t0 = res.header_t
        t = (res.header_t - self.t0) * self.tick if res.header_t is not None and self.t0 is not None else 0.0
        n_alert = 0
        for v in res.objects:
            if v.x is None:
                continue
            st = self._status(v)
            c = COLORS[st]
            ax.scatter([v.y], [v.x], s=40 if st != "out" else 12, color=c, zorder=3)
            if (v.vx or v.vy) and st != "out":
                ax.arrow(v.y, v.x, 0.35 * v.vy, 0.35 * v.vx, color=c, width=0.03, head_width=0.18,
                         length_includes_head=True, zorder=2)
            if st in ("alert", "flag") and v.reasons:
                ax.annotate(",".join(v.reasons[:3]), (v.y, v.x), xytext=(5, 5), textcoords="offset points",
                            fontsize=7, color=c)
            if st == "alert":
                n_alert += 1
                self.log.append(f"{t:7.2f}s cyc {res.index:5d} slot 0x{v.slot:02x}: {','.join(v.reasons)}")
        for r in res.cycle_reasons:
            self.log.append(f"{t:7.2f}s cyc {res.index:5d} CYCLE: {r}")
        ax.set_title(f"{title}  t={t:6.2f}s  cycle {res.index}  objects {len(res.objects)}  alerting {n_alert}",
                     fontsize=9)
        al = self.axlog
        al.clear()
        al.axis("off")
        al.set_title("alert log", fontsize=9, loc="left")
        al.text(0, 1, "\n".join(self.log) or "(none)", va="top", family="monospace", fontsize=7.5,
                transform=al.transAxes)
        handles = [plt.Line2D([], [], marker="o", ls="", color=COLORS[k], label=lab) for k, lab in
                   (("ok", "authentic"), ("flag", "flagged"), ("alert", "alert"), ("out", "outside ROI"))]
        ax.legend(handles=handles, loc="upper right", fontsize=7)
