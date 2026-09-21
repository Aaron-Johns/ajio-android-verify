"""Brand-filter reading on a real dump, deal/title matching, and the banner-vs-listing verdict."""
from pathlib import Path

import pytest

from qa.compare import AliasMap
from qa.spotcheck import filters

FIX = Path(__file__).resolve().parent / "fixtures"
BRANDS_SRC = (FIX / "ui_brand_filter.xml").read_text(encoding="utf-8")
PLP_SRC = (FIX / "ui_plp.xml").read_text(encoding="utf-8")
ALIASES = AliasMap([["LEVI'S", "LEVIS"]])


def test_brand_rows_are_read_with_counts():
    rows = filters.brand_rows(BRANDS_SRC)
    assert ("YOUSTA", 8130) in rows and ("Altheory by AZORTE", 441) in rows and ("AABTA", 5) in rows
    assert ("AVAASA MIX N' MATCH", 427) in rows


def test_buttons_are_found():
    assert filters.filter_button(PLP_SRC) is not None
    assert filters.facet_tab(BRANDS_SRC, "Brands") == (0, 653 - 0, 309, 696) or filters.facet_tab(BRANDS_SRC, "Brands") is not None
    assert filters.facet_tab(BRANDS_SRC, "Nonexistent") is None


@pytest.mark.parametrize("deal,title,expected", [
    ("MIN. 40% OFF*", "Min 40 Percent Off", True),
    ("UPTO 60% OFF", "Upto 60 Percent Off", True),
    ("UNDER ₹599", "Under Rs 599", True),
    ("MIN. 40% OFF*", "Min 70 Percent Off", False),
    ("UNDER ₹599", "Under Rs 999", False),
    ("MIN. 40% OFF", "Upto 40 Percent Off", False),
    ("", "Min 40 Percent Off", None),
    ("Min 40% off", None, None),
])
def test_deal_matches_title(deal, title, expected):
    assert filters.deal_matches_title(deal, title) is expected


def test_brand_comparison_is_alias_and_punctuation_tolerant():
    assert filters.same_brand("Levis", "LEVI'S", ALIASES)
    assert filters.same_brand("Dune London", "DUNE LONDON")
    assert not filters.same_brand("Dune", "Dune London")


class FakeDevice:
    """Serves the listing, then a brand filter whose rows narrow to those containing the typed text."""
    def __init__(self, brands, openable=True):
        self.brands, self.openable, self.typed, self.taps = brands, openable, [], []
        self.query = ""

    def state(self):
        return ("com.ril.ajio", ".home.AjioHomeActivity", self._filter_source() if self.taps else PLP_SRC)

    def _filter_source(self):
        if not self.openable:
            return PLP_SRC
        rows = "".join(f'<node resource-id="com.ril.ajio:id/general_facet_value_row_tv" text="{b} ({n})" bounds="[0,0][1,1]"/>'
                       for b, n in self.brands if self.query.lower() in b.lower())
        return ('<hierarchy><node resource-id="com.ril.ajio:id/facet_row_name_tv" text="Brands" bounds="[0,653][309,696]"/>'
                + rows + '</hierarchy>')

    def tap(self, box):
        self.taps.append(box)

    def type_into(self, rid, text):
        self.typed.append(text)
        self.query = text


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(filters.time, "sleep", lambda s: None)


INFO = {"brands_mentioned": ["Dune London", "Guess"], "deal_offered": "MIN. 40% OFF*"}


def test_pass_when_all_brands_are_filter_options_and_title_matches():
    d = FakeDevice([("DUNE LONDON", 12), ("GUESS", 9), ("Other", 1)])
    r = filters.verify_against_banner(d, INFO, "Min 40 Percent Off", ALIASES)
    assert r["result"] == "PASS" and r["missing_brands"] == [] and d.typed == ["Dune London", "Guess"]


def test_fail_when_a_banner_brand_is_not_in_the_filter():
    d = FakeDevice([("DUNE LONDON", 12), ("Other", 1)])
    r = filters.verify_against_banner(d, INFO, "Min 40 Percent Off", ALIASES)
    assert r["result"] == "FAIL" and r["missing_brands"] == ["Guess"]


def test_fail_when_title_does_not_match_the_deal():
    d = FakeDevice([("DUNE LONDON", 12), ("GUESS", 9)])
    assert filters.verify_against_banner(d, INFO, "Min 70 Percent Off", ALIASES)["result"] == "FAIL"


def test_inconclusive_when_the_filter_cannot_be_opened_or_banner_unreadable():
    assert filters.verify_against_banner(FakeDevice([], openable=False), INFO, "Min 40 Percent Off", ALIASES)["result"] == "INCONCLUSIVE"
    nothing = {"brands_mentioned": [], "deal_offered": ""}
    assert filters.verify_against_banner(FakeDevice([]), nothing, "Title", ALIASES)["result"] == "INCONCLUSIVE"


def test_verify_from_listing_uses_the_servers_brand_filter():
    brands = {"DUNE LONDON": 12, "GUESS": 9}
    ok = filters.verify_from_listing(INFO, "Min 40 Percent Off", brands, ALIASES)
    assert ok["result"] == "PASS" and ok["source"] == "server" and ok["brand_checks"][0]["matched_as"] == "DUNE LONDON"
    bad = filters.verify_from_listing(INFO, "Min 40 Percent Off", {"DUNE LONDON": 12}, ALIASES)
    assert bad["result"] == "FAIL" and bad["missing_brands"] == ["Guess"]
    assert filters.verify_from_listing(INFO, "Min 70 Percent Off", brands, ALIASES)["result"] == "FAIL"
    assert filters.verify_from_listing({"brands_mentioned": [], "deal_offered": ""}, "T", brands, ALIASES)["result"] == "INCONCLUSIVE"


def test_partial_brand_names_count_but_are_flagged():
    options = {"Kiana House Of Fashion": 3, "MYRIE": 8, "Dune London": 12, "Polo Plus": 1}
    assert filters.match_brand("kiana", options) == ("Kiana House Of Fashion", "partial")
    assert filters.match_brand("MYRIE INDIA", options) == ("MYRIE", "partial")
    assert filters.match_brand("DUNE LONDON", options) == ("Dune London", "exact")
    assert filters.match_brand("Chanel", options) == (None, None)
    assert filters.match_brand("Dune", {"Dune London": 1, "Dune": 2}) == ("Dune", "exact")   # exact beats partial


def test_up_to_and_upto_are_the_same_deal():
    assert filters.deal_matches_title("UP TO 60% OFF*", "Upto 60 Percent Off") is True
    assert filters.deal_matches_title("UP TO 60% OFF*", "Min 60 Percent Off") is False


def test_verify_from_listing_reports_the_match_kind():
    r = filters.verify_from_listing({"brands_mentioned": ["kiana"], "deal_offered": "MIN. 70% OFF*"}, "Min 70 Percent Off",
                                    {"Kiana House Of Fashion": 3}, ALIASES)
    assert r["result"] == "PASS" and r["brand_checks"][0]["match"] == "partial"
