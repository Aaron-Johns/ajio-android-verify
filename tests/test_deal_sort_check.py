"""The banner-deal check (qa/deal_sort_check.py): which deals it understands, what each one asks of the listing sorted by price
(price claims) or of its Discount Ranges filter (discount claims), and that data it can't get never becomes a failure.
Nothing here touches the network."""
import pytest

from qa import deal_sort_check as ds
from qa import listing_client as lc
from tests.test_feed_verify import Fetcher, banner, run


def product(price=None, discount=None):
    return {"price": {"value": price}, "discountPercent": f"{discount}% off" if discount is not None else None}


class Sorted:
    """A listing of `products` served in the order the sort asks for; records what was asked."""

    def __init__(self, products):
        self.products, self.asked = products, []

    def __call__(self, sort, page, size):
        self.asked.append((sort, page, size))
        key = {":prce-asc": lambda p: p["price"]["value"], ":prce-desc": lambda p: -p["price"]["value"]}[sort]
        ordered = sorted(self.products, key=key)
        return lc.Listing("s", "t", len(ordered), page, products=ordered[page * size:(page + 1) * size])


CATALOGUE = [product(price=p, discount=d) for p, d in ((161, 46), (390, 70), (420, 70), (943, 41), (1500, 30), (5760, 52))]


@pytest.mark.parametrize("text,rule", [
    ("MIN. 45% OFF*", ("min_discount", 45)),
    ("Minimum 50% off", ("min_discount", 50)),
    ("MIN. 50% OFF + EXTRA 30% OFF", ("min_discount", 50)),
    ("20-70% OFF*", ("discount_range", 20)),
    ("FROM 20% TO 70% OFF", ("discount_range", 20)),
    ("UNDER ₹799", ("under_price", 799)),
    ("UP TO ₹999", ("under_price", 999)),                                      # an up-to PRICE is a cap, like under
    ("UPTO RS. 1,499*", ("under_price", 1499)),
    ("Under Rs. 1,299", ("under_price", 1299)),
    ("STARTING AT RS 399", ("starting_price", 399)),
    ("STARTING FROM ₹1,499", ("starting_price", 1499)),
])
def test_the_deals_it_understands(text, rule):
    assert ds.parse_deal(text) == rule


@pytest.mark.parametrize("text", [None, "", "UP TO 60% OFF*", "UPTO 70% OFF", "Up to 50% off on 2 items", "UP TO 60% OFF* + EXTRA 30% OFF*",
                                  "UP TO ₹500 OFF", "UP TO RS. 500/- OFF", "UP TO 3 ITEMS", "FLAT 50% OFF", "NEW ARRIVALS", "BUY 1 GET 1"])
def test_other_wording_has_no_rule_and_is_left_alone(text):
    get = Sorted(CATALOGUE)
    assert ds.parse_deal(text) is None and ds.check(text, get) is None and get.asked == []


# ---- prices: the listing sorted by price ---------------------------------------------------------------

def test_under_price_looks_at_the_dearest_item():
    get = Sorted(CATALOGUE)
    assert ds.check("UNDER ₹5761", get)["status"] == "MATCH"
    assert ds.check("UNDER ₹5759", get)["status"] == "MATCH"                   # one rupee of slack
    r = ds.check("UNDER ₹799", get)
    assert r["status"] == "MISMATCH" and "dearest item on the listing is Rs 5760" in r["reason"]
    assert get.asked[-1] == (":prce-desc", 0, ds.PAGE_SIZE)


def test_starting_price_looks_at_the_cheapest_item():
    get = Sorted(CATALOGUE)
    assert ds.check("STARTING AT ₹161", get)["status"] == "MATCH"
    assert ds.check("STARTING AT ₹160", get)["status"] == "MISMATCH"           # strictly x: no slack either way
    assert ds.check("STARTING AT ₹162", get)["status"] == "MISMATCH"
    r = ds.check("STARTING AT ₹399", get)
    assert r["status"] == "MISMATCH" and "cheapest item on the listing is Rs 161" in r["reason"]
    assert get.asked[-1] == (":prce-asc", 0, ds.PAGE_SIZE)


def test_a_sort_that_cannot_be_loaded_is_unchecked_not_a_failure():
    def broken(sort, page, size):
        raise RuntimeError("HTTP 503")
    r = ds.check("UNDER ₹799", broken)
    assert r["status"] == "UNCHECKED" and "couldn't load the sorted listing" in r["reason"] and r["note"]
    assert ds.check("UNDER ₹799", lambda s, p, n: lc.Listing("s", "t", 0, 0, products=[]))["status"] == "UNCHECKED"


def test_every_price_result_carries_a_one_line_summary_for_the_detail_view():
    get = Sorted(CATALOGUE)
    assert ds.check("UNDER ₹6000", get)["summary"] == "True"
    assert ds.check("UNDER ₹799", get)["summary"] == "False"                         # the why is in the reason
    assert ds.check("STARTING AT ₹161", get)["summary"] == "True"
    assert ds.check("UP TO ₹6000", get)["summary"] == "True"

    def broken(sort, page, size):
        raise RuntimeError("HTTP 503")
    assert ds.check("UNDER ₹799", broken)["summary"] == "Couldn't check"


# ---- discounts: the Discount Ranges filter, floor = x - 10 ----------------------------------------------

def no_page(sort, page, size):
    raise AssertionError("a discount claim must be answered from the filter counts, never a sorted page")


SAME = {10: 900, 20: 900, 30: 900, 40: 700, 50: 500}          # nothing sits between 10% and 30%


def test_a_floor_step_that_holds_as_many_products_as_every_lower_step_is_correct():
    r = ds.check("MIN. 45% OFF", no_page, SAME)                                     # floor 35 -> the 30% step
    assert r["status"] == "MATCH" and r["observed"]["step"] == 30 and r["observed"]["count"] == 900
    assert r["summary"] == "True"
    assert ds.check("MIN. 40% OFF", no_page, SAME)["status"] == "MATCH"             # floor 30 -> the 30% step exactly
    assert ds.check("MIN. 50% OFF", no_page, SAME)["observed"]["step"] == 40        # floor 40


def test_a_lower_step_holding_more_products_means_products_are_under_the_floor():
    ranges = {10: 1000, 20: 1000, 30: 900, 40: 700}                                  # 100 products sit between 20% and 30%
    r = ds.check("MIN. 45% OFF", no_page, ranges)
    assert r["status"] == "MISMATCH" and r["observed"]["lower_step"] == 20
    assert "100 products on the listing are discounted less than 30%" in r["reason"]
    assert "the 20% and above filter holds 1000, the 30% and above filter only 900" in r["reason"]
    assert r["summary"] == "False"


def test_the_nearest_lower_step_with_extra_products_is_the_one_named():
    ranges = {10: 1000, 20: 950, 30: 900}                                            # both lower steps hold more
    r = ds.check("MIN. 40% OFF", no_page, ranges)
    assert r["status"] == "MISMATCH" and r["observed"]["lower_step"] == 20 and "50 products" in r["reason"]


def test_a_range_deal_uses_its_low_end_and_never_caps_the_top():
    assert ds.check("20-70% OFF", no_page, {10: 1000, 20: 800, 30: 700})["status"] == "MATCH"   # floor 10 -> the 10% step, nothing below to compare
    assert ds.check("40-45% OFF", no_page, SAME)["status"] == "MATCH"                # floor 30
    assert ds.check("50-60% OFF", no_page, {10: 1000, 20: 900, 30: 800, 40: 700})["status"] == "MISMATCH"   # floor 40: lower steps hold more


def test_the_lowest_step_has_nothing_below_it_to_compare():
    r = ds.check("20-70% OFF", no_page, {10: 1000, 20: 800, 30: 700})                # floor 10 is the lowest step
    assert r["status"] == "MATCH" and r["summary"] == "True"
    assert ds.check("MIN. 5% OFF", no_page, SAME)["summary"] == "True"


def test_a_floor_under_the_filters_first_step_or_no_filter_at_all_is_unchecked_never_a_failure():
    r = ds.check("MIN. 25% OFF", no_page, {20: 100, 30: 100})                        # floor 15, first step 20
    assert r["status"] == "UNCHECKED" and "starts above 15%" in r["reason"]
    r = ds.check("MIN. 45% OFF", no_page, {})
    assert r["status"] == "UNCHECKED" and "no discount filter" in r["reason"] and "note" not in r
    assert ds.check("MIN. 45% OFF", no_page, None)["status"] == "UNCHECKED"


def test_a_saved_discount_check_from_an_earlier_version_is_not_summarised():
    old = {"rule": "min_discount", "deal": "MIN. 40% OFF*", "observed": {"lowest_discount": 30}, "status": "MATCH", "reason": ""}
    assert ds.summary(old) == ""


def test_the_discount_filter_is_read_from_the_listing_response():
    data = {"pagination": {"totalResults": 9}, "facets": [{"name": "Discount Ranges", "values": [
        {"name": "10% and above", "count": 9}, {"name": "50% and above", "count": 4}, {"name": "weird", "count": 1}]}]}
    assert lc.parse_listing("s", 0, data).discount_ranges == {10: 9, 50: 4}
    assert lc.parse_listing("s", 0, {"pagination": {}, "facets": []}).discount_ranges == {}


# ---- inside the pipeline ---------------------------------------------------------------------------------

class SortedFetcher(Fetcher):
    """Plain fetches get the listing; sorted ones get the products in the order asked."""

    def __init__(self, products):
        listing = lc.Listing("a-1", "Under Rs 799", len(products), 0, {"NEW BALANCE": 5, "Under Armour": 9}, {},
                             {"Brands": 2}, products)
        super().__init__({"a-1": listing})
        self.products = products

    def __call__(self, slug, kind="curated", **k):
        base = super().__call__(slug, kind, **k)
        if not k.get("sort"):
            return base
        return Sorted(self.products)(k["sort"], k.get("page", 0), k.get("page_size") or 25)


def info(deal):
    return {"brands_mentioned": ["new balance", "under armour"], "deal_offered": deal, "target_gender": "men_and_women"}


def test_a_price_the_sorted_listing_contradicts_fails_the_banner_with_a_reason(tmp_path):
    products = [product(price=p) for p in (300, 900, 2500)]
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, SortedFetcher(products), analyzer=lambda p: info("UNDER ₹799"))[0]
    assert r["result"] == "FAIL" and "dearest item on the listing is Rs 2500" in r["reason"]
    assert r["banner_check"]["sort_check"]["status"] == "MISMATCH"


def test_a_price_the_sorted_listing_supports_still_passes(tmp_path):
    products = [product(price=p) for p in (300, 500, 799)]
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, SortedFetcher(products), analyzer=lambda p: info("UNDER ₹799"))[0]
    assert r["result"] == "PASS" and r["banner_check"]["sort_check"]["status"] == "MATCH"


def test_a_discount_deal_is_judged_from_the_listings_filter_counts_and_asks_for_no_sorted_page(tmp_path):
    fetcher = SortedFetcher([product(price=500, discount=10)])
    fetcher.table["a-1"].title = "Min 40 Percent Off"                                          # so the title check agrees with the deal
    fetcher.table["a-1"].discount_ranges = {10: 100, 20: 100, 30: 100, 40: 60}
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, fetcher, analyzer=lambda p: info("MIN. 40% OFF*"))[0]
    assert r["result"] == "PASS" and r["banner_check"]["sort_check"]["status"] == "MATCH" and fetcher.sorted_calls == []
    fetcher.table["a-1"].discount_ranges = {10: 100, 20: 100, 30: 80, 40: 60}                 # 20 products fall between 20% and 30%
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path / "again", fetcher, analyzer=lambda p: info("MIN. 40% OFF*"))[0]
    assert r["result"] == "FAIL" and "20 products on the listing are discounted less than 30%" in r["reason"]


def test_a_deal_with_no_rule_records_no_sort_check(tmp_path):
    fetcher = SortedFetcher([product(price=500, discount=10)])
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, fetcher, analyzer=lambda p: info("UP TO 60% OFF*"))[0]
    assert "sort_check" not in r["banner_check"] and fetcher.sorted_calls == []


def test_a_result_saved_before_the_summary_existed_is_given_one_when_the_run_is_read_back():
    from web import runner
    saved = {"result": "PASS", "reason": "", "banner_check": {"sort_check": {
        "rule": "under_price", "deal": "UNDER ₹899*", "observed": {"highest_price": 899}, "status": "MATCH", "reason": ""}}}
    assert runner._explained(saved)["banner_check"]["sort_check"]["summary"] == "True"
    assert "summary" not in saved["banner_check"]["sort_check"]                       # the saved dict itself is untouched
    longer = {**saved["banner_check"]["sort_check"], "summary": "Yes: an older, longer sentence"}
    assert runner._explained({**saved, "banner_check": {"sort_check": longer}})["banner_check"]["sort_check"]["summary"] == "True"   # re-worked
    assert runner._explained({"result": "PASS", "reason": "", "banner_check": {}}) == {"result": "PASS", "reason": "", "banner_check": {}}


# ---- the step above the floor must hold FEWER products ---------------------------------------------------

def test_the_step_above_the_floor_step_must_hold_strictly_fewer_products():
    equal_above = {10: 900, 20: 900, 30: 900, 40: 900, 50: 500}                       # MIN 45 -> floor step 30; 40% holds as many
    r = ds.check("MIN. 45% OFF", no_page, equal_above)
    assert r["status"] == "MISMATCH" and r["summary"] == "False"
    assert r["observed"]["above_step"] == 40 and r["observed"]["above_count"] == 900
    assert "no products on the listing are discounted between 30% and 40%" in r["reason"]
    assert "the 40% and above filter holds 900, the same as the 30% and above filter" in r["reason"]
    assert ds.check("MIN. 45% OFF", no_page, SAME)["status"] == "MATCH"               # 40% holds 700 < 900


def test_a_lower_step_with_extra_products_is_reported_before_the_step_above_is_looked_at():
    r = ds.check("MIN. 45% OFF", no_page, {10: 1000, 20: 1000, 30: 900, 40: 900})
    assert r["status"] == "MISMATCH" and "discounted less than 30%" in r["reason"] and "above_step" not in r["observed"]


def test_a_floor_on_the_top_step_has_no_step_above_to_compare():
    assert ds.check("MIN. 95% OFF", no_page, {10: 50, 20: 50, 80: 50, 90: 30})["status"] == "MATCH"    # floor 85 -> 80% step, 90% holds fewer
    assert ds.check("MIN. 100% OFF", no_page, {10: 50, 20: 50, 90: 50})["status"] == "MATCH"          # floor 90 -> the top step, nothing above
