"""The banner-deal-vs-sorted-listing check (qa/deal_sort_check.py): which deals it understands, what each one asks of the
sorted listing, and that a sort it can't get never becomes a failure. Nothing here touches the network."""
import pytest

from qa import deal_sort_check as ds
from qa import listing_client as lc
from tests.test_feed_verify import Fetcher, banner, run


def product(price=None, discount=None):
    return {"price": {"value": price}, "discountPercent": f"{discount}% off" if discount is not None else None}


def _discount(p):
    return int(p["discountPercent"].split("%")[0]) if p["discountPercent"] else 0


class Sorted:
    """A listing of `products` served in the order the sort asks for, page by page; records what was asked."""

    def __init__(self, products):
        self.products, self.asked = products, []

    def __call__(self, sort, page, size):
        self.asked.append((sort, page, size))
        key = {":discount-desc": lambda p: -_discount(p), ":prce-asc": lambda p: p["price"]["value"],
               ":prce-desc": lambda p: -p["price"]["value"]}[sort]
        ordered = sorted(self.products, key=key)
        return lc.Listing("s", "t", len(ordered), page, products=ordered[page * size:(page + 1) * size])


CATALOGUE = [product(price=p, discount=d) for p, d in ((161, 46), (390, 70), (420, 70), (943, 41), (1500, 30), (5760, 52))]


@pytest.mark.parametrize("text,rule", [
    ("MIN. 40% OFF*", ("min_discount", 40, None)),
    ("Minimum 50% off", ("min_discount", 50, None)),
    ("MIN. 50% OFF + EXTRA 30% OFF", ("min_discount", 50, None)),
    ("20-70% OFF*", ("discount_range", 20, 70)),
    ("FROM 20% TO 70% OFF", ("discount_range", 20, 70)),
    ("UNDER ₹799", ("under_price", 799, None)),
    ("Under Rs. 1,299", ("under_price", 1299, None)),
    ("STARTING AT RS 399", ("starting_price", 399, None)),
    ("STARTING FROM ₹1,499", ("starting_price", 1499, None)),
])
def test_the_deals_it_understands(text, rule):
    assert ds.parse_deal(text) == rule


@pytest.mark.parametrize("text", [None, "", "UP TO 60% OFF*", "FLAT 50% OFF", "NEW ARRIVALS", "BUY 1 GET 1"])
def test_other_wording_has_no_rule_and_is_left_alone(text):
    assert ds.parse_deal(text) is None and ds.check(text, 6, Sorted(CATALOGUE)) is None


def test_min_discount_reads_the_lowest_discount_from_the_last_page_of_discount_desc():
    get = Sorted(CATALOGUE)
    assert ds.check("MIN. 40% OFF", 6, get)["status"] == "MATCH"                    # lowest is 30, allowed down to 30
    r = ds.check("MIN. 50% OFF", 6, get)
    assert r["status"] == "MISMATCH" and "lowest discount on the listing is 30%" in r["reason"]
    assert r["observed"] == {"lowest_discount": 30}
    assert {a[0] for a in get.asked} == {":discount-desc"}                          # the API has no discount-asc to ask for


def test_the_last_page_is_worked_out_from_the_total():
    get = Sorted(CATALOGUE * 30)                                                    # 180 products, 60 a page -> pages 0..2
    ds.check("MIN. 40% OFF", 180, get)
    assert get.asked == [(":discount-desc", 2, ds.PAGE_SIZE)]


def test_a_range_only_checks_its_low_end_and_never_caps_the_top():
    get = Sorted(CATALOGUE)
    assert ds.check("20-70% OFF", 6, get)["status"] == "MATCH"                      # lowest 30 >= 10
    assert ds.check("40-60% OFF", 6, get)["status"] == "MATCH"                      # lowest 30 >= 30; the listing's 70% is not held against it
    assert ds.check("30-40% OFF", 6, get)["status"] == "MATCH"                      # a top of 40 against a 70% item: still fine
    r = ds.check("60-65% OFF", 6, get)                                              # lowest 30 < 50
    assert r["status"] == "MISMATCH" and "lowest discount on the listing is 30%" in r["reason"]
    assert {a[0] for a in get.asked} == {":discount-desc"} and "highest_discount" not in r["observed"]


def test_under_price_looks_at_the_dearest_item():
    get = Sorted(CATALOGUE)
    assert ds.check("UNDER ₹5761", 6, get)["status"] == "MATCH"
    assert ds.check("UNDER ₹5759", 6, get)["status"] == "MATCH"                # one rupee of slack
    r = ds.check("UNDER ₹799", 6, get)
    assert r["status"] == "MISMATCH" and "dearest item on the listing is Rs 5760" in r["reason"]
    assert get.asked[-1] == (":prce-desc", 0, ds.PAGE_SIZE)


def test_starting_price_looks_at_the_cheapest_item():
    get = Sorted(CATALOGUE)
    assert ds.check("STARTING AT ₹161", 6, get)["status"] == "MATCH"
    assert ds.check("STARTING AT ₹160", 6, get)["status"] == "MISMATCH"        # strictly x: no slack either way
    assert ds.check("STARTING AT ₹162", 6, get)["status"] == "MISMATCH"
    r = ds.check("STARTING AT ₹399", 6, get)
    assert r["status"] == "MISMATCH" and "cheapest item on the listing is Rs 161" in r["reason"]
    assert get.asked[-1] == (":prce-asc", 0, ds.PAGE_SIZE)


def test_a_sort_that_cannot_be_loaded_is_unchecked_not_a_failure():
    def broken(sort, page, size):
        raise RuntimeError("HTTP 403 outside the allowed range")
    r = ds.check("MIN. 40% OFF", 999999, broken)
    assert r["status"] == "UNCHECKED" and r["reason"] == "couldn't get the minimum discount since the page was too large" and r["note"]
    r = ds.check("UNDER \u20b9799", 6, lambda s, p, n: (_ for _ in ()).throw(RuntimeError("HTTP 503")))
    assert r["status"] == "UNCHECKED" and "couldn't load the sorted listing" in r["reason"] and r["note"]
    assert ds.check("UNDER ₹799", 6, lambda s, p, n: lc.Listing("s", "t", 0, 0, products=[]))["status"] == "UNCHECKED"
    assert ds.check("MIN. 40% OFF", 0, Sorted([]))["status"] == "UNCHECKED"


# ---- inside the pipeline ---------------------------------------------------------------------------------

class SortedFetcher(Fetcher):
    """Plain fetches get the listing; sorted ones get the products in the order asked."""

    def __init__(self, products):
        listing = lc.Listing("a-1", "Min 40 Percent Off", len(products), 0, {"NEW BALANCE": 5, "Under Armour": 9}, {},
                             {"Brands": 2}, products)
        super().__init__({"a-1": listing})
        self.products = products

    def __call__(self, slug, kind="curated", **k):
        base = super().__call__(slug, kind, **k)
        if not k.get("sort"):
            return base
        return Sorted(self.products)(k["sort"], k.get("page", 0), k.get("page_size") or 25)


def test_a_deal_the_sorted_listing_contradicts_fails_the_banner_with_a_reason(tmp_path):
    products = [product(price=500, discount=d) for d in (20, 25, 30)]
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, SortedFetcher(products))[0]         # INFO's deal: MIN. 40% OFF*
    assert r["result"] == "FAIL" and "lowest discount on the listing is 20%" in r["reason"]
    assert r["banner_check"]["sort_check"]["status"] == "MISMATCH"


def test_a_deal_the_sorted_listing_supports_still_passes(tmp_path):
    products = [product(price=500, discount=d) for d in (30, 45, 60)]
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, SortedFetcher(products))[0]
    assert r["result"] == "PASS" and r["banner_check"]["sort_check"]["status"] == "MATCH"


def test_a_banner_whose_deal_has_no_rule_records_no_sort_check_and_asks_for_no_sorted_page(tmp_path):
    fetcher = SortedFetcher([product(price=500, discount=10)])
    info = {"brands_mentioned": ["new balance", "under armour"], "deal_offered": "UP TO 60% OFF", "target_gender": "men_and_women"}
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, fetcher, analyzer=lambda p: info)[0]
    assert "sort_check" not in r["banner_check"] and fetcher.sorted_calls == []


def test_a_listing_too_large_to_page_keeps_its_verdict_and_says_what_it_could_not_read(tmp_path):
    class TooBig(SortedFetcher):
        def __call__(self, slug, kind="curated", **k):
            if k.get("sort") == ":discount-desc":
                raise RuntimeError("listing a-1: HTTP 403 (outside the allowed range)")
            return super().__call__(slug, kind, **k)

    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, TooBig([product(price=500, discount=45)]))[0]
    assert r["result"] == "PASS"                                                     # decided by the brand / title / gender checks
    assert r["reason"] == "couldn't get the minimum discount since the page was too large"


# ---- the Discount Ranges filter answers the low end before any sorted page is fetched ---------------------

def _too_big(sort, page, size):
    raise RuntimeError("listing a-1: HTTP 403 (outside the allowed range)")


def test_the_discount_filter_settles_a_min_deal_without_fetching_a_page():
    ranges = {10: 1000, 20: 1000, 30: 1000, 40: 1000, 50: 800}                     # everyone is discounted at least 40%
    r = ds.check("MIN. 50% OFF", 1000, _too_big, ranges)                            # needs >= 40; an unreachable page would have failed
    assert r["status"] == "MATCH" and r["observed"] == {"discount_filter": {"all_at_or_above": 40}}
    assert ds.check("MIN. 40% OFF", 1000, _too_big, ranges)["status"] == "MATCH"


def test_the_discount_filter_finds_products_below_what_the_banner_allows():
    ranges = {10: 1000, 20: 1000, 30: 990, 40: 900, 50: 700}                        # 10 products are under 30%
    r = ds.check("MIN. 40% OFF", 1000, _too_big, ranges)                            # needs >= 30
    assert r["status"] == "MISMATCH"
    assert "10 of 1000 products on the listing are discounted less than 30%" in r["reason"]


def test_a_need_between_two_steps_uses_whichever_side_is_certain():
    ranges = {10: 1000, 20: 1000, 30: 1000, 40: 900}
    assert ds.check("MIN. 50% OFF", 1000, _too_big, {**ranges, 40: 1000})["status"] == "MATCH"     # all >= 40 >= 35? need 40, exact
    assert ds.check("MIN. 45% OFF", 1000, _too_big, {**ranges, 40: 1000})["status"] == "MATCH"      # need 35: all >= 40
    r = ds.check("MIN. 45% OFF", 1000, _too_big, {**ranges, 30: 950, 40: 900})                        # need 35: 50 are under 30
    assert r["status"] == "MISMATCH"
    r = ds.check("MIN. 45% OFF", 1000, _too_big, ranges)                            # need 35: all >= 30 but 100 are under 40: undecided
    assert r["status"] == "UNCHECKED" and "too large" in r["reason"]                # so it falls back to the last page, which is unreachable


def test_an_undecided_filter_falls_back_to_the_last_page_when_it_can_be_reached():
    get = Sorted(CATALOGUE)
    r = ds.check("MIN. 45% OFF", 6, get, {10: 6, 20: 6, 30: 6, 40: 5})               # need 35; lowest is 30 -> mismatch via the page
    assert r["status"] == "MISMATCH" and r["observed"] == {"lowest_discount": 30}


def test_a_range_deal_uses_the_filter_for_its_low_end_too():
    r = ds.check("50-80% OFF", 1000, _too_big, {10: 1000, 20: 1000, 30: 1000, 40: 1000, 50: 900})
    assert r["status"] == "MATCH"


def test_the_discount_filter_is_read_from_the_listing_response():
    data = {"pagination": {"totalResults": 9}, "facets": [{"name": "Discount Ranges", "values": [
        {"name": "10% and above", "count": 9}, {"name": "50% and above", "count": 4}, {"name": "weird", "count": 1}]}]}
    assert lc.parse_listing("s", 0, data).discount_ranges == {10: 9, 50: 4}
    assert lc.parse_listing("s", 0, {"pagination": {}, "facets": []}).discount_ranges == {}
