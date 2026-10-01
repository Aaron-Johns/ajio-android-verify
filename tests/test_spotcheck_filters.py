"""Brand-filter reading on a real dump, deal/title matching, and the banner-vs-listing verdict."""

import pytest

from qa.compare import AliasMap
from qa.spotcheck import filters

ALIASES = AliasMap([["LEVI'S", "LEVIS"]])


@pytest.mark.parametrize("deal,title,expected", [
    ("MIN. 40% OFF*", "Min 40 Percent Off", True),
    ("UPTO 60% OFF", "Upto 60 Percent Off", True),
    ("UNDER ₹599", "Under Rs 599", True),
    ("MIN. 40% OFF*", "Min 70 Percent Off", False),
    ("UNDER ₹599", "Under Rs 999", False),
    ("MIN. 40% OFF", "Upto 40 Percent Off", False),
    ("", "Min 40 Percent Off", None),
    ("Min 40% off", None, None),
    # word-form variants that mean the same thing but don't match token-for-token - reported case:
    ("DELIVERY STARTING IN 30 MINS", "Delivery Starts in 30 Mins", True),
    ("DELIVERY STARTING IN 30 MINS", "Delivery Starts in 45 Mins", False),  # still catches an actual difference
    # "mins" (minutes) must never stem down to "min" (minimum) - they mean different things
    ("MIN 40% OFF", "Delivery in 30 Mins", False),
])
def test_deal_matches_title(deal, title, expected):
    assert filters.deal_matches_title(deal, title) is expected


def test_brand_comparison_is_alias_and_punctuation_tolerant():
    assert filters.same_brand("Levis", "LEVI'S", ALIASES)
    assert filters.same_brand("Dune London", "DUNE LONDON")
    assert not filters.same_brand("Dune", "Dune London")


# target_gender defaults to a recognized-but-unchecked reading (no listing_genders passed in most
# tests here means "no evidence" -> None, never blocking) so brand/title-focused tests below don't
# also have to think about gender; tests about gender itself override target_gender explicitly.
INFO = {"brands_mentioned": ["Dune London", "Guess"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}


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
    r = filters.verify_from_listing({"brands_mentioned": ["kiana"], "deal_offered": "MIN. 70% OFF*", "target_gender": "men_and_women"},
                                    "Min 70 Percent Off", {"Kiana House Of Fashion": 3}, ALIASES)
    assert r["result"] == "PASS" and r["brand_checks"][0]["match"] == "partial"


@pytest.mark.parametrize("text,expected", [
    ("men", "Men"), ("Men's", "Men"), ("MALE", "Men"),
    ("women", "Women"), ("Women's", "Women"), ("ladies", "Women"),
    ("Boys", "Boys"), ("boy", "Boys"),
    ("girls", "Girls"), ("Girl", "Girls"),
    ("Infants", "Infants"), ("toddler", "Infants"),
    ("kids", None), ("unisex", None), ("unclear", None), ("", None), (None, None),
])
def test_gender_key_normalizes_listing_facet_values_to_the_five_categories(text, expected):
    assert filters.gender_key(text) == expected


@pytest.mark.parametrize("reading", ["unclear", "", None, "kids", "unisex", "somethingelse"])
def test_gender_matches_is_inconclusive_when_the_banner_reading_is_not_one_of_the_seven_categories(reading):
    # Per spec: anything the banner reads as that isn't men/women/boys/girls/infants/
    # men_and_women/girls_and_boys forces the whole banner INCONCLUSIVE, not a skip.
    assert filters.gender_matches(reading, {"Men": 5}) == "INCONCLUSIVE"


def test_gender_matches_has_no_evidence_when_the_listing_has_no_recognized_facet_value():
    assert filters.gender_matches("men", {}) is None
    assert filters.gender_matches("men", {"Kids": 5}) is None   # "Kids" isn't one of the five categories


# Below, excluded categories are given a count but no total_results, which falls back to
# zero-tolerance (see test_gender_matches_excluded_categories_tolerate_noise_under_2_percent for
# the actual <2% behavior once a total is available).

def test_gender_matches_men_requires_men_and_allows_only_men_or_boys():
    assert filters.gender_matches("men", {"Men": 5}) is True
    assert filters.gender_matches("men", {"Men": 5, "Boys": 3}) is True
    assert filters.gender_matches("men", {"Men": 5, "Women": 1}) is False   # Women not allowed alongside a men's banner
    assert filters.gender_matches("men", {"Boys": 5}) is False              # Men itself must be present, not just allowed


def test_gender_matches_women_requires_women_and_allows_only_women_or_girls():
    assert filters.gender_matches("women", {"Women": 5}) is True
    assert filters.gender_matches("women", {"Women": 5, "Girls": 3}) is True
    assert filters.gender_matches("women", {"Women": 5, "Men": 1}) is False
    assert filters.gender_matches("women", {"Girls": 5}) is False


@pytest.mark.parametrize("reading,only_tag", [("boys", "Boys"), ("girls", "Girls"), ("infants", "Infants")])
def test_gender_matches_boys_girls_infants_require_their_tag_and_allow_the_other_two_kids_tags(reading, only_tag):
    assert filters.gender_matches(reading, {only_tag: 5}) is True
    assert filters.gender_matches(reading, {only_tag: 5, "Men": 1}) is False       # Men/Women still excluded
    other_kids = {"Boys", "Girls", "Infants"} - {only_tag}
    assert filters.gender_matches(reading, {only_tag: 5, **{t: 2 for t in other_kids}}) is True  # the other kid tags are unrestricted
    other = next(iter(other_kids))
    assert filters.gender_matches(reading, {other: 5}) is False                    # the required tag itself must be present


def test_gender_matches_men_and_women_requires_both_but_allows_extras():
    assert filters.gender_matches("men_and_women", {"Men": 5, "Women": 5}) is True
    assert filters.gender_matches("men_and_women", {"Men": 5, "Women": 5, "Boys": 2}) is True   # extras are fine
    assert filters.gender_matches("men_and_women", {"Men": 5}) is False
    assert filters.gender_matches("men_and_women", {"Women": 5}) is False


def test_gender_matches_girls_and_boys_only_excludes_men_and_women():
    assert filters.gender_matches("girls_and_boys", {"Girls": 5, "Boys": 5}) is True
    assert filters.gender_matches("girls_and_boys", {"Girls": 5}) is True         # Boys not required, just not excluded
    assert filters.gender_matches("girls_and_boys", {"Infants": 5}) is True       # unrestricted beyond Men/Women
    assert filters.gender_matches("girls_and_boys", {"Men": 5}) is False
    assert filters.gender_matches("girls_and_boys", {"Girls": 5, "Women": 1}) is False


def test_gender_matches_excluded_categories_tolerate_noise_under_2_percent():
    # A men's banner against a listing of 10,000 products where "Women" is a 1-item cross-tagging
    # artifact (0.01%) should still pass; at 2%+ it's real enough to fail.
    assert filters.gender_matches("men", {"Men": 9000, "Women": 1}, total_results=10_000) is True
    assert filters.gender_matches("men", {"Men": 9000, "Women": 199}, total_results=10_000) is True   # 1.99%, under
    assert filters.gender_matches("men", {"Men": 9000, "Women": 200}, total_results=10_000) is False  # 2.00%, not under
    assert filters.gender_matches("men", {"Men": 9000, "Women": 500}, total_results=10_000) is False  # 5%, clearly real


def test_gender_matches_falls_back_to_zero_tolerance_without_a_total():
    # Without total_results there's no way to compute a share, so any nonzero excluded count fails,
    # same as before the 2% rule existed.
    assert filters.gender_matches("men", {"Men": 9000, "Women": 1}) is False
    assert filters.gender_matches("men", {"Men": 9000, "Women": 1}, total_results=0) is False
    assert filters.gender_matches("men", {"Men": 9000, "Women": 1}, total_results=None) is False


def test_gender_matches_sums_facet_values_that_normalize_to_the_same_category():
    # "Girls" and "Junior Girls" both normalize to Girls and should combine before the % check.
    assert filters.gender_matches("men", {"Men": 9000, "Girls": 100, "Junior Girls": 99}, total_results=10_000) is True   # 199 < 2%
    assert filters.gender_matches("men", {"Men": 9000, "Girls": 100, "Junior Girls": 100}, total_results=10_000) is False  # 200 = 2%


def test_verify_from_listing_fails_when_the_banners_audience_is_not_in_the_listing():
    info = {**INFO, "target_gender": "women"}
    brands = {"DUNE LONDON": 12, "GUESS": 9}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", brands, ALIASES, {"Men": 50})
    assert r["result"] == "FAIL" and r["gender_matches"] is False and r["missing_brands"] == []


def test_verify_from_listing_passes_when_gender_also_matches():
    info = {**INFO, "target_gender": "women"}
    brands = {"DUNE LONDON": 12, "GUESS": 9}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", brands, ALIASES, {"Women": 50})
    assert r["result"] == "PASS" and r["gender_matches"] is True


def test_verify_from_listing_is_inconclusive_when_the_banner_reading_is_unclear():
    brands = {"DUNE LONDON": 12, "GUESS": 9}
    unclear = filters.verify_from_listing({**INFO, "target_gender": "unclear"}, "Min 40 Percent Off", brands, ALIASES, {"Men": 5})
    assert unclear["result"] == "INCONCLUSIVE" and unclear["gender_matches"] == "INCONCLUSIVE"


def test_verify_from_listing_ignores_gender_when_the_listing_has_no_recognized_gender_facet():
    brands = {"DUNE LONDON": 12, "GUESS": 9}
    no_facet = filters.verify_from_listing({**INFO, "target_gender": "women"}, "Min 40 Percent Off", brands, ALIASES)
    assert no_facet["result"] == "PASS" and no_facet["gender_matches"] is None


def test_verify_from_listing_fails_a_men_and_women_banner_against_a_single_gender_listing():
    brands = {"DUNE LONDON": 12, "GUESS": 9}
    r = filters.verify_from_listing({**INFO, "target_gender": "men_and_women"}, "Min 40 Percent Off", brands, ALIASES, {"Men": 5})
    assert r["result"] == "FAIL" and r["gender_matches"] is False


def test_verify_from_listing_passes_a_men_and_women_banner_against_a_listing_with_both():
    brands = {"DUNE LONDON": 12, "GUESS": 9}
    r = filters.verify_from_listing({**INFO, "target_gender": "men_and_women"}, "Min 40 Percent Off", brands, ALIASES, {"Men": 5, "Women": 3})
    assert r["result"] == "PASS" and r["gender_matches"] is True


# ---- brand-matching tolerance: ajio's-own-brand ignore, folding, substring match, master-list ignore ----

@pytest.mark.parametrize("name,expected", [
    ("AJIO", True), ("AJIO beauty", True), ("Ajio Beauty", True), ("AJIOGRAM", True),
    ("Guess", False), ("Dune London", False),
])
def test_is_ajio_own_brand(name, expected):
    assert filters.is_ajio_own_brand(name) is expected


def test_is_recognized_brand_checks_the_master_brand_list():
    assert filters.is_recognized_brand("Guess") is True
    assert filters.is_recognized_brand("Under Armour") is True
    assert filters.is_recognized_brand("Some Made Up Brand Xyz") is False


def test_ajio_own_brands_are_ignored_not_checked_against_the_filter():
    # Plain "AJIO" here, not "AJIO beauty" - that specific case forces its own AJIO_BEAUTY flag
    # (see test_ajio_beauty_skips_gender_and_flags_the_banner below) and would make this test's
    # PASS assertion fail for an unrelated reason.
    info = {"brands_mentioned": ["AJIO", "Guess"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"GUESS": 9}, ALIASES)
    assert r["result"] == "PASS" and r["banner_brands"] == ["Guess"]
    assert {"brand": "AJIO", "reason": "ajio_own_brand"} in r["ignored_brands"]


@pytest.mark.parametrize("name,expected", [
    ("AJIO beauty", True), ("Ajio Beauty", True), ("ajiobeauty", True),
    ("AJIO", False), ("AJIOGRAM", False), ("Beauty", False), ("Guess", False),
])
def test_is_ajio_beauty_brand(name, expected):
    assert filters.is_ajio_beauty_brand(name) is expected


def test_ajio_beauty_skips_gender_and_flags_the_banner_even_when_gender_would_have_passed():
    info = {"brands_mentioned": ["AJIO beauty", "Guess"], "deal_offered": "MIN. 40% OFF*", "target_gender": "women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"GUESS": 9}, ALIASES, {"Women": 50})
    assert r["result"] == "INCONCLUSIVE" and r["gender_matches"] == "AJIO_BEAUTY" and r["ajio_beauty_flag"] is True


def test_ajio_beauty_still_runs_brand_and_title_checks():
    # Only gender is skipped - a real brand/title mismatch on an AJIO beauty banner still shows up
    # in the detail (both routes land on INCONCLUSIVE either way here, but the underlying checks
    # that fed it are still the real ones, not skipped wholesale).
    info = {"brands_mentioned": ["AJIO beauty", "Guess"], "deal_offered": "MIN. 70% OFF*", "target_gender": "unisex"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"SOMEONE ELSE": 9}, ALIASES)
    assert r["title_matches_deal"] is False and r["missing_brands"] == ["Guess"]
    assert r["ajio_beauty_flag"] is True and r["result"] == "INCONCLUSIVE"


def test_accented_brand_names_are_folded_to_plain_ascii_for_comparison():
    assert filters.same_brand("RENÉE", "RENEE")
    assert filters.match_brand("RENÉE", ["RENEE", "Other"]) == ("RENEE", "exact")
    info = {"brands_mentioned": ["RENÉE"], "deal_offered": "UPTO 25% OFF", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Upto 25 Percent Off", {"RENEE": 40})
    assert r["result"] == "PASS" and r["missing_brands"] == []


def test_substring_match_catches_names_that_share_no_whole_word():
    # "Peaches" isn't a whole-word match for the real brand "Pspeaches" (different words entirely),
    # but it's a substring of it - vision reading only part of a stylized/glued brand name.
    assert filters.match_brand("Peaches", {"Pspeaches": 12}) == ("Pspeaches", "substring")
    assert filters.match_brand("Pspeaches Official", {"Peaches": 3}) == ("Peaches", "substring")
    assert filters.match_brand("Totally Unrelated", {"Dune London": 12}) == (None, None)


def test_two_adjacent_brands_read_as_one_string_still_match_either_ones_filter_option():
    # Nyrika and Acai are two distinct real brands; vision can return them as one "Nyrika Acai" string
    # when their logos sit side by side. The filter option's whole word is still inside that string.
    assert filters.match_brand("Nyrika Acai", ["Nyrika"]) == ("Nyrika", "partial")
    assert filters.match_brand("Nyrika Acai", ["Acai"]) == ("Acai", "partial")


def test_a_partial_brand_name_is_still_found_via_substring_when_the_filter_has_it():
    # "Peaches" is a real (if partial) read of the actual brand "Pspeaches" - the substring rule finds
    # it directly, so this should never even reach the master-list-ignore fallback below.
    info = {"brands_mentioned": ["Peaches"], "deal_offered": "MIN. 70% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 70 Percent Off", {"Pspeaches": 12})
    assert r["result"] == "PASS" and r["missing_brands"] == [] and r["brand_checks"][0]["match"] == "substring"


def test_a_missing_brand_not_in_the_master_list_is_ignored_not_failed():
    # A name that isn't in AJIO's own master brand list at all, and isn't a substring/partial match for
    # anything in this listing's filter either - most likely a vision misread, not a real feed defect.
    # An empty listing filter also keeps this test clear of the separate exhaustive-brand-list rule below.
    info = {"brands_mentioned": ["Zzqorblefloop"], "deal_offered": "MIN. 70% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 70 Percent Off", {})
    assert r["result"] == "PASS" and r["missing_brands"] == []
    assert {"brand": "Zzqorblefloop", "reason": "not_in_master_brand_list"} in r["ignored_brands"]


def test_a_missing_brand_that_is_in_the_master_list_still_fails():
    info = {"brands_mentioned": ["Guess"], "deal_offered": "MIN. 70% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 70 Percent Off", {"Other Brand": 5})
    assert r["result"] == "FAIL" and r["missing_brands"] == ["Guess"]


# ---- "& more" vs. a closed brand list ----

def test_a_banner_with_no_more_qualifier_is_read_as_a_closed_brand_list():
    info = {"brands_mentioned": ["Guess"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"GUESS": 9, "Unrelated Brand": 3})
    assert r["result"] == "FAIL" and r["extra_brands"] == ["Unrelated Brand"] and r["brand_list_exhaustive"] is True


def test_a_banner_that_says_and_more_allows_extra_brands_in_the_listing():
    info = {"brands_mentioned": ["Guess"], "deal_offered": "MIN. 40% OFF*", "more_brands_than_named": True, "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"GUESS": 9, "Unrelated Brand": 3})
    assert r["result"] == "PASS" and "extra_brands" not in r and r["brand_list_exhaustive"] is False


def test_ajios_own_brand_in_the_listing_does_not_count_as_an_extra():
    info = {"brands_mentioned": ["Guess"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"GUESS": 9, "AJIO": 20})
    assert r["result"] == "PASS" and r["extra_brands"] == []


def test_no_brands_named_never_triggers_the_closed_list_check():
    info = {"brands_mentioned": [], "deal_offered": "MIN. 40% OFF*"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"GUESS": 9, "Unrelated Brand": 3})
    assert r["brand_list_exhaustive"] is False and "extra_brands" not in r


# ---- single-brand banner: listing brands that contain the banner's brand name aren't "extra" ----

def test_single_brand_banner_does_not_flag_the_brands_own_family_variants_as_extra():
    info = {"brands_mentioned": ["Calvin Klein"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off",
                                    {"Calvin Klein": 9, "Calvin Klein Sports": 4, "Calvin Klein Jeans": 2})
    assert r["result"] == "PASS" and r["extra_brands"] == []


def test_single_brand_banner_still_flags_a_genuinely_unrelated_brand_as_extra():
    info = {"brands_mentioned": ["Calvin Klein"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off", {"Calvin Klein Sports": 4, "Tommy Hilfiger": 3})
    assert r["result"] == "FAIL" and r["extra_brands"] == ["Tommy Hilfiger"]


def test_the_family_variant_exemption_only_applies_when_exactly_one_brand_is_named():
    info = {"brands_mentioned": ["Calvin Klein", "Guess"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}
    r = filters.verify_from_listing(info, "Min 40 Percent Off",
                                    {"Calvin Klein": 9, "GUESS": 5, "Calvin Klein Sports": 4})
    assert r["result"] == "FAIL" and r["extra_brands"] == ["Calvin Klein Sports"]
