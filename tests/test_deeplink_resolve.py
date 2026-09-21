import json
from pathlib import Path

import pytest

from qa import deeplink_resolve as dl

SAMPLE = Path(__file__).resolve().parent.parent / "analysis" / "traffic_capture" / "home_theme_response_sample.json"

# raw, type, brand, category, identifier, host
CASES = [
    ("https://www.ajio.com/s/new30-166553", dl.PLP, None, None, "166553", "www.ajio.com"),
    ("https://www.ajio.com/s/4hoursdelivery-160865?isPDBanner=true", dl.PLP, None, None, "160865", "www.ajio.com"),
    ("www.ajio.com/c/clearance-store-1789044034", dl.CATEGORY, None, "clearance store", "1789044034", "www.ajio.com"),
    ("/c/830216", dl.CATEGORY, None, None, "830216", None),
    ("/men-jeans/c/830216", dl.CATEGORY, None, "men jeans", "830216", None),
    ("https://www.ajio.com/b/levis-2003", dl.BRAND, "levis", None, "2003", "www.ajio.com"),
    ("https://www.ajio.com/brand/nike", dl.BRAND, "nike", None, None, "www.ajio.com"),
    ("/p/469012345_black", dl.PRODUCT, None, None, None, None),
    ("http://ajio.com/shop/ethnicwear-torso-tail", dl.CAMPAIGN, None, None, None, "ajio.com"),
    ("https://www.ajio.com/sections/trending-stories", dl.CAMPAIGN, None, None, None, "www.ajio.com"),
    ("https://www.ajio.com/offers", dl.CAMPAIGN, None, None, "offers", "www.ajio.com"),
    ("https://www.ajio.com/my-account/refer-and-earn", dl.OTHER, None, None, "my-account", "www.ajio.com"),
    ("https://www.ajio.com/", dl.HOME, None, None, None, "www.ajio.com"),
    ("https://luxe.ajio.com/c/watches-77", dl.CATEGORY, None, "watches", "77", "luxe.ajio.com"),
    ("ajioapps://www.ajio.com/b/nike-1", dl.BRAND, "nike", None, "1", "www.ajio.com"),
    ("https://apply.scapia.cards/landing_page?utm_source=Ajio", dl.EXTERNAL, None, None, None, "apply.scapia.cards"),
    ("https://notajio.com/c/x-1", dl.EXTERNAL, None, None, None, "notajio.com"),
    ("https://ajio.com.evil.io/c/x-1", dl.EXTERNAL, None, None, None, "ajio.com.evil.io"),
]


@pytest.mark.parametrize("raw,type_,brand,category,identifier,host", CASES, ids=[c[0] for c in CASES])
def test_grammar(raw, type_, brand, category, identifier, host):
    t = dl.resolve(raw)
    assert (t.type, t.brand, t.category, t.identifier, t.host) == (type_, brand, category, identifier, host)
    assert t.resolved and t.raw == raw


def test_onelink_wrapper_is_unwrapped():
    url = "https://ajio.onelink.me/x1?deep_link_value=ajioapps%3A%2F%2Fwww.ajio.com%2Fb%2Fnike-1&af_dp=x"
    t = dl.resolve(url)
    assert (t.type, t.brand, t.identifier) == (dl.BRAND, "nike", "1")


@pytest.mark.parametrize("raw,reason", [
    (None, "empty_destination"),
    ("", "empty_destination"),
    ("   ", "empty_destination"),
    ("/reliance-sbi-tnc", "unrecognized_path"),
    ("https://www.ajio.com/supercash", "unrecognized_path"),
    ("https://ajio.onelink.me/abc", "wrapper_without_target"),
])
def test_unresolvable_links_are_unknown_with_reason(raw, reason):
    t = dl.resolve(raw)
    assert t.type == dl.UNKNOWN and not t.resolved and t.reason == reason


def test_every_live_sample_destination_parses():
    urls = [d["destination"] for d in json.loads(SAMPLE.read_text(encoding="utf-8"))["all_destination_urls"]]
    assert len(urls) == 327
    types = {dl.resolve(u).type for u in urls}
    assert {dl.PLP, dl.CATEGORY} <= types
    assert types <= {dl.PLP, dl.CATEGORY, dl.BRAND, dl.CAMPAIGN, dl.EXTERNAL, dl.PRODUCT, dl.HOME, dl.OTHER, dl.UNKNOWN}
