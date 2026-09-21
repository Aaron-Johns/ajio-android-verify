"""Landing-page parsing (on trimmed real UiAutomator dumps) and the judge, with a fixture per status."""
from pathlib import Path

import pytest

from qa import deeplink_resolve as dl
from qa.compare import AliasMap
from qa.spotcheck import landing as lp
from qa.spotcheck.landing import Landing, VisionVerdict, judge
from qa.status import SpotStatus as S

FIX = Path(__file__).resolve().parent / "fixtures"
HOME_SRC = (FIX / "ui_home.xml").read_text(encoding="utf-8")
PLP_SRC = (FIX / "ui_plp.xml").read_text(encoding="utf-8")
WEB_SRC = (FIX / "ui_webview.xml").read_text(encoding="utf-8")
HOME_ACT, WEB_ACT = ".home.AjioHomeActivity", ".web.CustomWebViewActivity"
ALIASES = AliasMap([["LEVI'S", "LEVIS"]])


def landing(kind, title=None, brands=(), products=0):
    return Landing(kind, lp.APP_PACKAGE, HOME_ACT, title=title, brands=list(brands), product_count=products)


def t(url):
    return dl.resolve(url)


# ---------------- parsing real dumps ----------------

def test_plp_dump_is_recognised_despite_the_home_screen_leaking_underneath():
    got = lp.parse_landing(lp.APP_PACKAGE, HOME_ACT, PLP_SRC, before_source=HOME_SRC)
    assert got.kind == lp.PLP and got.title == "Delivery Starts in 30 Mins"
    assert got.brands[:2] == ["RIO", "RIO"] and got.product_count >= 2


def test_webview_dump_is_recognised_with_its_title():
    got = lp.parse_landing(lp.APP_PACKAGE, WEB_ACT, WEB_SRC, before_source=HOME_SRC)
    assert (got.kind, got.title) == (lp.WEBVIEW, "HDFC EMI Credit | AJIO")


def test_other_package_is_external():
    assert lp.parse_landing("com.android.chrome", "org.chromium.Main", WEB_SRC).kind == lp.EXTERNAL


def test_unchanged_screen_means_the_tap_did_not_navigate():
    got = lp.parse_landing(lp.APP_PACKAGE, HOME_ACT, HOME_SRC, before_source=HOME_SRC)
    assert got.kind == lp.HOME and got.similarity_to_before == 1.0


def test_changed_but_unrecognised_in_app_screen_is_other():
    other = '<hierarchy><node class="x" resource-id="com.ril.ajio:id/foo" text="Something" content-desc="" clickable="false" bounds="[0,0][1,1]"/></hierarchy>'
    assert lp.parse_landing(lp.APP_PACKAGE, HOME_ACT, other, before_source=HOME_SRC).kind == lp.OTHER


# ---------------- CONFIRMED ----------------

def test_confirmed_search_link_on_a_listing_is_type_only_evidence():
    j = judge(t("https://www.ajio.com/s/4hoursdelivery-160865"), landing(lp.PLP, "Delivery Starts in 30 Mins", ["RIO"], 4))
    assert (j.status, j.evidence) == (S.CONFIRMED, "type_only")


def test_confirmed_category_when_listing_title_matches():
    j = judge(t("https://www.ajio.com/c/beauty-1786629584"), landing(lp.PLP, "Beauty", [], 6))
    assert (j.status, j.evidence) == (S.CONFIRMED, "type_and_text")


def test_confirmed_brand_link_alias_aware():
    j = judge(t("https://www.ajio.com/b/levis-9"), landing(lp.PLP, "Levi's", ["LEVI'S", "LEVI'S"], 4), aliases=ALIASES)
    assert j.status is S.CONFIRMED and j.evidence == "type_and_text"


def test_confirmed_campaign_page_in_webview_or_in_app():
    assert judge(t("https://www.ajio.com/shop/menswear"), landing(lp.WEBVIEW, "Menswear")).status is S.CONFIRMED
    assert judge(t("https://www.ajio.com/shop/menswear"), landing(lp.OTHER)).status is S.CONFIRMED


def test_confirmed_external_link_opened_outside_or_in_webview():
    ext = Landing(lp.EXTERNAL, "com.android.chrome", "Main")
    assert judge(t("https://apply.scapia.cards/x"), ext).status is S.CONFIRMED
    assert judge(t("https://apply.scapia.cards/x"), landing(lp.WEBVIEW)).status is S.CONFIRMED


def test_confirmed_ambiguous_link_via_page_title_matching_the_slug():
    j = judge(t("https://www.ajio.com/hdfc-emi-credit"), landing(lp.WEBVIEW, "HDFC EMI Credit | AJIO"))
    assert (j.status, j.evidence) == (S.CONFIRMED, "type_and_text")


def test_confirmed_ambiguous_link_via_vision_match():
    j = judge(t("https://www.ajio.com/supercash"), landing(lp.WEBVIEW, "Something else"), VisionVerdict("MATCH", "same brand"))
    assert (j.status, j.evidence) == (S.CONFIRMED, "vision")


# ---------------- APP_DEVIATES_FROM_FEED ----------------

def test_deviates_when_tap_navigates_nowhere():
    j = judge(t("https://www.ajio.com/s/x-1"), landing(lp.HOME))
    assert j.status is S.APP_DEVIATES_FROM_FEED and j.reason == "tap_did_not_navigate"


@pytest.mark.parametrize("kind", [lp.WEBVIEW, lp.EXTERNAL])
def test_deviates_when_a_listing_link_lands_on_a_webview_or_another_app(kind):
    j = judge(t("https://www.ajio.com/c/shoes-7"), landing(kind))
    assert j.status is S.APP_DEVIATES_FROM_FEED and kind.lower() in j.reason


def test_deviates_when_linked_brand_is_not_on_the_listing():
    j = judge(t("https://www.ajio.com/b/nike-1"), landing(lp.PLP, "Shoes", ["PUMA", "PUMA"], 4))
    assert j.status is S.APP_DEVIATES_FROM_FEED and "nike" in j.reason


def test_deviates_when_in_app_campaign_leaves_the_app():
    assert judge(t("https://www.ajio.com/shop/menswear"), Landing(lp.EXTERNAL, "com.android.chrome", "Main")).status is S.APP_DEVIATES_FROM_FEED


def test_deviates_when_external_link_stays_on_a_listing():
    assert judge(t("https://apply.scapia.cards/x"), landing(lp.PLP, "Shoes", ["X"], 4)).status is S.APP_DEVIATES_FROM_FEED


def test_deviates_when_product_link_lands_on_a_listing():
    assert judge(t("https://www.ajio.com/p/469012345_black"), landing(lp.PLP, "Shoes", ["X"], 4)).status is S.APP_DEVIATES_FROM_FEED


def test_deviates_when_vision_says_different_brand_and_title_does_not_explain_it():
    j = judge(t("https://www.ajio.com/supercash"), landing(lp.WEBVIEW, "Nike Store"), VisionVerdict("DIFFERENT", "hdfc vs nike"))
    assert j.status is S.APP_DEVIATES_FROM_FEED and j.evidence == "type_only"


# ---------------- INCONCLUSIVE ----------------

def test_inconclusive_on_unrecognised_in_app_screen():
    assert judge(t("https://www.ajio.com/s/x-1"), landing(lp.OTHER)).status is S.INCONCLUSIVE


def test_inconclusive_when_listing_is_empty():
    j = judge(t("https://www.ajio.com/s/x-1"), landing(lp.PLP, "Empty", [], 0))
    assert j.status is S.INCONCLUSIVE and j.reason == "listing_page_shows_no_products"


def test_inconclusive_when_category_title_differs():
    assert judge(t("https://www.ajio.com/c/beauty-1"), landing(lp.PLP, "Sale", [], 4)).status is S.INCONCLUSIVE


def test_inconclusive_ambiguous_link_without_evidence():
    j = judge(t("https://www.ajio.com/supercash"), landing(lp.WEBVIEW, "Unrelated"))
    assert j.status is S.INCONCLUSIVE and j.reason == "ambiguous_link_no_supporting_evidence"


def test_inconclusive_when_campaign_link_opens_a_listing():
    assert judge(t("https://www.ajio.com/shop/menswear"), landing(lp.PLP, "Men", ["X"], 4)).status is S.INCONCLUSIVE


def test_inconclusive_when_no_rule_applies():
    assert judge(t("https://www.ajio.com/offers"), landing(lp.PLP, "x", ["y"], 4)).status is S.INCONCLUSIVE
    assert judge(t("https://www.ajio.com/"), landing(lp.PLP, "x", ["y"], 4)).status is S.INCONCLUSIVE


# ---------------- helpers ----------------

def test_title_match_and_brand_overlap_helpers():
    assert lp.title_matches_slug("HDFC EMI Credit | AJIO", "hdfc emi credit")
    assert not lp.title_matches_slug("Nike Store", "hdfc emi credit")
    assert not lp.title_matches_slug("anything", None)
    assert lp.brands_overlap(["LEVIS"], ["LEVI'S"], ALIASES)
    assert lp.brands_overlap(["HDFC Bank"], ["HDFC"])
    assert not lp.brands_overlap(["Nike"], ["Puma"])
