"""Listing-page extraction on a trimmed real dump, the scroll loop, and the consistency checks."""
import csv
from pathlib import Path

from qa.spotcheck import plp
from qa.spotcheck.plp import Header, PlpData, Product

FIX = Path(__file__).resolve().parent / "fixtures"
PLP_SRC = (FIX / "ui_plp.xml").read_text(encoding="utf-8")


def test_header_is_extracted():
    h = plp.parse_header(PLP_SRC)
    assert (h.title, h.subtitle, h.sort) == ("Delivery Starts in 30 Mins", "10K+ Products", "Relevance")
    assert h.sub_categories == ["Men Tshirts", "Men Shirts"]
    assert h.filters == ["90 min", "Shop For", "Category", "Size & Fit"]


def test_fully_visible_cards_are_extracted_field_by_field():
    first, second = plp.parse_products(PLP_SRC)[:2]
    assert (first.brand, first.name) == ("RIO", "Women Graphic Print T-Shirt & Pyjamas Set")
    assert (first.price, first.mrp, first.discount_percent, first.offer_price) == (356, 699, 49, 249)
    assert first.rating == 3.6 and first.tags == ["BESTSELLER", "Selling Fast"]
    assert first.delivery == "4 hours" and first.wishlist_count == "11K" and first.complete and not first.is_ad
    assert (second.price, second.mrp, second.discount_percent, second.offer_price) == (489, 699, 30, 342)
    assert second.rating == 4.4 and second.tags == ["BESTSELLER"]


def test_cards_cut_off_by_the_screen_edge_are_partial_and_ads_are_flagged():
    products = plp.parse_products(PLP_SRC)
    assert len(products) == 4
    partial = products[2:]
    assert not any(p.complete for p in partial) and all(p.label for p in partial)
    assert [p.is_ad for p in partial] == [False, True]


def test_merge_replaces_a_partial_card_with_its_complete_version():
    seen = {}
    assert plp.merge_products(seen, [Product("card A"), Product("card B", "X", "n", 10)]) == 2
    assert plp.merge_products(seen, [Product("card A", "Y", "m", 20), Product("card B", "Z", "q", 99)]) == 0
    assert seen["card A"].brand == "Y"          # partial -> complete
    assert seen["card B"].brand == "X"          # complete is never overwritten


class FakeDevice:
    def __init__(self, sources):
        self.sources, self.scrolls = list(sources), 0

    def state(self):
        return ("com.ril.ajio", ".home.AjioHomeActivity", self.sources[min(self.scrolls, len(self.sources) - 1)])

    def scroll(self, direction, percent):
        self.scrolls += 1

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")
        return str(path)


def test_capture_stops_when_scrolling_yields_nothing_new(tmp_path):
    device = FakeDevice([PLP_SRC])
    data = plp.capture_plp(device, tmp_path, "s0")
    assert data.stopped == "no_new_products" and data.screens == 3 and device.scrolls == 2
    assert len(data.products) == 4 and len(data.screenshots) == 3 and data.header.title == "Delivery Starts in 30 Mins"


def test_capture_respects_the_screen_limit(tmp_path):
    first = PLP_SRC
    second = PLP_SRC.replace("Women Graphic Print T-Shirt &amp; Pyjamas Set", "Another Product").replace("₹356", "₹300")
    data = plp.capture_plp(FakeDevice([first, second, first.replace("RIO", "ZED")]), tmp_path, "s1", max_screens=3)
    assert data.screens == 3 and data.stopped == "max_screens" and len(data.products) > 4


def good(**kw):
    base = dict(label="a", brand="B", name="n", price=356, mrp=699, discount_percent=49)
    return Product(**{**base, **kw})


def data_of(*products, title="Title"):
    return PlpData(Header(title=title), list(products), 1)


def failed(data):
    return {c["check"] for c in plp.checks(data) if not c["ok"]}


def test_checks_pass_on_the_real_listing():
    products = [p for p in plp.parse_products(PLP_SRC)]
    assert failed(PlpData(plp.parse_header(PLP_SRC), products, 1)) == set()


def test_checks_catch_each_kind_of_problem():
    assert failed(data_of()) == {"has_products"}
    assert failed(data_of(good(), title=None)) == {"has_title"}
    assert failed(data_of(good(discount_percent=10))) == {"discount_matches_price_and_mrp"}
    assert failed(data_of(good(price=800, discount_percent=None))) == {"price_not_above_mrp"}
    assert "no_duplicate_products" in failed(data_of(good(), good()))
    assert failed(data_of(good(discount_percent=48))) == set()      # 1 point of rounding tolerance


def test_products_csv_roundtrip(tmp_path):
    data = data_of(good(tags=["BESTSELLER", "Selling Fast"], rating=4.4))
    rows = [{"slide": 0, **r} for r in plp.product_rows(data)]
    path = tmp_path / "p.csv"
    plp.write_products_csv(rows, path, ["slide"])
    with open(path, encoding="utf-8-sig") as f:
        row = next(csv.DictReader(f))
    assert row["brand"] == "B" and row["price"] == "356" and row["tags"] == "BESTSELLER | Selling Fast" and row["slide"] == "0"
