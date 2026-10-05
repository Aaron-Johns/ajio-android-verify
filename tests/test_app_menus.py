"""qa/app_menus.py: the top / bottom navigation, ads and trending of the app. Fakes only; nothing touches the network."""
import json

import pytest

from qa import app_menus as am
from qa.feed_client import FeedError


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.text = status, body if body is not None else {}, "x"

    def json(self):
        return self._body


class Session:
    """get() pops the next preset response and records what it was asked."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.responses.pop(0)


NAV = {"items": [{"navigation": [
    {"display": "Men", "active": True, "images": [{"label": "active", "value": "https://cdn/men.png"}], "inactive_image": {"value": "https://cdn/men-off.png"},
     "action": {"page": {"type": "sections", "url": "/sections/men-nav-page"}},
     "sub_navigation": [
         {"display": "Footwear", "images": [], "action": {"page": {"type": "external", "query": {"url": ["https://www.ajio.com/shop/footwear"]}}},
          "user": {"user_type": "all_user"}},
         {"display": "Old", "active": False, "action": {"page": {"type": "sections", "url": "/sections/old"}}}]},
    {"display": "Home", "action": {"page": {"type": "home"}}, "acl": ["all"]}]}]}


def test_navigation_is_flattened_parents_first_with_what_each_entry_opens():
    items = am.flatten_navigation(NAV)
    assert [(i["title"], i["level"], i["parent"]) for i in items] == [("Men", 0, None), ("Footwear", 1, 0), ("Old", 1, 0), ("Home", 0, None)]
    men, shoes, old, home = items
    assert men["images"] == ["https://cdn/men.png", "https://cdn/men-off.png"] and men["link"] == "" and men["opens"] == "app screen: /sections/men-nav-page"
    assert shoes["link"] == "https://www.ajio.com/shop/footwear" and shoes["opens"] == "web page" and shoes["audience"] == "all_user"
    assert old["active"] is False and home["opens"] == "app screen: home" and home["audience"] == "all"
    assert [i["id"] for i in items] == [0, 1, 2, 3]


def test_a_navigation_response_that_is_a_list_is_read_too():
    assert am.flatten_navigation([NAV])[0]["title"] == "Men"


def test_trends_become_items_with_their_text_picture_and_search():
    items = am.flatten_trends({"topTrends": [{"displayName": "#A", "description": "d", "image": "https://cdn/a.png", "redirectQuery": ":relevance:trend:#A"},
                                             {"displayName": "#B"}]})
    assert items[0]["title"] == "#A" and items[0]["description"] == "d" and items[0]["images"] == ["https://cdn/a.png"]
    assert items[0]["opens"] == "in-app search: :relevance:trend:#A" and items[1]["images"] == []


def test_ads_hang_under_their_slot_in_rank_order_and_an_empty_slot_is_still_listed():
    ad = lambda rank, dest: {"rank": str(rank), "elements": {"mobile_image": f"https://cdn/{rank}.jpg", "desktop_image": f"https://cdn/{rank}d.jpg", "destination_url": dest}}
    items = am.flatten_ads([("Home screen", {"ads": {"98": [ad(2, "https://b"), ad(1, "https://a")], "88": []}}), ("My account banner", {"ads": {}})])
    assert [(i["title"], i["level"]) for i in items] == [("Home screen", 0), ("", 1), ("", 1), ("My account banner", 0)]
    assert items[0]["opens"] == "2 ads" and items[3]["opens"] == "0 ads"
    assert [i["link"] for i in items[1:3]] == ["https://a", "https://b"] and items[1]["images"] == ["https://cdn/1.jpg", "https://cdn/1d.jpg"]
    assert items[1]["parent"] == 0 and items[1]["description"] == "rank 1"


# ---- requests ----

def test_a_temporary_error_is_tried_again_and_a_refusal_is_not():
    sleeps = []
    ok = am._get_json("u", lambda: {}, "x", session=Session(Resp(502), Resp(200, {"a": 1})), sleep=sleeps.append)
    assert ok == {"a": 1} and len(sleeps) == 1
    with pytest.raises(FeedError, match="HTTP 403"):
        am._get_json("u", lambda: {}, "x", session=Session(Resp(403)), sleep=sleeps.append)
    with pytest.raises(FeedError, match="gave up after 4 attempts"):
        am._get_json("u", lambda: {}, "x", session=Session(*[Resp(503)] * 4), sleep=lambda s: None)


def test_the_navigation_request_is_signed_and_carries_the_cohort(monkeypatch):
    monkeypatch.setenv("AJIO_THEME_BEARER", "bearer-x")
    sess = Session(Resp(200, NAV))
    got = am.fetch_navigation("top-nav", user_groups="l1:premium|l2:p_null,false,unisex,noasp", session=sess, sleep=lambda s: None)
    call = sess.calls[0]
    assert got is NAV and call["url"].endswith("/content/v2.0/navigations?slug=rn-ajio-new-top-nav")
    h = call["headers"]
    assert h["user-groups"] == "l1:premium|l2:p_null,false,unisex,noasp" and h["authorization"] == "Bearer bearer-x"
    assert h["x-fp-signature"] and h["x-fp-date"] and h["client_type"] == "Android"


def test_a_navigation_retry_gets_a_fresh_signature_and_request_id(monkeypatch):
    monkeypatch.setenv("AJIO_THEME_BEARER", "b")
    sess = Session(Resp(500), Resp(200, NAV))
    am.fetch_navigation("bottom-nav", user_groups="g", session=sess, sleep=lambda s: None)
    assert sess.calls[0]["headers"]["requestid"] != sess.calls[1]["headers"]["requestid"]


def test_trends_ask_for_the_shoppers_segment():
    sess = Session(Resp(200, {"topTrends": []}))
    am.fetch_trends("premium", session=sess)
    assert sess.calls[0]["params"] == {"type": "Cohort", "value": "premium", "store": "ajio"} and "authorization" not in sess.calls[0]["headers"]


def test_an_ad_request_carries_segment_state_and_login_status():
    sess = Session(Resp(200, {"ads": {}}), Resp(200, {"ads": {}}))
    am.fetch_ads_slot("_sections_ajio", 10, "nontransacted", "unisex", "ASSAM", session=sess)
    am.fetch_ads_slot("_myaccount_banner", 25, "premium", "men", "GUJARAT", session=sess)
    a, b = (c["params"] for c in sess.calls)
    assert (a["pt"], a["pcnt_au"], a["f.user_cohort"], a["f.user_type"], a["f.state"], a["f.user_loginstatus"]) == ("_sections_ajio", "10", "nontransacted", "new", "ASSAM", "NON_LOGGED_IN")
    assert (b["f.user_cohort"], b["f.user_type"], b["f.user_secondary_cohort"], b["f.state"]) == ("premium", "Existing", "p_null,false,men,noasp", "GUJARAT")


def test_collect_asks_each_ad_slot_once(monkeypatch):
    monkeypatch.setattr(am, "fetch_ads_slot", lambda pt, n, *a, **k: {"ads": {"1": [{"rank": "1", "elements": {"mobile_image": f"https://cdn/{pt}.jpg"}}]}})
    items = am.collect("ads", "premium", "unisex", "KARNATAKA")
    assert [i["title"] for i in items if i["level"] == 0] == [name for _, name, _ in am.AD_SLOTS]
    with pytest.raises(ValueError):
        am.collect("nope", "premium", "unisex", "KARNATAKA")


# ---- saving ----

def test_pictures_are_saved_once_each_and_a_failed_one_keeps_its_address(tmp_path):
    items = [{"images": ["https://cdn/a.png", "https://cdn/b.png"]}, {"images": ["https://cdn/a.png"]}, {"images": []}]
    calls = []

    def fetcher(url):
        calls.append(url)
        return ("OK", b"img", "image/png") if url.endswith("a.png") else ("HTTP 404", None, "")

    failed = am.download_images(items, tmp_path, fetcher=fetcher)
    assert failed == 1 and sorted(calls) == ["https://cdn/a.png", "https://cdn/b.png"]
    name = am.image_file_name("https://cdn/a.png", "image/png")
    assert items[0]["image_files"] == [name, None] and items[1]["image_files"] == [name] and items[2]["image_files"] == []
    assert (tmp_path / "images" / name).read_bytes() == b"img"


def test_a_run_writes_its_menu_meta_and_pictures_and_a_failure_exits_non_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(am.fc, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(am, "collect", lambda *a, **k: [{"id": 0, "parent": None, "level": 0, "title": "Men", "images": ["https://cdn/m.png"], "image_files": []}])
    monkeypatch.setattr(am, "download_images", lambda items, out, **k: 0)        # the pictures have their own test above
    assert am.main(["--kind", "top-nav", "--l1", "premium"]) == 0
    out = next(tmp_path.glob("*_topnav_menu"))
    menu = json.loads((out / "menu.json").read_text(encoding="utf-8"))
    assert menu["kind"] == "top-nav" and menu["l1"] == "premium" and menu["items"][0]["title"] == "Men"
    assert json.loads((out / "menu_meta.json").read_text(encoding="utf-8"))["items"] == 1
    assert am.load_menu(out)["kind"] == "top-nav" and am.load_menu_meta(out)["with_images"] == 1 and am.load_menu(tmp_path) is None

    def boom(*a, **k):
        raise FeedError("trending: HTTP 502")
    monkeypatch.setattr(am, "collect", boom)
    assert am.main(["--kind", "trending"]) == 1 and "HTTP 502" in capsys.readouterr().out


# ---- third-party data must not be able to add a script to a page, a file or a link ----

def test_only_plain_web_addresses_count_as_links_or_pictures():
    assert am.web_url("https://a.b/c?d=1") == "https://a.b/c?d=1" and am.web_url("  http://x.y/z ") == "http://x.y/z"
    for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>", "file:///c:/x", "//evil.example/x", "ftp://x.y", "https://a b", "vbscript:x", "", None):
        assert am.web_url(bad) == "", bad


def test_unsafe_addresses_in_third_party_data_never_reach_an_item():
    nav = {"items": [{"navigation": [{"display": "X", "images": [{"value": "javascript:alert(1)"}, {"value": "https://cdn/ok.png"}],
                                      "action": {"page": {"type": "external", "query": {"url": ["javascript:alert(1)"]}}}}]}]}
    item = am.flatten_navigation(nav)[0]
    assert item["link"] == "" and item["images"] == ["https://cdn/ok.png"] and item["opens"] == "not a web address"
    ad = {"rank": "1", "elements": {"destination_url": "data:text/html,<script>x</script>", "mobile_image": "javascript:x", "desktop_image": "https://cdn/d.jpg"}}
    ads = am.flatten_ads([("Home screen", {"ads": {"1": [ad]}})])
    assert ads[1]["link"] == "" and ads[1]["opens"] == "" and ads[1]["images"] == ["https://cdn/d.jpg"]
    assert am.flatten_trends({"topTrends": [{"displayName": "#A", "image": "file:///c:/x.png"}]})[0]["images"] == []


def test_an_svg_is_never_saved_because_it_can_carry_a_script(tmp_path):
    items = [{"images": ["https://cdn/logo.svg", "https://cdn/ok.png"]}]
    fetcher = lambda url: ("OK", b"<svg onload=alert(1)>", "image/svg+xml") if url.endswith(".svg") else ("OK", b"png", "image/png")
    assert am.download_images(items, tmp_path, fetcher=fetcher) == 1
    assert items[0]["image_files"][0] is None and items[0]["image_files"][1].endswith(".png")
    assert not list((tmp_path / "images").glob("*.svg")) and len(list((tmp_path / "images").iterdir())) == 1
