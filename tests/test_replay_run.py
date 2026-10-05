import math
from collections import deque

from phantomguard.config import load_config
from phantomguard.detect.replay_fp import ReplayChecker, track_fingerprints
from phantomguard.tracks import Track, TrackPoint

CFG = load_config()


def _path(n, start=0, x0=3.0):
    """A wiggly moving path: distinct displacement symbols so windows are fingerprinted."""
    pts, x, y = [], x0, 1.0
    for i in range(n):
        vx = 1.0 + 0.5 * math.sin(i * 0.9)
        vy = 0.75 * math.cos(i * 1.3)
        x, y = x + vx * 0.0332 * 6, y + vy * 0.0332 * 6
        pts.append(TrackPoint(start + i, (start + i) * 0.0332, round(x, 1), round(y, 1), vx, vy, 18.0,
                              math.hypot(x, y), vx, start + i))
    return pts


def _feed(checker, tid, pts):
    tr = Track(tid, 1, pts[0].cycle_index, False, deque(maxlen=64))
    runs = []
    for p in pts:
        tr.points.append(p)
        hit, _ = checker.check(tr, p.cycle_index)
        runs.append(checker.run)
        assert (checker.run > 0) == hit
    return runs


def test_run_counts_consecutive_matched_windows_and_resets():
    k = CFG["replay"]["k_gram"]
    src = _path(40)
    lib = set()
    for _, tfp, rfp in track_fingerprints(src, k, CFG["replay"]["disp_quant"], CFG["replay"]["vel_quant"],
                                          CFG["replay"]["min_complexity"], CFG["motion"]["moving_threshold_mps"]):
        lib |= {tfp, rfp}
    assert lib, "synthetic path must be fingerprinted"
    checker = ReplayChecker(CFG, lib)
    copy = [TrackPoint(1000 + i, p.t_s + 100, p.x, p.y, p.vx, p.vy, p.rcs, p.rng, p.vr, 5000 + i)
            for i, p in enumerate(src)]
    runs = _feed(checker, 7, copy)
    hits = [r for r in runs if r]
    assert hits == list(range(1, len(hits) + 1))          # strictly consecutive count
    assert len(hits) >= 20
    gapped = ReplayChecker(CFG, lib)
    runs = _feed(gapped, 8, copy[:25] + copy[26:])           # one missing cycle breaks the run
    before, after = runs[:25], runs[25:]
    assert max(before) >= 10 and after[after.index(next(r for r in after if r))] == 1
