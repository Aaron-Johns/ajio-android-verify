"""Verify a listing page against the banner that opened it, using the listing's own filters.

Three rules: every brand named in the banner must be an option in the listing's Brands filter
(as the listing's Brands facet lists them), the listing title must match the deal the
banner offers, and the listing's Gender facet must satisfy the banner's audience's required/
excluded categories (excluded categories tolerate up to 2% noise/cross-tagging - see
`gender_matches()`, a user-specified rule set, not inferred). If the banner mentions AJIO's beauty
vertical specifically, the gender check is skipped and the banner is flagged instead of scored,
since beauty products don't carry that split. Banner brands/deal/gender are read from the banner
image by the vision model.

Brand comparison tolerates real-world messiness: AJIO's own store-wide labels ("AJIO", "AJIO
beauty") aren't a brand to check and are ignored outright; names are compared with accented
characters folded to plain ASCII (RENÉE == renee); a substring/contains check is tried when exact
and whole-word matches fail; and a "missing" brand that isn't even in AJIO's own master brand list
is ignored rather than failed, since it's more likely a vision misread than a real feed defect.
"""
from __future__ import annotations

import re
import unicodedata

from qa.brand_resolver import load_brand_list, normalize_brand
from qa.compare import AliasMap, brand_key

_DEAL_NOISE = {"percent", "off", "and", "the", "on", "flat", "extra", "products", "product"}

# The closed set of gender categories a listing's Gender facet is checked against. Anything a
# listing's facet says that isn't one of these five is simply not recognized (normalize_gender_tag
# returns None for it) and dropped before matching - it neither helps nor hurts a verdict.
GENDER_CATEGORIES = {"Men", "Women", "Boys", "Girls", "Infants"}

# Normalizes a listing's Gender-facet value ("Men", "Junior Girls", "Infants", ...) to one of the
# five GENDER_CATEGORIES, so free-form facet text can be compared against the rules below.
_GENDER_WORDS = {
    "men": "Men", "man": "Men", "male": "Men", "mens": "Men", "gentlemen": "Men",
    "women": "Women", "woman": "Women", "female": "Women", "womens": "Women", "ladies": "Women",
    "boys": "Boys", "boy": "Boys",
    "girls": "Girls", "girl": "Girls",
    "infant": "Infants", "infants": "Infants", "toddler": "Infants", "toddlers": "Infants",
}

# What the banner's own target_gender reading (qa.spotcheck.vision's TARGET GENDER field) requires
# of the listing's Gender facet, once normalized to GENDER_CATEGORIES. Every rule is (required,
# excluded); any category in neither set is unrestricted (may or may not be present, uncapped -
# e.g. Boys for a "girls" banner). "required" categories must simply be present (no % threshold -
# not asked for). "excluded" categories aren't held to a hard zero - each is allowed to appear as
# noise/cross-tagging up to EXCLUDE_MAX_SHARE of the listing's total product count individually
# (not summed together) before it counts against the banner - see gender_matches().
# A banner reading that isn't a key here (including "unclear") isn't evaluated by this table at
# all - gender_matches() reports it as the sentinel "INCONCLUSIVE" instead.
EXCLUDE_MAX_SHARE = 0.02  # [D] user-specified: an excluded category must be under 2% of the listing's products

_BANNER_GENDER_RULES = {
    "men":            {"required": {"Men"},        "excluded": {"Women", "Girls", "Infants"}},
    "women":          {"required": {"Women"},      "excluded": {"Men", "Boys", "Infants"}},
    "boys":           {"required": {"Boys"},        "excluded": {"Men", "Women"}},
    "girls":          {"required": {"Girls"},       "excluded": {"Men", "Women"}},
    "infants":        {"required": {"Infants"},     "excluded": {"Men", "Women"}},
    "men_and_women":  {"required": {"Men", "Women"}, "excluded": set()},
    "girls_and_boys": {"required": set(),            "excluded": {"Men", "Women"}},
}


def _fold(text: str | None) -> str:
    """ASCII-fold a normalized brand name so accented spellings match their plain-ASCII form
    (RENÉE -> renee) - the listing's Brands filter is inconsistent about diacritics."""
    n = normalize_brand(text or "")
    return "".join(c for c in unicodedata.normalize("NFKD", n) if not unicodedata.combining(c))


def is_ajio_own_brand(name: str | None) -> bool:
    """AJIO's own store-wide labels (AJIO, AJIO beauty, AJIOGRAM, ...) aren't a brand to check
    against a listing's Brands filter."""
    return "ajio" in _fold(name).replace(" ", "")


def is_ajio_beauty_brand(name: str | None) -> bool:
    """Specifically AJIO's beauty vertical, not just any AJIO-labeled entry (see is_ajio_own_brand
    for the broader check used in brand matching). Beauty products don't carry the Men/Women/Boys/
    Girls/Infants split apparel listings do, so when this is mentioned the gender check is skipped
    entirely and the banner is flagged for a human look instead of auto-scored - user-specified."""
    folded = _fold(name).replace(" ", "")
    return "ajio" in folded and "beauty" in folded


_MASTER_BRAND_KEYS: set[str] | None = None


def _master_brand_keys() -> set[str]:
    global _MASTER_BRAND_KEYS
    if _MASTER_BRAND_KEYS is None:
        _MASTER_BRAND_KEYS = {_fold(b).replace(" ", "") for b in load_brand_list()}
    return _MASTER_BRAND_KEYS


def is_recognized_brand(name: str | None) -> bool:
    """True if AJIO's own master brand list (config/ajio_brand_names_deduped.json, ~7,000 names)
    recognises this name at all."""
    key = _fold(name).replace(" ", "")
    return bool(key) and key in _master_brand_keys()


def same_brand(a: str, b: str, aliases: AliasMap | None = None) -> bool:
    return (brand_key(a) == brand_key(b) or _fold(a).replace(" ", "") == _fold(b).replace(" ", "")
            or bool(aliases and aliases.same_brand(a, b)))


def match_brand(name: str, options, aliases: AliasMap | None = None) -> tuple[str | None, str | None]:
    """(option, 'exact'|'partial'|'substring'). partial = one name's whole words are all inside the
    other's (banners abbreviate); substring = a looser contains-check either way, for acronyms and
    extra wording that don't line up word-for-word (e.g. "DUNE" vs "Dune London Pvt Ltd")."""
    options = list(options)
    for n in options:
        if same_brand(name, n, aliases):
            return n, "exact"
    words = set(_fold(name).split())
    for n in options:
        other = set(_fold(n).split())
        if words and other and (words <= other or other <= words):
            return n, "partial"
    name_c = _fold(name).replace(" ", "")
    for n in options:
        other_c = _fold(n).replace(" ", "")
        if name_c and other_c and (name_c in other_c or other_c in name_c):
            return n, "substring"
    return None, None


_STEM_EXEMPT = {"mins"}  # "mins" (minutes) must never collapse into "min" (minimum) - different qualifiers


def _stem(word: str) -> str:
    """A deliberately crude suffix strip, not a real stemmer - just enough so that the tense
    variation feeds and titles disagree on ("Starts" vs "Starting") doesn't fail a token-set
    comparison that's otherwise saying the same thing. Only needs to be consistent between the two
    sides being compared, not linguistically correct - and must never conflate two short words that
    mean different things in deal text (see _STEM_EXEMPT)."""
    if word.isdigit() or len(word) <= 3 or word in _STEM_EXEMPT:
        return word
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def deal_tokens(text: str | None) -> set[str]:
    t = (text or "").lower().replace("₹", " rs ").replace("%", " percent ")
    t = re.sub(r"\bup\s+to\b", "upto", t)
    return {_stem(w) for w in normalize_brand(t).split() if w not in _DEAL_NOISE and w != "rs"}


def deal_matches_title(deal: str | None, title: str | None) -> bool | None:
    """None when either side is missing; else True if one side's key words (min/upto/under, amounts) contain the other's."""
    d, t = deal_tokens(deal), deal_tokens(title)
    if not d or not t:
        return None
    return d <= t or t <= d


def gender_key(text: str | None) -> str | None:
    """Normalize one listing Gender-facet value to a GENDER_CATEGORIES member ('Men'/'Women'/'Boys'/
    'Girls'/'Infants'), or None if it's not one of those five (e.g. a plain "Kids" tag, or anything
    _GENDER_WORDS doesn't recognize) - such values are dropped, not guessed at."""
    words = re.findall(r"[a-z]+", (text or "").lower())
    keys = {_GENDER_WORDS[w] for w in words if w in _GENDER_WORDS}
    return keys.pop() if len(keys) == 1 else None


def gender_matches(banner_gender: str | None, listing_genders: dict[str, int],
                   total_results: int | None = None) -> bool | None | str:
    """Checks the banner's target_gender reading against the listing's Gender facet and its product
    counts, per the fixed rule table in _BANNER_GENDER_RULES.

    listing_genders is the raw facet {name: count} (e.g. {"Women": 1540461, "Junior Girls": 1}) -
    values are summed per normalized category (so "Girls" and "Junior Girls" combine) before
    checking. total_results is the listing's overall product count, needed to turn an excluded
    category's raw count into a share of the listing.

    Returns:
      True / False - the banner's audience reading is one of the seven recognized categories, and
        the listing's Gender facet does / doesn't satisfy that category's required/excluded sets.
      None - the reading is recognized, but the listing exposes no facet value this table
        recognizes at all (nothing to check against); never blocks a PASS.
      "INCONCLUSIVE" - the banner's own reading isn't one of the seven recognized categories at all
        (includes "unclear", empty, or anything else) - the caller forces the whole banner
        INCONCLUSIVE rather than silently skipping the check.
    """
    rule_key = (banner_gender or "").strip().lower()
    rule = _BANNER_GENDER_RULES.get(rule_key)
    if rule is None:
        return "INCONCLUSIVE"
    counts: dict[str, int] = {}
    for name, count in (listing_genders or {}).items():
        key = gender_key(name)
        if key:
            counts[key] = counts.get(key, 0) + (count or 0)
    if not counts:
        return None
    if not (rule["required"] <= set(counts)):
        return False
    for category in rule["excluded"]:
        n = counts.get(category, 0)
        if n == 0:
            continue
        if not total_results:
            return False   # can't compute a share without a total - fall back to zero-tolerance
        if n / total_results >= EXCLUDE_MAX_SHARE:
            return False
    return True


def _split_ignored(raw_brands: list[str]) -> tuple[list[str], list[dict]]:
    """Drop AJIO's own store-wide labels before any matching; keep them as a visible receipt."""
    ignored = [{"brand": b, "reason": "ajio_own_brand"} for b in raw_brands if is_ajio_own_brand(b)]
    kept = [b for b in raw_brands if not is_ajio_own_brand(b)]
    return kept, ignored


def _finish(out: dict, brands: list[str], title_ok: bool | None, gender_ok: bool | None | str = None) -> dict:
    missing = [c["brand"] for c in out["brand_checks"] if not c["found"]]
    unrecognized = [b for b in missing if not is_recognized_brand(b)]
    out["missing_brands"] = [b for b in missing if b not in unrecognized]
    if unrecognized:
        out.setdefault("ignored_brands", []).extend({"brand": b, "reason": "not_in_master_brand_list"} for b in unrecognized)
    if "error" in out or (not brands and title_ok is None) or isinstance(gender_ok, str):
        out["result"] = "INCONCLUSIVE"
    elif title_ok is False or out["missing_brands"] or gender_ok is False or out.get("extra_brands"):
        out["result"] = "FAIL"
    elif title_ok is None or not brands:
        out["result"] = "INCONCLUSIVE"
    else:
        out["result"] = "PASS"
    return out


def verify_from_listing(banner_info: dict, listing_title: str | None, listing_brands: dict[str, int],
                        aliases: AliasMap | None = None, listing_genders: dict[str, int] | None = None,
                        total_results: int | None = None) -> dict:
    """Same verdict as verify_against_banner, but the Brands/Gender filters come from the server's listing response.

    Whether the listing may carry brands beyond the ones the banner names depends on the banner's own
    wording: "Nike, Puma & more" (more_brands_than_named) allows extras; a banner that names brands
    with no such qualifier is read as a closed list, and any other brand in the listing's filter (once
    AJIO's own entries are set aside) is a mismatch, not just a missing one.

    total_results (the listing's overall product count) feeds gender_matches()'s <2% noise
    tolerance on excluded categories - pass it whenever it's available.
    """
    raw_brand_names = [b for b in banner_info.get("brands_mentioned") or [] if b]
    ajio_beauty = any(is_ajio_beauty_brand(b) for b in raw_brand_names)
    brands, ignored = _split_ignored(raw_brand_names)
    deal = banner_info.get("deal_offered") or None
    banner_gender = banner_info.get("target_gender") or None
    # AJIO beauty doesn't carry a Men/Women/Boys/Girls/Infants split the way apparel does - skip the
    # gender check entirely and flag it (forces INCONCLUSIVE below) rather than score it - user-specified.
    gender_ok = "AJIO_BEAUTY" if ajio_beauty else gender_matches(banner_gender, listing_genders or {}, total_results)
    out: dict = {"source": "server", "banner_brands": brands, "banner_deal": deal, "listing_title": listing_title,
                 "title_matches_deal": deal_matches_title(deal, listing_title), "ajio_beauty_flag": ajio_beauty,
                 "banner_gender": banner_gender, "listing_genders": list((listing_genders or {}).keys()),
                 "gender_matches": gender_ok, "brand_checks": [], "ignored_brands": ignored,
                 "brand_list_exhaustive": bool(brands) and not banner_info.get("more_brands_than_named")}
    matched_options = set()
    for b in brands:
        matched, kind = match_brand(b, listing_brands, aliases)
        if matched:
            matched_options.add(matched)
        out["brand_checks"].append({"brand": b, "found": matched is not None, "matched_as": matched, "match": kind,
                                    "products": listing_brands.get(matched)})
    if out["brand_list_exhaustive"]:
        extra = [b for b in listing_brands if b not in matched_options and not is_ajio_own_brand(b)]
        if len(brands) == 1:
            # A single-brand banner ("Calvin Klein") shouldn't flag the listing's own brand-family
            # variants ("Calvin Klein Sports") as extras - only a listing brand that doesn't contain
            # the banner's brand name at all is genuinely extra. match_brand() above only records one
            # matched_options entry per banner brand, so without this a listing with several variants
            # of the same family would wrongly flag all but one of them - user-specified.
            main = _fold(brands[0]).replace(" ", "")
            extra = [b for b in extra if main not in _fold(b).replace(" ", "")]
        out["extra_brands"] = extra
    return _finish(out, brands, out["title_matches_deal"], gender_ok)
