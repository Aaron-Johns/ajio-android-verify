"""The reference check (qa/reference_check.py): a banner judged against what a human says it should lead to, and how that
plugs into a run (qa/feed_verify.py) and the fill-in template (qa/reference_template.py)."""
import csv
import io
import json

import pytest
from PIL import Image as PILImage

from qa import feed_verify as fv
from qa import listing_client as lc
from qa import reference_check as rc
from qa import reference_template as rt
from qa.compare import AliasMap
from qa.feed_client import Banner
from qa.reference import ReferenceRow, load_reference

ALIASES = AliasMap([["LEVI'S", "LEVIS"]])
INFO = {"brands_mentioned": ["new balance", "under armour"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}


def listing(title="Min 40 Percent Off", brands=None):
    return lc.Listing("slug", title, 1000, 0, {"NEW BALANCE": 5, "Under Armour": 9} if brands is None else brands, {}, {"Brands": 2}, [])


def row(**kw):
    return ReferenceRow(banner_id="a", **kw)


def check(r, dest="https://ajio.com/s/a-1", lst=None, exc=None):
    def get():
        if exc:
            raise exc
        return lst or listing()
    return rc.check(r, dest, get, ALIASES)


# ---- the rules ----

def test_a_row_with_no_expectations_gives_no_check():
    assert rc.check(ReferenceRow("a", notes="just a note"), "https://ajio.com/s/a-1", lambda: listing(), ALIASES) is None


def test_expected_brands_must_all_be_on_the_listing_alias_aware():
    ok = check(row(expected_brand="Under Armour|NEW BALANCE"))
    assert ok["status"] == "MATCH" and ok["problems"] == [] and ok["reason"] == ""
    alias = check(row(expected_brand="Levis"), lst=listing(brands={"LEVI'S": 3}))
    assert alias["status"] == "MATCH"                                   # LEVIS = LEVI'S
    bad = check(row(expected_brand="Puma|Under Armour"))
    assert bad["status"] == "MISMATCH" and "Puma" in bad["problems"][0] and "Under Armour" not in bad["problems"][0]
    assert bad["reason"].startswith("reference: expected brand not on the listing: Puma")


def test_several_missing_brands_are_all_named():
    bad = check(row(expected_brand="Puma|Nike"))
    assert "brands not on the listing: Puma, Nike" in bad["reason"]


def test_expected_category_matches_the_listing_title_either_way_round():
    assert check(row(expected_category="Clearance Store"), lst=listing(title="Clearance Store"))["status"] == "MATCH"
    assert check(row(expected_category="clearance"), lst=listing(title="Clearance Store"))["status"] == "MATCH"
    assert check(row(expected_category="Men's Clearance Store sale"), lst=listing(title="Clearance Store"))["status"] == "MATCH"
    bad = check(row(expected_category="Sarees"), lst=listing(title="Clearance Store"))
    assert bad["status"] == "MISMATCH" and "Sarees" in bad["reason"] and "Clearance Store" in bad["reason"]


def test_expected_link_type_uses_the_plp_family_and_is_checked_without_the_listing():
    def boom():
        raise AssertionError("the listing is not needed for a type-only expectation")
    assert rc.check(row(expected_deeplink_type="PLP"), "https://www.ajio.com/s/x-1", boom, ALIASES)["status"] == "MATCH"
    assert rc.check(row(expected_deeplink_type="PLP"), "https://www.ajio.com/c/x-1", boom, ALIASES)["status"] == "MATCH"   # /c/ is in the family
    bad = rc.check(row(expected_deeplink_type="CATEGORY"), "https://www.ajio.com/s/x-1", boom, ALIASES)
    assert bad["status"] == "MISMATCH" and "expected a CATEGORY link" in bad["reason"]


def test_a_banner_with_no_link_fails_a_type_expectation_and_cannot_be_checked_for_the_rest():
    bad = check(row(expected_deeplink_type="PLP"), dest=None)
    assert bad["status"] == "MISMATCH" and "no link" in bad["reason"]
    assert check(row(expected_brand="Puma"), dest="")["status"] == "MISMATCH"


def test_things_that_cannot_be_tested_are_unchecked_never_a_pass_or_a_fail():
    down = check(row(expected_brand="Puma"), exc=RuntimeError("timeout"))
    assert down["status"] == "UNCHECKED" and "listing not available" in down["unchecked"][0] and down["reason"] == ""
    no_brands = check(row(expected_brand="Puma"), lst=listing(brands={}))
    assert no_brands["status"] == "UNCHECKED"
    no_title = check(row(expected_category="Sarees"), lst=listing(title=""))
    assert no_title["status"] == "UNCHECKED"
    external = check(row(expected_brand="Puma"), dest="https://example.com/page")
    assert external["status"] == "UNCHECKED" and "not a listing" in external["unchecked"][0]


def test_a_mismatch_wins_over_an_unchecked_part_and_matching_parts_dont_hide_it():
    r = check(row(expected_brand="Puma", expected_category="Sarees"), lst=listing(title=""))
    assert r["status"] == "MISMATCH" and len(r["problems"]) == 1 and len(r["unchecked"]) == 1


def test_a_luxe_link_is_looked_up_in_the_luxe_store_by_the_pipeline_not_the_checker():
    r = check(row(expected_brand="Under Armour"), dest="https://luxe.ajio.com/s/a-1")
    assert r["status"] == "MATCH"


# ---- loading ----

def test_load_default_is_off_without_a_file_and_keeps_only_rows_with_expectations(tmp_path):
    assert rc.load_default(tmp_path / "missing.csv") == ({}, None)
    p = tmp_path / "ref.csv"
    p.write_text("banner_id,expected_brand,expected_category,expected_deeplink_type,notes\na,Puma,,,\nb,,,,only a note\n", encoding="utf-8")
    rows, path = rc.load_default(p)
    assert list(rows) == ["a"] and path == p


def test_a_broken_reference_file_turns_the_check_off_instead_of_stopping_a_run(tmp_path):
    p = tmp_path / "ref.csv"
    p.write_bytes(b"\xff\xfe\x00 not utf-8 at all \x80\x81")
    assert rc.load_default(p) == ({}, None)


def test_the_loader_survives_a_byte_order_mark_and_ignores_the_context_columns(tmp_path):
    p = tmp_path / "ref.csv"
    p.write_text("# comment first, after a BOM\nbanner_id,expected_brand,expected_category,expected_deeplink_type,notes,alt_text\n"
                 "a,Puma,,plp,,some banner\n", encoding="utf-8-sig")
    got = load_reference(p)
    assert got["a"].expected_brand == "Puma" and got["a"].expected_deeplink_type == "PLP"


# ---- inside a run ----

def banner(bid="a", dest="https://ajio.com/s/a-1", **kw):
    return Banner(bid, "https://cdn/x.webp", 0, dest, "hybrid-dynamic-banner", bid, 0, 0, hotspots=[], alt_text=bid, **kw)


class Fetcher:
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, slug, kind="curated", **k):
        self.calls.append(slug)
        return self.table[slug]


def real_image(url):
    buf = io.BytesIO()
    PILImage.new("RGB", (200, 100), "blue").save(buf, format="PNG")
    return "OK", buf.getvalue(), "image/png"


def run(banners, tmp_path, fetcher, reference, **kw):
    kw.setdefault("cache", None)
    return fv.run_feed_verify(banners, tmp_path, analyzer=lambda p: INFO, aliases=ALIASES, listing_fetcher=fetcher, image_fetcher=real_image,
                              workers=1, retry_rounds=0, retry_pause=0, reference=reference, **kw)


def test_a_banner_that_passes_on_its_artwork_fails_when_the_reference_says_otherwise(tmp_path):
    f = Fetcher({"a-1": listing()})
    plain = run([banner()], tmp_path / "p", f, None)[0]
    assert plain["result"] == "PASS" and "reference_check" not in plain
    got = run([banner()], tmp_path / "r", f, {"a": row(expected_brand="Puma")})[0]
    assert got["result"] == "FAIL" and got["reference_check"]["status"] == "MISMATCH"
    assert got["reason"].startswith("reference: expected brand not on the listing: Puma")


def test_a_matching_reference_leaves_the_result_alone_but_is_recorded(tmp_path):
    got = run([banner()], tmp_path, Fetcher({"a-1": listing()}), {"a": row(expected_brand="Under Armour")})[0]
    assert got["result"] == "PASS" and got["reference_check"]["status"] == "MATCH"


def test_an_existing_fail_keeps_its_own_reasons_and_gets_the_reference_finding_beside_them(tmp_path):
    f = Fetcher({"a-1": listing(brands={"NEW BALANCE": 5})})          # Under Armour (named on the artwork) is missing: a FAIL already
    got = run([banner()], tmp_path, f, {"a": row(expected_brand="Puma")})[0]
    assert got["result"] == "FAIL" and "missing brands" in got["reason"] and "reference:" not in got["reason"]   # its own reason is untouched
    assert got["reference_check"]["status"] == "MISMATCH"
    summary = fv.format_summary([got])
    assert "under armour" in summary.lower() and "reference: expected brand not on the listing: Puma" in summary       # both shown


def test_only_banners_with_a_row_are_checked_and_no_extra_listing_calls_are_made(tmp_path):
    f = Fetcher({"a-1": listing(), "b-2": listing()})
    got = run([banner("a"), banner("b", "https://ajio.com/s/b-2")], tmp_path, f, {"a": row(expected_brand="Under Armour")})
    assert "reference_check" in got[0] and "reference_check" not in got[1]
    assert sorted(f.calls) == ["a-1", "b-2"]                          # the listing cache served the reference check


def test_the_reference_is_applied_after_the_cache_so_an_edited_expectation_takes_effect(tmp_path):
    from qa.banner_cache import BannerCache
    cache = BannerCache(path=tmp_path / "c.json")
    f = Fetcher({"a-1": listing()})
    first = run([banner()], tmp_path / "r1", f, None, cache=cache)[0]
    assert first["result"] == "PASS"
    second = run([banner()], tmp_path / "r2", f, {"a": row(expected_brand="Puma")}, cache=cache)[0]
    assert second["result"] == "FAIL" and second["reference_check"]["status"] == "MISMATCH"      # cache hit, still judged


def test_a_hidden_or_user_skipped_banner_is_not_referenced(tmp_path):
    hid = banner(hidden=True, hidden_reason="block_hidden")
    got = run([hid], tmp_path, Fetcher({"a-1": listing()}), {"a": row(expected_brand="Puma")})[0]
    assert got["result"] == "SKIPPED" and "reference_check" not in got


def test_a_listing_that_will_not_load_leaves_the_banner_retryable_and_unjudged(tmp_path):
    def down(slug, kind="curated", **k):
        raise lc.FeedError("HTTP 503")
    got = run([banner()], tmp_path, down, {"a": row(expected_brand="Puma")})[0]
    assert got["result"] == "INCONCLUSIVE" and fv.is_retryable(got)
    assert got.get("reference_check", {}).get("status") in (None, "UNCHECKED")


def test_a_reference_finding_shows_in_the_excel_reason_and_in_alert_text(tmp_path):
    from qa import export_xlsx as xl
    from qa import run_diff as rd
    r = {"banner_id": "a", "result": "FAIL", "reason": "", "alt_text": "A", "destination_raw": "https://ajio.com/s/a-1",
         "reference_check": {"status": "MISMATCH", "reason": "reference: expected brand not on the listing: Puma"}}
    assert "expected brand not on the listing: Puma" in xl._cell(r, "reason")
    d = rd.diff_runs({"a": {**r, "result": "PASS", "reference_check": None}}, {"a": r})
    assert "Puma" in d["newly_failing"][0]["reason"]


# ---- the fill-in template ----

def make_run(tmp_path):
    banners = [banner("a", "https://ajio.com/s/a-1"), banner("b", "https://ajio.com/c/clearance-store-1"), banner("h", hidden=True, hidden_reason="block_hidden")]
    tmp_path.mkdir(parents=True, exist_ok=True)
    fv.save_banners(tmp_path, banners)
    (tmp_path / "results.json").write_text(json.dumps([
        {"banner_id": "a", "result": "PASS", "attempts": 1, "listing_title": "Min 40 Percent Off",
         "banner_check": {"banner_brands": ["new balance", "under armour"], "banner_deal": "MIN. 40% OFF*"}},
        {"banner_id": "b", "result": "FAIL", "attempts": 1}]), encoding="utf-8")
    return tmp_path


def test_the_template_lists_visible_banners_with_blank_expectations_and_context(tmp_path):
    run_dir = make_run(tmp_path / "run")
    out = tmp_path / "config" / "reference.csv"
    got = rt.write_template(run_dir, out)
    assert got["created"] and got["added"] == 2                              # the hidden banner is left out
    text = out.read_text(encoding="utf-8-sig")
    assert text.startswith("# Reference rows")
    rows = list(csv.DictReader([l for l in text.splitlines() if not l.startswith("#")]))
    a = next(r for r in rows if r["banner_id"] == "a")
    assert (a["expected_brand"], a["expected_category"], a["expected_deeplink_type"]) == ("", "", "")
    assert a["destination_type"] and a["artwork_brands"] == "new balance | under armour" and a["listing_title"] == "Min 40 Percent Off"
    assert a["tool_result"] == "PASS" and a["seen_in_run"] == "run"
    assert load_reference(out) and rc.load_default(out) == ({}, out)          # nothing filled in yet: the check stays off


def test_running_it_again_adds_only_new_banners_and_never_touches_filled_in_rows(tmp_path):
    run_dir = make_run(tmp_path / "run")
    out = tmp_path / "reference.csv"
    rt.write_template(run_dir, out)
    text = out.read_text(encoding="utf-8-sig").replace("a,,,,", "a,Puma,,PLP,my note", 1)      # the user fills a row in
    out.write_text(text, encoding="utf-8-sig")
    again = rt.write_template(run_dir, out)
    assert again == {"added": 0, "already": 2, "path": out, "created": False}
    assert out.read_text(encoding="utf-8-sig") == text                                          # byte-identical
    fv.save_banners(run_dir, [*fv.load_banners(run_dir), banner("c", "https://ajio.com/s/c-3")])
    third = rt.write_template(run_dir, out)
    assert third["added"] == 1 and third["already"] == 2
    rows, _ = rc.load_default(out)
    assert rows["a"].expected_brand == "Puma" and rows["a"].notes == "my note"
    assert "c" in load_reference(out)


def test_merging_into_a_file_without_the_context_columns_still_works(tmp_path):
    run_dir = make_run(tmp_path / "run")
    out = tmp_path / "ref.csv"
    out.write_text("banner_id,expected_brand,expected_category,expected_deeplink_type,notes\nzz,Nike,,,", encoding="utf-8")   # no trailing newline
    got = rt.write_template(run_dir, out)
    assert got["added"] == 2 and set(load_reference(out)) == {"zz", "a", "b"}
    assert load_reference(out)["zz"].expected_brand == "Nike"


def test_latest_run_dir_prefers_the_newest_run_that_has_results(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "RUNS_DIR", tmp_path)
    for name, done in (("20260101T000000Z_x", True), ("20260102T000000Z_x", True), ("20260103T000000Z_x", False)):
        d = tmp_path / name
        d.mkdir()
        (d / "banners.json").write_text("[]", encoding="utf-8")
        if done:
            (d / "results.json").write_text("[]", encoding="utf-8")
    assert rt.latest_run_dir().name == "20260102T000000Z_x"
    with pytest.raises(FileNotFoundError):
        monkeypatch.setattr(rt, "RUNS_DIR", tmp_path / "nothing")
        rt.latest_run_dir()
