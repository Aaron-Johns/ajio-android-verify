"""AJIO brand resolver, ported from inputs/brand_verify_combined.py.

[!] The matching logic (normalize_brand, build_brand_index, word_boundary_match, similarity,
fuzzy_find, resolve_brand) is copied verbatim and must not change; it is pinned by
inputs/resolver_fixtures.json (tests/test_brand_resolver.py). Only the file loading was adapted
(no prints / SystemExit) and the Gemini-file processing was dropped. Ambiguity handling (word-boundary matches with several candidate brands) is not
done here; the resolver only answers "which brands could this be" (CLAUDE.md 5).
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEDUPED_BRAND_FILE = REPO_ROOT / "config" / "ajio_brand_names_deduped.json"
ORIGINAL_BRAND_FILE = REPO_ROOT / "inputs" / "ajio_brand_names_fixed.json"

# Minimum fuzzy-match score (0-100) to accept automatically.
FUZZY_THRESHOLD = 85

# Minimum gap between the best and second-best fuzzy match, to avoid
# accepting an ambiguous fuzzy match.
FUZZY_MARGIN = 5


def normalize_brand(name):
    """Normalize a brand name for exact/fuzzy comparison.

    Examples:
        Adidas          -> adidas
        Armani-Exchange -> armani exchange
        H&M             -> h and m
    """
    if not isinstance(name, str):
        return ""

    name = unicodedata.normalize("NFKC", name)
    name = name.replace("&amp;", "&")
    name = name.casefold()
    name = name.replace("&", " and ")
    name = re.sub(r"[^\w\s]", " ", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def load_brand_list(path=None):
    """Load canonical brand names. Accepts either a flat list of strings
    or a list of objects with a "name"/"brand_name" field."""
    path = Path(path) if path else (DEDUPED_BRAND_FILE if DEDUPED_BRAND_FILE.exists() else ORIGINAL_BRAND_FILE)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = [item.get("name") or item.get("brand_name") for item in raw]

    if not isinstance(raw, list):
        raise ValueError(f"{path}: AJIO brand JSON must contain a JSON array")

    return [str(b).strip() for b in raw if b and str(b).strip()]


def build_brand_index(brands):
    """Map normalized brand name -> list of raw brand names sharing it."""
    index = {}
    for brand in brands:
        normalized = normalize_brand(brand)
        if normalized:
            index.setdefault(normalized, []).append(brand)
    return index


def word_boundary_match(query, brands):
    """Find canonical brands that contain `query` as a whole word,
    case-insensitively (e.g. "Urbano" matches "Urbano Fashion" but not
    "Urban"). Returns (chosen_brand, all_matches) or (None, []).
    """
    query = query.strip()
    if not query:
        return None, []

    pattern = re.compile(rf"\b{re.escape(query)}\b", re.IGNORECASE)
    matches = [brand for brand in brands if pattern.search(brand)]

    if not matches:
        return None, []

    exact = [b for b in matches if b.lower() == query.lower()]
    chosen = exact[0] if exact else min(matches, key=len)
    return chosen, matches


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio() * 100


def fuzzy_find(gemini_brand, brands):
    normalized_input = normalize_brand(gemini_brand)
    if not normalized_input:
        return None, 0, 0

    results = []
    for brand in brands:
        normalized_brand = normalize_brand(brand)
        if normalized_brand:
            results.append((similarity(normalized_input, normalized_brand), brand))

    if not results:
        return None, 0, 0

    results.sort(reverse=True, key=lambda x: x[0])
    best_score, best_brand = results[0]
    second_score = results[1][0] if len(results) > 1 else 0
    return best_brand, best_score, second_score


def resolve_brand(gemini_brand, brands, normalized_index):
    normalized_input = normalize_brand(gemini_brand)

    if not normalized_input:
        return {
            "gemini_brand": gemini_brand,
            "ajio_brand": None,
            "match_type": "unmatched",
            "confidence": 0.0,
            "needs_verification": True,
        }

    # 1. Normalized exact match
    exact_matches = normalized_index.get(normalized_input)
    if exact_matches:
        if len(exact_matches) == 1:
            return {
                "gemini_brand": gemini_brand,
                "ajio_brand": exact_matches[0],
                "match_type": "normalized_exact",
                "confidence": 1.0,
                "needs_verification": False,
            }
        return {
            "gemini_brand": gemini_brand,
            "ajio_brand": None,
            "possible_ajio_brands": exact_matches,
            "match_type": "ambiguous_exact",
            "confidence": 1.0,
            "needs_verification": True,
        }

    # 2. Whole-word substring match
    chosen, word_matches = word_boundary_match(gemini_brand, brands)
    if chosen:
        result = {
            "gemini_brand": gemini_brand,
            "ajio_brand": chosen,
            "match_type": "word_boundary",
            "confidence": 0.95,
            "needs_verification": False,
        }
        if len(word_matches) > 1:
            result["possible_ajio_brands"] = word_matches
        return result

    # 3. Fuzzy match
    best_brand, best_score, second_score = fuzzy_find(gemini_brand, brands)
    if best_brand is None:
        return {
            "gemini_brand": gemini_brand,
            "ajio_brand": None,
            "match_type": "unmatched",
            "confidence": 0.0,
            "needs_verification": True,
        }

    margin = best_score - second_score
    if best_score >= FUZZY_THRESHOLD and margin >= FUZZY_MARGIN:
        return {
            "gemini_brand": gemini_brand,
            "ajio_brand": best_brand,
            "match_type": "fuzzy",
            "confidence": round(best_score / 100, 4),
            "score": round(best_score, 2),
            "needs_verification": False,
        }

    # 4. Ambiguous / low confidence
    return {
        "gemini_brand": gemini_brand,
        "ajio_brand": None,
        "possible_ajio_brand": best_brand,
        "match_type": "ambiguous_or_low_confidence",
        "confidence": round(best_score / 100, 4),
        "score": round(best_score, 2),
        "needs_verification": True,
    }


class BrandResolver:
    """Holds a brand list and its index so callers don't rebuild them per lookup."""

    def __init__(self, brands=None):
        self.brands = brands if brands is not None else load_brand_list()
        self.index = build_brand_index(self.brands)

    def resolve(self, text):
        return resolve_brand(text, self.brands, self.index)
