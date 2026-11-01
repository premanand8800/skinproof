from datetime import date

import pytest

from conftest import jpeg
from skinproof import noise
from skinproof.store import Store
from skinproof.verdict import (IMPROVED, LIKELY_IMPROVED, LIKELY_WORSE, NO_PROVEN, WORSE,
                               compute_verdicts)


def test_noise_threshold_rule():
    cal = noise.build_calibration(real=[{"pore": 60}, {"pore": 66}, {"pore": 62}, {"pore": 60}, {"redness": 1}])
    # pore range 6, sd 2.87 -> 2sd 5.74, range 6 wins
    assert cal["concerns"]["pore"]["threshold"] == pytest.approx(6.0, abs=0.02)


def test_noise_floor_and_range():
    cal = noise.build_calibration(real=[{"a": 50, "b": 50}, {"a": 50.5, "b": 60}, {"a": 50, "b": 50}, {"a": 50, "b": 50}])
    assert cal["concerns"]["a"]["threshold"] == 3  # floor
    assert cal["concerns"]["b"]["threshold"] >= 10  # range


def test_noise_needs_four():
    assert noise.MIN_RETAKES == 4
    with pytest.raises(ValueError):
        noise.build_calibration(real=[{"a": 1}, {"a": 2}, {"a": 3}])
    cal = noise.build_calibration(real=[{"a": 1}, {"a": 2}, {"a": 3}, {"a": 2}])
    assert "fewer than 6" in cal["note"]
    six = noise.build_calibration(real=[{"a": i} for i in range(6)])
    assert six["note"] is None


def test_noise_takes_max_with_synthetic():
    cal = noise.build_calibration(real=[{"a": 50}, {"a": 51}, {"a": 50}, {"a": 50}],
                                  synthetic=[{"a": 50}, {"a": 58}, {"a": 50}])
    assert cal["source"] == "retakes+synthetic" and cal["concerns"]["a"]["threshold"] >= 8


def test_perturbations_no_network():
    import io
    from PIL import Image
    im = Image.effect_noise((300, 400), 60).convert("RGB")
    buf = io.BytesIO(); im.save(buf, "JPEG")
    out = noise.perturbations(buf.getvalue())
    names = [n for n, _ in out]
    assert len(out) == 8 and all(isinstance(b, bytes) and b for _, b in out)
    assert {"rotate+3", "rotate-3", "yaw-shift+4"} <= set(names)
    assert len({b for _, b in out}) == 8


def ck(d, **s):
    return {"id": d, "date": d, "scores": s}


T = {"pore": 4.0, "redness": 5.0}


def test_single_reading_is_likely_not_proven():
    v = compute_verdicts([ck("2026-01-01", pore=50, redness=50), ck("2026-02-01", pore=59, redness=52)], T)
    by = {c["concern"]: c for c in v["concerns"]}
    assert by["pore"]["verdict"] == LIKELY_IMPROVED and not by["pore"]["confirmed"]
    assert "needs confirmation" in by["pore"]["sentence"] and "Likely improved" in by["pore"]["sentence"]
    assert by["redness"]["verdict"] == NO_PROVEN and "No proven change yet" in by["redness"]["sentence"]
    assert v["concerns_tested"] == 2 and "2 concerns were tested" in v["multiple_comparison_note"]


def test_two_consecutive_readings_prove_it():
    cks = [ck("2026-01-01", pore=50), ck("2026-02-01", pore=59), ck("2026-02-15", pore=60)]
    c = compute_verdicts(cks, {"pore": 4.0})["concerns"][0]
    assert c["verdict"] == IMPROVED and c["confirmed"] and c["recent_deltas"] == [9, 10]
    assert "Proven" in c["sentence"]


def test_second_reading_inside_noise_stays_unproven():
    cks = [ck("2026-01-01", pore=50), ck("2026-02-01", pore=59), ck("2026-02-15", pore=52)]
    assert compute_verdicts(cks, {"pore": 4.0})["concerns"][0]["verdict"] == NO_PROVEN
    cks = [ck("2026-01-01", pore=50), ck("2026-02-01", pore=52), ck("2026-02-15", pore=59)]
    assert compute_verdicts(cks, {"pore": 4.0})["concerns"][0]["verdict"] == LIKELY_IMPROVED


def test_opposite_directions_do_not_confirm():
    cks = [ck("2026-01-01", pore=50), ck("2026-02-01", pore=60), ck("2026-02-15", pore=40)]
    assert compute_verdicts(cks, {"pore": 4.0})["concerns"][0]["verdict"] == LIKELY_WORSE


def test_four_checkins_use_median_baseline_and_last_two():
    cks = [ck("2026-01-01", pore=50), ck("2026-01-08", pore=52),
           ck("2026-02-01", pore=58), ck("2026-02-08", pore=70)]
    c = compute_verdicts(cks, {"pore": 4.0})["concerns"][0]
    assert c["baseline"] == 51 and c["latest"] == 70 and c["delta"] == 19 and c["verdict"] == IMPROVED


def test_exactly_threshold_is_not_proven():
    v = compute_verdicts([ck("2026-01-01", pore=50), ck("2026-02-01", pore=54)], {"pore": 4.0})
    assert v["concerns"][0]["verdict"] == NO_PROVEN


def test_worse_needs_confirmation():
    v = compute_verdicts([ck("2026-01-01", pore=60), ck("2026-02-01", pore=50)], {"pore": 4.0})
    assert v["concerns"][0]["verdict"] == LIKELY_WORSE
    v = compute_verdicts([ck("2026-01-01", pore=60), ck("2026-02-01", pore=50), ck("2026-02-08", pore=49)], {"pore": 4.0})
    assert v["concerns"][0]["verdict"] == WORSE


def test_needs_two_and_quality_gate():
    one = compute_verdicts([ck("2026-01-01", pore=60)], T)
    assert not one["ready"]
    bad = {"id": "x", "date": "2026-02-01", "error": {"code": "error_lighting_dark"}}
    v = compute_verdicts([ck("2026-01-01", pore=60), bad], T)
    assert not v["ready"] and v["skipped_checkins"] == 1


def test_no_overall_score_and_next_step():
    prod = {"start_date": "2026-01-01", "concerns": ["redness"]}
    v = compute_verdicts([ck("2026-01-01", pore=50, redness=50), ck("2026-03-05", pore=50, redness=51)], T, prod)
    assert not any(k in v for k in ("overall", "score"))
    red = next(c for c in v["concerns"] if c["concern"] == "redness")
    assert "Don't rebuy yet" in red["next"]
    early = compute_verdicts([ck("2026-01-01", redness=50), ck("2026-01-15", redness=51)], T, prod)
    assert "Too early" in early["concerns"][0]["next"]


def test_store_roundtrip(tmp_path):
    s = Store(tmp_path)
    p = s.create_profile("Ana")
    s.set_calibration(p["id"], {"concerns": {}})
    s.add_product(p["id"], "Serum", "2026-01-01", ["pore"])
    s.add_checkin(p["id"], {"date": "2026-01-02", "scores": {"pore": 5}})
    rel = s.save_photo(p["id"], "checkin", b"abc")
    s2 = Store(tmp_path)
    got = s2.get_profile(p["id"])
    assert got["name"] == "Ana" and got["products"][0]["name"] == "Serum"
    assert got["checkins"][0]["scores"] == {"pore": 5} and (tmp_path / rel).read_bytes() == b"abc"
    with pytest.raises(KeyError):
        s2.add_product("nope", "x", "2026-01-01", [])
