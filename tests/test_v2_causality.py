"""Causality and leakage properties of the profile-v2 detector on real recordings.

* prefix invariance: verdicts of a cycle never depend on frames that arrive later;
* fresh session: a new Detector carries no state from another instance;
* frames carry no side channel: attacked frames rebuilt from (id, bytes, time) score identically;
* the numeric slot ID is a link key only: a consistent relabelling of slots inside the observed range
  leaves every reason, alert and evidence record unchanged;
* evidence never cites a cycle later than the one being reported.
"""

from __future__ import annotations

import dataclasses

import pytest

from conftest import needs_data
from phantomguard.config import load_config, raw_path
from phantomguard.detect.autoencoder import collection_config
from phantomguard.detect.pipeline import Detector
from phantomguard.detect.replay_fp import build_library
from phantomguard.eval.splits import Segment
from phantomguard.frames import CAN_ID_OBJECT, Frame
from phantomguard.io.replay import ReplaySource
from phantomguard.stats.baseline import collect, derive_thresholds, track_features
from phantomguard.stats.v2 import apply_v2

pytestmark = needs_data
CFG = load_config()
CHAOTIC = "multiplePeopleChaotic.csv"
TEST = (3400, 3800)          # a busy stretch of the chaotic recording, disjoint from the fitting prefix


@pytest.fixture(scope="module")
def v2():
    segs = [Segment(f, 0, 900) for f in CFG["data"]["files"]]
    st = collect(collection_config(CFG, {"reassign_jump": CFG["tracks"]["reassign_jump_default"]}), segs)
    base = derive_thresholds(st, track_features(st, CFG), CFG)
    apply_v2(CFG, base, st)
    base["rule_thresholds"] = {"value": {"RCS_ENV": 1.0, "DRIFT": 8.0, "DRIFT_STATIC": 32.0, "REPLAY": 2.0,
                                         "SPEED": 1.0, "SLOT_RANGE": 1.0, "ACCEL": 10.0, "RR_RESID": 1.0,
                                         "POS_SPEED": 4.0, "RCS_STD": 0.5, "COLOC": 0.1, "ARRIVAL_POS": 0.0}}
    return base, build_library(CFG, list(st.tracks.values()))


def frames(lo=TEST[0], hi=TEST[1]):
    return list(ReplaySource(raw_path(CFG, CHAOTIC), (lo, hi)))


def signature(results, upto=None):
    out = []
    for r in results[:upto]:
        out.append((r.index, tuple(r.cycle_reasons), r.cycle_alert,
                    tuple((v.frame_index, v.track_id, tuple(v.reasons), v.alert,
                           tuple(sorted((e["reason"], e["status"], tuple(e["frames"])) for e in v.evidence)))
                          for v in r.objects)))
    return out


def detect(base_lib, stream):
    base, lib = base_lib
    return list(Detector(CFG, base, None, lib).run(stream))


def test_prefix_invariance(v2):
    long = detect(v2, frames())
    short = detect(v2, frames(TEST[0], TEST[0] + 250))
    n = len(short) - 1                     # the final cycle of the short stream is closed by end-of-stream
    assert n > 200 and signature(short, n) == signature(long, n)
    assert any(v.reasons for r in long for v in r.objects), "fixture should exercise some reasons"


def test_fresh_session_has_no_shared_state(v2):
    first = detect(v2, frames())
    detect(v2, frames(TEST[1], TEST[1] + 300))           # another session in between
    assert signature(detect(v2, frames())) == signature(first)


def test_frames_have_no_side_channel(v2):
    from phantomguard.eval.attack_adapter import attack_source

    stream = list(attack_source(iter(frames()), CFG, v2[0], attack_type="T1", level="A3", seed=11,
                                train_segments=[Segment(CHAOTIC, 0, 900)], motion_case="moving"))
    assert {f.name for f in dataclasses.fields(Frame)} == {"can_id", "data", "timestamp_ticks"}
    rebuilt = [Frame(int(f.can_id), bytes(f.data), int(f.timestamp_ticks)) for f in stream]
    assert signature(detect(v2, stream)) == signature(detect(v2, rebuilt))


def test_slot_number_is_only_a_link_key(v2):
    original = frames()
    used = sorted({f.data[0] for f in original if f.can_id == CAN_ID_OBJECT})
    perm = dict(zip(used, reversed(used)))                # bijection inside the observed slot range
    relabelled = [Frame(f.can_id, bytes([perm[f.data[0]]]) + f.data[1:], f.timestamp_ticks)
                  if f.can_id == CAN_ID_OBJECT else f for f in original]
    a, b = detect(v2, original), detect(v2, relabelled)
    assert signature(a) == signature(b)


def test_evidence_never_cites_the_future(v2):
    for r in detect(v2, frames()):
        for v in r.objects:
            for e in v.evidence:
                if e.get("cycles"):
                    assert e["cycles"][-1] <= r.index
                assert all(fi <= max(x.frame_index for x in r.frames) for fi in e["frames"])


def test_unknown_contract_fails_clearly(v2):
    import copy

    base = copy.deepcopy(v2[0])
    base["detector_contract"]["value"]["schema"] = 99
    with pytest.raises(ValueError, match="not supported by this code"):
        Detector(CFG, base, None, v2[1])
    base["detector_contract"]["value"].update(profile="v9", schema=1)
    with pytest.raises(ValueError, match="not supported by this code"):
        Detector(CFG, base, None, v2[1])


def test_every_reason_has_an_evidence_class():
    from phantomguard.detect.common import REASONS
    from phantomguard.detect.evidence import REASON_CLASS

    assert set(REASONS) <= set(REASON_CLASS) and set(REASON_CLASS) <= set(REASONS)
