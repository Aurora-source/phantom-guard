import csv

import pytest

from phantomguard.commands.compare_profiles import PAIR, compare, markdown
from phantomguard.commands.rule_audit import cp_upper


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _eval_dir(tmp_path, name, identified, forged="5"):
    d = tmp_path / name
    d.mkdir()
    base = {k: "x" for k in PAIR}
    inst = []
    for i, ident in enumerate(identified):
        inst.append({**base, "attack_id": str(i), "attack_type": "T1", "level": "A3", "layers": "all",
                     "identified": str(ident), "detected": str(ident), "right_censored": "False",
                     "forged_object_frames": forged, "forged_object_frames_loc": forged,
                     "forged_frames_alerting": "2" if ident else "0", "window_real_excess_alert_frames": "1",
                     "paired_status": "exact" if ident else "none"})
        inst.append({**inst[-1], "layers": "all-minus-COLOC", "identified": "False"})
    _write(d / "attack_eval_instances.csv", inst)
    _write(d / "attack_eval_runs.csv", [{"layers": "all"}])
    _write(d / "attack_eval_clean.csv", [{"split": "timeblock", "fold": "timeblock", "file": "f.csv", "layers": "all",
                                          "status": "ok", "alert_events": "1", "minutes_exact": "2.0", "minutes": "2.0"}])
    return d


def test_compare_pairs_instances_and_lists_regressions(tmp_path):
    before = _eval_dir(tmp_path, "b", [True, True, False])
    after = _eval_dir(tmp_path, "a", [True, False, True])
    res = compare(before, after)
    row = res["rows"][0]
    assert (row["before_identified"], row["after_identified"], row["gained_identified"], row["lost_identified"]) == (2, 2, 1, 1)
    assert len(res["regressions"]) == 1 and res["regressions"][0]["attack_id"] == "1"
    assert res["ablations"]["after"]["all-minus-COLOC"]["T1|A3"] == 0
    assert "| T1 | A3 | 3 |" in markdown(res, "t", ("legacy", "v2"))


def test_compare_refuses_different_attacker_streams(tmp_path):
    before = _eval_dir(tmp_path, "b", [True])
    after = _eval_dir(tmp_path, "a", [True], forged="6")
    with pytest.raises(SystemExit, match="attacker streams differ"):
        compare(before, after)


def test_clopper_pearson_zero_events():
    n = 442724
    assert cp_upper(0, n) == pytest.approx(1 - 0.05 ** (1 / n), rel=1e-6)
    assert cp_upper(5, 10) < 1 and cp_upper(0, 0) == 1.0
