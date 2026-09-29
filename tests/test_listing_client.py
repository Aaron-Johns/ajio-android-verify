"""Listing client: response parsing and retry behaviour, with a fake session (no network)."""
import pytest

from qa import listing_client as lc
from qa.feed_client import FeedError

DATA = {
    "metaElementData": [{"name": "description"}, {"name": "pageTitle", "content": "Min 70 Percent Off"}],
    "pagination": {"pageSize": 25, "currentPage": 0, "totalResults": 29504, "totalPages": 1181},
    "facets": [{"name": "Gender", "values": [{"name": "Women", "count": 5}]},
               {"name": "Brands", "values": [{"name": "Arrabi", "count": 46}, {"name": "NETPLAY", "count": 675}]}],
    "products": [{"code": "1"}, {"code": "2"}],
    "currentQuery": {"query": {"value": ":relevance:curated:true"}},
}


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text, self.headers, self.content = status, body, "", {}, b"{}"

    def json(self):
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        return self.responses.pop(0)


def test_parse_listing_extracts_title_totals_brands_and_products():
    lst = lc.parse_listing("min70", 0, DATA)
    assert lst.title == "Min 70 Percent Off" and lst.total_results == 29504 and len(lst.products) == 2
    assert lst.brands == {"Arrabi": 46, "NETPLAY": 675} and lst.facets == {"Gender": 1, "Brands": 2}
    assert lst.genders == {"Women": 5}


@pytest.mark.parametrize("raw,expected", [
    ("https://ajio.com/s/min70percentoffcurated-402881", ("curated", "min70percentoffcurated-402881")),
    ("www.ajio.com/c/clearance-store-1789044034", ("category", "clearance-store-1789044034")),
    ("https://www.ajio.com/clearance-store/c/clearance-store-1789044034", ("category", "clearance-store-1789044034")),
    ("http://ajio.com/shop/ethnicwear-torso-tail", None),
    ("https://apply.scapia.cards/landing_page?x=1", None),
    ("", None), (None, None),
])
def test_listing_target_picks_the_endpoint_variant(raw, expected):
    assert lc.listing_target(raw) == expected


def test_category_listing_uses_the_slug_in_the_path_and_falls_back_to_freetext_title():
    s = Session(Resp(200, {**DATA, "metaElementData": [{"name": "pageTitle"}], "freeTextSearch": "Clearance Store"}))
    lst = lc.fetch_listing("clearance-store-1789044034", kind="category", session=s, sleep=lambda x: None)
    url = s.calls[0][0]
    assert "/products/category/clearance-store-1789044034?" in url and "curatedid" not in url
    assert lst.title == "Clearance Store"


def test_request_uses_the_slug_and_sends_no_authorization():
    s = Session(Resp(200, DATA))
    lc.fetch_listing("min70percentoffcurated-402881", session=s, sleep=lambda x: None)
    url, headers = s.calls[0]
    assert "curatedid=min70percentoffcurated-402881" in url and url.startswith(f"https://{lc.HOST}{lc.PATH}?")
    assert "authorization" not in {k.lower() for k in headers}


def test_retries_transient_errors_but_not_a_404():
    ok = lc.fetch_listing("s", session=Session(Resp(503), Resp(200, DATA)), sleep=lambda x: None)
    assert ok.title == "Min 70 Percent Off"
    with pytest.raises(FeedError, match="404"):
        lc.fetch_listing("s", session=Session(Resp(404)), sleep=lambda x: None)


def test_product_rows_flatten_the_server_record():
    p = {"code": "7", "fnlColorVariantData": {"brandName": "JOMPERS"}, "name": "Suit", "price": {"value": 9310.0},
         "wasPriceData": {"value": 18999.0}, "discountPercent": "51% off", "offerPrice": {"value": 8810.0},
         "averageRating": 4.2, "ratingCount": 9, "verticalNameText": "Ethnic Wear", "segmentNameText": "Men", "url": "/u"}
    row = lc.product_row(p)
    assert row == {"code": "7", "brand": "JOMPERS", "name": "Suit", "price": 9310, "mrp": 18999, "discount_percent": 51,
                   "offer_price": 8810, "rating": 4.2, "rating_count": 9, "category": "Ethnic Wear", "gender": "Men", "url": "/u"}
    assert lc.product_row({"code": "8"})["price"] is None


# ---- luxe.ajio.com links live in the Luxe store, not the standard one --------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://luxe.ajio.com/s/allstarsearlyoffersmen-403091", "luxe"),
    ("luxe.ajio.com/s/x-1", "luxe"),
    ("https://LUXE.ajio.com/c/x-1", "luxe"),
    ("https://www.ajio.com/s/x-1", None),
    ("https://ajio.com/s/x-1", None),
    ("https://www.ajio.com/luxe/s/x-1", None),          # "luxe" only counts as the host
    ("", None), (None, None),
])
def test_listing_store_comes_from_the_links_host(raw, expected):
    assert lc.listing_store(raw) == expected


def test_a_luxe_link_asks_the_luxe_store_and_keeps_its_slug():
    s = Session(Resp(200, DATA))
    lc.fetch_listing("allstarsearlyoffersmen-403091", session=s, sleep=lambda x: None, store="luxe")
    url = s.calls[0][0]
    assert "store=luxe" in url and "store=rilfnl" not in url and "curatedid=allstarsearlyoffersmen-403091" in url


def test_the_standard_store_is_still_the_default():
    s = Session(Resp(200, DATA))
    lc.fetch_listing("min70percentoffcurated-402881", session=s, sleep=lambda x: None)
    assert "store=rilfnl" in s.calls[0][0]
