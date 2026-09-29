"""What changed between two runs of the same schedule - the rules behind scheduled-run alerts."""
from qa import run_diff as rd


def rec(result, alt="", reason="", **extra):
    return {"result": result, "alt_text": alt, "reason": reason, "destination_raw": "https://ajio.com/s/x-1", **extra}


def test_a_banner_that_was_passing_and_now_fails_is_a_new_failure():
    d = rd.diff_runs({"a": rec("PASS")}, {"a": rec("FAIL", "Levis banner", "missing brands ['Levis']")})
    assert [e["banner_id"] for e in d["newly_failing"]] == ["a"]
    e = d["newly_failing"][0]
    assert (e["was"], e["now"], e["alt_text"]) == ("PASS", "FAIL", "Levis banner")


def test_a_banner_that_fails_in_both_runs_is_still_failing_not_new():
    d = rd.diff_runs({"a": rec("FAIL")}, {"a": rec("FAIL")})
    assert d["newly_failing"] == [] and d["still_failing"] == 1
    assert not rd.is_notable(d, "new_fails") and not rd.is_notable(d, "any_change")


def test_a_brand_new_banner_that_fails_counts_as_a_new_failure_and_a_new_banner():
    d = rd.diff_runs({}, {"a": rec("FAIL")})
    assert len(d["newly_failing"]) == 1 and len(d["new_banners"]) == 1
    assert d["newly_failing"][0]["was"] is None


def test_inconclusive_and_recovery_are_tracked_separately_from_failures():
    d = rd.diff_runs({"a": rec("PASS"), "b": rec("FAIL"), "c": rec("INCONCLUSIVE")},
                     {"a": rec("INCONCLUSIVE"), "b": rec("PASS"), "c": rec("PASS")})
    assert [e["banner_id"] for e in d["newly_inconclusive"]] == ["a"]
    assert sorted(e["banner_id"] for e in d["recovered"]) == ["b", "c"]
    assert d["newly_failing"] == []


def test_banners_appearing_and_leaving_the_feed_are_reported():
    d = rd.diff_runs({"gone": rec("PASS")}, {"fresh": rec("PASS")})
    assert [e["banner_id"] for e in d["new_banners"]] == ["fresh"]
    assert [e["banner_id"] for e in d["removed_banners"]] == ["gone"]


def test_a_banner_with_no_result_is_ignored_on_both_sides():
    d = rd.diff_runs({"a": None, "b": {"banner_id": "b"}}, {"a": rec("PASS"), "c": None})
    assert [e["banner_id"] for e in d["new_banners"]] == ["a"] and d["removed_banners"] == []


def test_new_fails_mode_only_alerts_on_a_new_failure():
    quiet = rd.diff_runs({"a": rec("PASS")}, {"a": rec("INCONCLUSIVE"), "n": rec("PASS")})
    assert not rd.is_notable(quiet, "new_fails")
    assert rd.is_notable(quiet, "any_change")
    loud = rd.diff_runs({"a": rec("PASS")}, {"a": rec("FAIL")})
    assert rd.is_notable(loud, "new_fails") and rd.is_notable(loud, "any_change")


def test_off_mode_never_alerts_and_no_change_never_alerts():
    loud = rd.diff_runs({"a": rec("PASS")}, {"a": rec("FAIL")})
    assert not rd.is_notable(loud, "off")
    same = rd.diff_runs({"a": rec("PASS")}, {"a": rec("PASS")})
    assert not rd.is_notable(same, "any_change") and rd.summarize(same) == "no change"


def test_a_baseline_never_alerts_even_when_everything_fails():
    b = rd.baseline("first completed run of this schedule")
    assert not rd.is_notable(b, "any_change") and rd.summarize(b).startswith("baseline")


def test_summary_and_alert_text_lead_with_the_failing_banners():
    d = rd.diff_runs({"a": rec("PASS"), "b": rec("PASS")},
                     {"a": rec("FAIL", "Levis", "missing brands ['Levis']"), "b": rec("INCONCLUSIVE", "Puma", "vision_failed")})
    assert rd.summarize(d) == "1 new FAIL, 1 new INCONCLUSIVE"
    title, body = rd.alert_text(d, "Hourly hero", "new_fails")
    assert title == "Hourly hero: 1 new FAIL, 1 new INCONCLUSIVE"
    assert body == "FAIL - Levis: missing brands ['Levis']"          # inconclusive not listed in new_fails mode
    _, body_all = rd.alert_text(d, "Hourly hero", "any_change")
    assert body_all.splitlines() == ["FAIL - Levis: missing brands ['Levis']", "INCONCLUSIVE - Puma: vision_failed"]


def test_alert_text_caps_the_lines_and_a_long_reason_is_truncated():
    cur = {f"b{i}": rec("FAIL", f"Banner {i}", "x" * 500) for i in range(7)}
    d = rd.diff_runs({}, cur)
    assert all(len(e["reason"]) <= 200 for e in d["newly_failing"])
    _, body = rd.alert_text(d, "s", "new_fails", max_lines=3)
    assert body.splitlines()[-1] == "+4 more" and len(body.splitlines()) == 4


def test_a_new_failing_banner_is_not_listed_twice_in_any_change_text():
    d = rd.diff_runs({}, {"a": rec("FAIL", "Levis")})
    _, body = rd.alert_text(d, "s", "any_change")
    assert body.splitlines() == ["FAIL - Levis"]


def test_a_banner_that_became_unavailable_is_not_a_finding_but_is_counted():
    d = rd.diff_runs({"a": rec("PASS"), "b": rec("FAIL")}, {"a": rec("UNAVAILABLE"), "b": rec("UNAVAILABLE")})
    assert d["newly_failing"] == [] and d["newly_inconclusive"] == [] and d["recovered"] == []
    assert d["counts"]["unavailable"] == 2 and rd.summarize(d) == "2 couldn't be checked"
    assert not rd.is_notable(d, "new_fails") and not rd.is_notable(d, "any_change")     # an outage never raises an alert


def test_an_unavailable_banner_that_now_fails_is_a_new_failure():
    d = rd.diff_runs({"a": rec("UNAVAILABLE")}, {"a": rec("FAIL")})
    assert len(d["newly_failing"]) == 1 and rd.is_notable(d, "new_fails")


def test_a_baseline_has_a_zero_unavailable_count():
    assert rd.baseline("first run")["counts"]["unavailable"] == 0
