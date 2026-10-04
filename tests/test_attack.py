from __future__ import annotations

import numpy as np
import pytest
from copy import deepcopy

from conftest import needs_data
from phantomguard.attack.injector import MixedSource, label_map
from phantomguard.attack.levels import LEVELS
from phantomguard.attack.pools import build_pools
from phantomguard.attack.scenarios import make_context, plan_run
from phantomguard.config import BASELINE_PATH, bval, load_baseline, load_config, raw_path
from phantomguard.detect.common import layer_of
from phantomguard.detect.pipeline import Detector
from phantomguard.eval.splits import time_block_segments
from phantomguard.frames import CAN_ID_OBJECT, decode_object
from phantomguard.io.replay import ReplaySource

pytestmark = [pytest.mark.skipif(not BASELINE_PATH.exists(), reason="run scripts/learn_baseline.py first"), needs_data]
CFG = load_config()


def build(atype, level, file="multiplePeopleChaotic.csv", part="test", seed=11, cfg=None):
    cfg = cfg or CFG
    b = load_baseline()
    segs = time_block_segments(cfg)
    seg = next(s for s in segs[part] if s.file == file)
    known = build_pools(cfg, segs["train"])
    unseen = build_pools(cfg, [s for s in segs["val"] if s.file != file])
    ctx = make_context(cfg, b, level, np.random.default_rng(seed), known, unseen)
    base = ReplaySource(raw_path(cfg, file), (seg.lo, seg.hi))
    insts = plan_run(ctx, atype, base.cycles, seg.lo, seg.hi)
    src = MixedSource(base, ctx, insts, tag=f"{atype}-{level}")
    frames = list(src)
    return cfg, b, src, frames, label_map(src)


def fab_object_reasons(cfg, b, frames, lm):
    """Run the detector; return (per-fab-object reasons, cycle reasons touching fab cycles)."""
    det = Detector(cfg, b)
    fab_obj, fab_cycle = [], []
    fab_frame_idx = set(lm)
    for cyc in det.run(iter(frames)):
        has_fab = any(v.frame_index in fab_frame_idx for v in cyc.objects)
        if has_fab:
            fab_cycle.append(set(cyc.cycle_reasons))
        for v in cyc.objects:
            if v.frame_index in fab_frame_idx:
                fab_obj.append(set(v.reasons))
    return fab_obj, fab_cycle


def test_a0_is_caught_by_protocol():
    cfg, b, src, frames, lm = build("T1", "A0")
    assert lm, "A0 produced no fabricated frames"
    fab_obj, fab_cycle = fab_object_reasons(cfg, b, frames, lm)
    flagged = sum(1 for r in fab_obj if any(layer_of(c) == "protocol" for c in r)) + sum(len(c) for c in fab_cycle)
    assert flagged > 0


@pytest.mark.parametrize("level", ["A2", "A3", "A4"])
def test_a2plus_pass_claude_protocol_checks(level):
    """A2+ keep header count, counter, cadence, slot uniqueness, fields and arrival valid (CLAUDE.md A2)."""
    cfg, b, src, frames, lm = build("T1", level)
    fab_obj, fab_cycle = fab_object_reasons(cfg, b, frames, lm)
    forbidden_obj = {"DUP_SLOT", "SLOT_RANGE", "FIXED_FIELD", "ARRIVAL", "FRAME_LEN"}
    # CLAUDE.md's A2 contract: header==actual, counter, cadence, status, slot uniqueness, valid fields.
    # COUNT_RANGE/RANGE_ORDER/BURST_GAP are detector checks beyond that contract (see decisions.md) and
    # are allowed to fire on A2; A3+ is tested separately for order/burst below.
    forbidden_cycle = {"COUNT_MISMATCH", "COUNTER", "CADENCE", "STATUS", "NO_HEADER"}
    bad_obj = set().union(*fab_obj) & forbidden_obj if fab_obj else set()
    bad_cycle = set().union(*fab_cycle) & forbidden_cycle if fab_cycle else set()
    assert not bad_obj, bad_obj
    assert not bad_cycle, bad_cycle


@pytest.mark.parametrize("level", ["A3", "A4"])
def test_a3plus_also_pass_order_and_burst(level):
    """A3+ replicate the range-sorted, back-to-back burst, so order/timing checks also pass."""
    cfg, b, src, frames, lm = build("T1", level)
    fab_obj, _ = fab_object_reasons(cfg, b, frames, lm)
    bad = set().union(*fab_obj) & {"RANGE_ORDER", "BURST_GAP"} if fab_obj else set()
    assert not bad, bad


def test_fabricated_values_lie_in_real_ranges():
    b = load_baseline()
    slot_max, rcs_lo, rcs_hi = bval(b, "slot_max"), bval(b, "rcs_lo"), bval(b, "rcs_hi")
    alo, ahi = bval(b, "arrival_lo"), bval(b, "arrival_hi")
    roi = CFG["roi"]["max_range"]
    for level in ("A1", "A3", "A4"):
        cfg, bb, src, frames, lm = build("T1", level)
        # rebuild per-frame index to read fabricated object frames
        fi = -1
        for fr in frames:
            fi += 1
            if fi not in lm:
                continue
            o = decode_object(fr.data)
            assert 0 <= o.slot <= slot_max, (level, o.slot)
            assert rcs_lo <= o.rcs <= rcs_hi and o.rcs == round(o.rcs), (level, o.rcs)
            assert o.range <= roi + 0.3, (level, o.range)


def test_labels_are_side_output_only_and_deterministic():
    # frames carry no label information; labels only index fabricated object frames
    # Determinism is tested inside available slot capacity; exhaustion is a separate outcome.
    test_cfg = deepcopy(CFG)
    test_cfg["attack"]["T2"]["count"] = [10, 10]
    test_cfg["attack"]["instances_per_run"] = 1
    cfg, b, src, frames, lm = build("T2", "A2", seed=7, cfg=test_cfg)
    n_obj = sum(1 for f in frames if f.can_id == CAN_ID_OBJECT)
    assert all(0 <= k < len(frames) for k in lm)
    assert all(frames[k].can_id == CAN_ID_OBJECT for k in lm)
    assert 0 < len(lm) <= n_obj
    # same seed -> identical labels
    _, _, _, _, lm2 = build("T2", "A2", seed=7, cfg=test_cfg)
    assert sorted(lm) == sorted(lm2)


def test_header_count_fixed_only_for_a2plus():
    # A1 keeps the real header count (so added objects cause a mismatch); A2 fixes it
    for level, expect_mismatch in (("A1", True), ("A2", False)):
        cfg, b, src, frames, lm = build("T1", level)
        _, fab_cycle = fab_object_reasons(cfg, b, frames, lm)
        has_mismatch = any("COUNT_MISMATCH" in c for c in fab_cycle)
        assert has_mismatch == expect_mismatch, (level, has_mismatch)


def test_attack_eval_seed_is_stable_across_processes():
    import os
    import subprocess
    import sys

    from phantomguard.config import REPO_ROOT

    code = ("import sys; sys.path.insert(0, 'scripts'); import run_attack_eval as r; "
            "print(r.job_seed(11, 'multiplePeopleChaotic.csv'))")
    outs = set()
    for hs in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hs}
        outs.add(subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True,
                                text=True, check=True).stdout.strip())
    assert len(outs) == 1, outs
