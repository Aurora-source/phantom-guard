"""Association fixtures: quantised holds, gap bridging, reassignment, near-gate ambiguity, predictive gate,
crossing, duplicate slots, ROI boundary and timestamp reset. The slot is only a link key."""

from phantomguard.cycles import Cycle, ObjObs
from phantomguard.frames import RadarObject
from phantomguard.tracks import TrackManager

TICK = 1e-4
PERIOD = 332


def cyc(i, objs, t=None):
    """objs: list of (slot, x, y, vx, vy)."""
    t0 = (i * PERIOD if t is None else t)
    obs = [ObjObs(1000 * i + k, t0 + 3 + 2 * k, b"", RadarObject(s, x, y, vx, vy, 0, 18.0), 3 + 2 * k, k)
           for k, (s, x, y, vx, vy) in enumerate(objs)]
    return Cycle(i, None, None, t0, obs)


def run(manager, cycles):
    return [[(tr.track_id, tr.assoc) for _, tr in manager.update(c)] for c in cycles]


def tm(**kw):
    return TrackManager(1.0, TICK, 0.3, max_gap_cycles=1, **kw)


def test_quantised_hold_and_gap_bridge():
    out = run(tm(), [cyc(0, [(1, 5.0, 1.0, 0, 0)]), cyc(1, [(1, 5.0, 1.0, 0, 0)]), cyc(2, []),
                     cyc(3, [(1, 5.2, 1.0, 0, 0)]), cyc(4, []), cyc(5, []), cyc(6, [(1, 5.2, 1.0, 0, 0)])])
    assert out[0] == [(0, "born")] and out[1] == [(0, "continued")] and out[3] == [(0, "gap_bridged")]
    assert out[6] == [(1, "born")]                                   # two missing cycles exceed max_gap


def test_reassignment_and_near_gate_ambiguity_keep_lineage():
    m = tm()
    out = run(m, [cyc(0, [(2, 5.0, 1.0, 0, 0)]), cyc(1, [(2, 5.9, 1.0, 0, 0)]), cyc(2, [(2, 7.0, 1.0, 0, 0)]),
                  cyc(3, [(2, 12.0, 1.0, 0, 0)])])
    assert out[1] == [(0, "ambiguous_continued")]
    assert out[2] == [(1, "ambiguous_reset")]
    assert out[3] == [(2, "born_by_jump")]
    tr = m.active[2]
    assert tr.born_by_jump and tr.predecessor == 1 and tr.gate_distance == 5.0


def test_predictive_gate_only_keeps_links_and_matches_plain_gate_otherwise():
    # 20 m/s mover across one dropped cycle reports 1.2 of travel (predicted 1.33): plain gate cuts, predictive keeps
    frames = [cyc(0, [(3, 5.0, 1.0, 20.0, 0)]), cyc(1, []), cyc(2, [(3, 6.2, 1.0, 20.0, 0)])]
    assert run(tm(), frames)[2] == [(1, "ambiguous_reset")]
    assert run(tm(predictive_gate=True), frames)[2] == [(0, "gap_bridged")]
    slow = [cyc(0, [(4, 5.0, 1.0, 0.5, 0)]), cyc(1, [(4, 5.2, 1.0, 0.5, 0)])]
    assert run(tm(), slow) == run(tm(predictive_gate=True), slow)


def test_crossing_tracks_follow_their_slots_and_duplicates_split():
    a = [(5, 4.0 + 0.2 * i, 1.0, 1.0, 0) for i in range(4)]
    b = [(6, 4.6 - 0.2 * i, 1.0, -1.0, 0) for i in range(4)]
    out = run(tm(), [cyc(i, [a[i], b[i]]) for i in range(4)])
    assert all(row == [(0, s0), (1, s1)] for row, (s0, s1) in
               zip(out, [("born", "born")] + [("continued", "continued")] * 3))
    dup = run(tm(), [cyc(0, [(7, 3.0, 1.0, 0, 0)]), cyc(1, [(7, 3.0, 1.0, 0, 0), (7, 3.0, 1.2, 0, 0)])])
    assert dup[1] == [(0, "continued"), (1, "duplicate_slot")]


def test_roi_boundary_does_not_affect_association_and_timestamp_reset_is_safe():
    # Range 14.9 -> 15.1 crosses the default ROI; linking is geometric only.
    out = run(tm(predictive_gate=True), [cyc(0, [(8, 14.9, 0.0, 0.3, 0)]), cyc(1, [(8, 15.1, 0.0, 0.3, 0)]),
                                         cyc(2, [(8, 15.2, 0.0, 0.3, 0)], t=5)])     # clock went backwards
    assert [r[0] for r in out] == [(0, "born"), (0, "continued"), (0, "continued")]
