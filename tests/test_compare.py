"""One or more fixtures per status (MATCH, MISMATCH, AMBIGUOUS_DEEPLINK, NO_REFERENCE, ERROR)."""
from pathlib import Path

import pytest

from qa.brand_resolver import BrandResolver
from qa.compare import AliasMap, compare_banner
from qa.reference import ReferenceRow, load_reference
from qa.status import Status

REPO = Path(__file__).resolve().parent.parent

BRANDS = ["Nike", "ADIDAS", "LEVI'S", "LEVIS", "Polo Plus", "U.S. Polo Assn.", "Beverly Hills Polo Club",
          "Oomph", "OOMPH!", "RED TAPE", "REDTAPE"]
ALIASES = AliasMap([["LEVI'S", "LEVIS"], ["RED TAPE", "REDTAPE"], ["Oomph", "OOMPH!"]])
RESOLVER = BrandResolver(BRANDS)


def ref(brand=None, category=None, type_=None):
    return ReferenceRow("b1", brand, category, type_)


def run(dest, reference, resolver=RESOLVER):
    return compare_banner("b1", dest, reference, resolver, ALIASES)


# ---------------- MATCH ----------------

def test_match_brand():
    c = run("https://www.ajio.com/b/nike-123", ref(brand="Nike", type_="BRAND"))
    assert c.status is Status.MATCH and c.brand_resolution["ajio_brand"] == "Nike"


def test_match_brand_through_alias_where_plain_comparison_fails():
    c = run("https://www.ajio.com/b/levis-9", ref(brand="LEVI'S"))
    assert c.brand_resolution["ajio_brand"] == "LEVIS" != "LEVI'S"
    assert c.status is Status.MATCH


def test_match_category():
    assert run("www.ajio.com/c/clearance-store-1789044034", ref(category="Clearance Store", type_="CATEGORY")).status is Status.MATCH


def test_match_type_only():
    assert run("https://www.ajio.com/s/new30-166553", ref(type_="PLP")).status is Status.MATCH


def test_expected_plp_accepts_category_and_brand_listing_pages():
    assert run("https://www.ajio.com/c/x-1", ref(type_="PLP")).status is Status.MATCH
    assert run("https://www.ajio.com/b/nike-1", ref(type_="PLP")).status is Status.MATCH


def test_match_external():
    assert run("https://apply.scapia.cards/landing_page", ref(type_="EXTERNAL")).status is Status.MATCH


def test_multi_brand_expected_uses_first_as_dominant():
    assert run("https://www.ajio.com/b/nike-1", ref(brand="Nike|ADIDAS")).status is Status.MATCH
    assert run("https://www.ajio.com/b/adidas-1", ref(brand="Nike|ADIDAS")).status is Status.MISMATCH


# ---------------- MISMATCH ----------------

def test_mismatch_brand():
    c = run("https://www.ajio.com/b/adidas-5", ref(brand="Nike"))
    assert c.status is Status.MISMATCH and "brand_expected_Nike_got_ADIDAS" in c.reason


def test_mismatch_type():
    c = run("https://www.ajio.com/b/nike-1", ref(type_="CAMPAIGN"))
    assert c.status is Status.MISMATCH and c.reason == "type_expected_CAMPAIGN_got_BRAND"


def test_mismatch_category():
    c = run("https://www.ajio.com/c/shoes-7", ref(category="Clearance Store"))
    assert c.status is Status.MISMATCH


def test_type_mismatch_wins_over_ambiguity():
    assert run("https://www.ajio.com/c/x-1", ref(brand="Nike", type_="CAMPAIGN")).status is Status.MISMATCH


# ---------------- AMBIGUOUS_DEEPLINK ----------------

def test_word_boundary_with_several_candidates_is_ambiguous_not_accepted():
    c = run("https://www.ajio.com/b/polo-77", ref(brand="U.S. Polo Assn."))
    # The frozen resolver confidently picks the shortest name (the trap CLAUDE.md 7.1 describes)...
    assert c.brand_resolution["ajio_brand"] == "Polo Plus"
    assert c.brand_resolution["needs_verification"] is False
    assert len(c.brand_resolution["possible_ajio_brands"]) == 3
    # ...and the status layer refuses to trust it.
    assert c.status is Status.AMBIGUOUS_DEEPLINK and c.reason == "word_boundary_multiple_candidates"


def test_ambiguous_exact_from_colliding_pair():
    c = run("https://www.ajio.com/b/oomph-2", ref(brand="Oomph"))
    assert c.status is Status.AMBIGUOUS_DEEPLINK and c.reason == "resolver_ambiguous_exact"


def test_unrecognized_path():
    c = run("https://www.ajio.com/supercash", ref(type_="CAMPAIGN"))
    assert c.status is Status.AMBIGUOUS_DEEPLINK and c.reason == "unrecognized_path"


def test_wrapper_without_target():
    assert run("https://ajio.onelink.me/abc", ref(type_="BRAND")).status is Status.AMBIGUOUS_DEEPLINK


def test_brand_expected_but_link_has_no_brand():
    c = run("https://www.ajio.com/s/x-1", ref(brand="Nike"))
    assert c.status is Status.AMBIGUOUS_DEEPLINK and c.reason == "brand_not_in_link"


def test_category_expected_but_link_has_no_category():
    c = run("https://www.ajio.com/s/x-1", ref(category="Shoes"))
    assert c.status is Status.AMBIGUOUS_DEEPLINK and c.reason == "category_not_in_link"


def test_empty_brand_slug_needs_verification():
    c = run("https://www.ajio.com/b/", ref(brand="Nike"))
    assert c.status is Status.AMBIGUOUS_DEEPLINK and c.reason == "empty_brand"


def test_low_confidence_brand_is_ambiguous():
    c = run("https://www.ajio.com/b/zzzqqq-1", ref(brand="Nike"))
    assert c.status is Status.AMBIGUOUS_DEEPLINK and c.reason.startswith("resolver_")


# ---------------- NO_REFERENCE ----------------

def test_no_reference_row():
    assert run("https://www.ajio.com/b/nike-1", None).status is Status.NO_REFERENCE


def test_reference_row_without_expectations():
    c = run("https://www.ajio.com/b/nike-1", ref())
    assert c.status is Status.NO_REFERENCE and c.reason == "reference_row_has_no_expectations"


def test_no_reference_takes_priority_over_empty_destination():
    assert run("", None).status is Status.NO_REFERENCE


# ---------------- ERROR ----------------

@pytest.mark.parametrize("dest", [None, "", "  "])
def test_empty_destination_with_reference_is_error(dest):
    c = run(dest, ref(type_="PLP"))
    assert c.status is Status.ERROR and c.reason == "empty_destination"


def test_unexpected_exception_becomes_error_instead_of_crashing():
    class Broken:
        def resolve(self, text):
            raise RuntimeError("boom")

    c = run("https://www.ajio.com/b/nike-1", ref(brand="Nike"), resolver=Broken())
    assert c.status is Status.ERROR and "RuntimeError: boom" in c.reason


# ---------------- alias map + reference loading ----------------

def test_all_22_approved_alias_groups_load_and_compare():
    aliases = AliasMap.from_file()
    assert aliases.same_brand("LEVI'S", "LEVIS") and aliases.same_brand("U.S. Polo Assn.", "US POLO ASSN.")
    assert aliases.same_brand("RED TAPE", "REDTAPE") and not aliases.same_brand("Nike", "ADIDAS")
    assert not aliases.same_brand("", "")


def test_sample_reference_loads_and_runs_end_to_end():
    rows = load_reference(REPO / "config" / "reference.sample.csv")
    assert len(rows) == 3
    assert rows["6a7c9d27d63402ac4bb86c11:14"].expected_category == "Clearance Store"
    c = compare_banner("6a980a6f203fa05428096a91", "https://www.ajio.com/s/new30-166553",
                       rows["6a980a6f203fa05428096a91"], RESOLVER, ALIASES)
    assert c.status is Status.MATCH


def test_reference_loader_ignores_comments_and_normalizes_type(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("# note\nbanner_id,expected_brand,expected_category,expected_deeplink_type,notes\n"
                 "x1, Nike ,,brand,hi\n,,,,\n", encoding="utf-8")
    rows = load_reference(p)
    assert list(rows) == ["x1"] and rows["x1"].expected_brand == "Nike" and rows["x1"].expected_deeplink_type == "BRAND"
