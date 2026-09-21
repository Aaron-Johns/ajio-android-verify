"""
AJIO brand resolver.

Combines the two earlier scripts:
  - brand_verify.py   -> normalization, fuzzy matching, confidence scoring,
                          ambiguity handling, recursive file discovery
  - better_verify.py  -> whole-word substring matching (catches cases like
                          "Urbano" -> "Urbano Fashion" that fuzzy scoring
                          alone tends to miss or score too low)

Resolution order for each Gemini-detected brand name:
  1. Normalized exact match against the AJIO brand list
  2. Whole-word substring match (case-insensitive) against raw brand names
  3. Fuzzy match (difflib), accepted only if confident and unambiguous
  4. Otherwise: flagged for manual verification

Output is written next to each gemini_analysis.json as
gemini_analysis_resolved.json, without modifying the original file.
"""

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

# AJIO brand list must be in the same folder as this script.
BRAND_FILE = SCRIPT_DIR / "ajio_brand_names_fixed.json"

GEMINI_FILENAME = "gemini_analysis.json"
OUTPUT_FILENAME = "gemini_analysis_resolved.json"

# Minimum fuzzy-match score (0-100) to accept automatically.
FUZZY_THRESHOLD = 85

# Minimum gap between the best and second-best fuzzy match, to avoid
# accepting an ambiguous fuzzy match.
FUZZY_MARGIN = 5


# ============================================================
# NORMALIZATION
# ============================================================

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


# ============================================================
# LOAD AJIO BRAND LIST
# ============================================================

def load_brand_list():
    """Load canonical brand names. Accepts either a flat list of strings
    or a list of objects with a "name"/"brand_name" field."""
    if not BRAND_FILE.exists():
        print()
        print("=" * 70)
        print("ERROR: AJIO BRAND FILE NOT FOUND")
        print("=" * 70)
        print(f"\nExpected file:\n{BRAND_FILE}\n")
        print("Put the AJIO JSON file in the same folder as this script.\n")
        raise SystemExit(1)

    print(f"Loading AJIO brand list:\n  {BRAND_FILE}")

    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"ERROR reading brand JSON: {e}")
        raise SystemExit(1)

    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = [item.get("name") or item.get("brand_name") for item in raw]

    if not isinstance(raw, list):
        print("ERROR: AJIO brand JSON must contain a JSON array.")
        print('Example: ["ADIDAS", "ARMANI EXCHANGE", "NIKE"]')
        raise SystemExit(1)

    brands = [str(b).strip() for b in raw if b and str(b).strip()]

    print(f"  Loaded {len(brands):,} AJIO brands")
    return brands


def build_brand_index(brands):
    """Map normalized brand name -> list of raw brand names sharing it."""
    index = {}
    for brand in brands:
        normalized = normalize_brand(brand)
        if normalized:
            index.setdefault(normalized, []).append(brand)
    return index


# ============================================================
# WHOLE-WORD SUBSTRING MATCH
# ============================================================

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


# ============================================================
# FUZZY MATCHING
# ============================================================

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


# ============================================================
# RESOLVE ONE BRAND
# ============================================================

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


# ============================================================
# PROCESS ONE GEMINI JSON
# ============================================================

def process_gemini_file(gemini_file, brands, normalized_index):
    try:
        with open(gemini_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ERROR reading {gemini_file}: {e}")
        return False

    brands_mentioned = data.get("brands_mentioned", [])
    if not isinstance(brands_mentioned, list):
        brands_mentioned = []

    brand_resolution = [
        resolve_brand(b, brands, normalized_index)
        for b in brands_mentioned
        if isinstance(b, str)
    ]

    # Add resolution data without destroying Gemini's original output.
    data["brand_resolution"] = brand_resolution
    data["ajio_brands_resolved"] = [
        item["ajio_brand"]
        for item in brand_resolution
        if item.get("ajio_brand") and not item.get("needs_verification", True)
    ]

    if any(item.get("needs_verification", True) for item in brand_resolution):
        data["verification_required"] = True
        data["verification"] = "User verification required"

    output_file = gemini_file.parent / OUTPUT_FILENAME
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  ERROR writing {output_file}: {e}")
        return False

    return True


# ============================================================
# FIND GEMINI FILES
# ============================================================

def find_gemini_files():
    return sorted(
        path
        for path in SCRIPT_DIR.rglob(GEMINI_FILENAME)
        if path.name != OUTPUT_FILENAME
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 70)
    print("AJIO BRAND RESOLVER")
    print("=" * 70)
    print(f"\nScript folder:\n  {SCRIPT_DIR}\n")

    brands = load_brand_list()
    print()

    print("Building normalized brand index...")
    normalized_index = build_brand_index(brands)
    print(f"  {len(normalized_index):,} normalized brand entries\n")

    print("Searching for Gemini analysis files...")
    gemini_files = find_gemini_files()
    print(f"  Found {len(gemini_files):,} {GEMINI_FILENAME} files\n")

    if not gemini_files:
        print("No Gemini analysis files found.\n")
        print("Expected structure:\n")
        print("script_folder\\")
        print("├── brand_verify_combined.py")
        print("├── ajio_brand_names_fixed.json")
        print("└── banner_001\\")
        print("    └── gemini_analysis.json\n")
        return

    successful = 0
    failed = 0

    for index, gemini_file in enumerate(gemini_files, start=1):
        print(f"[{index}/{len(gemini_files)}] {gemini_file}")
        if process_gemini_file(gemini_file, brands, normalized_index):
            print("  OK")
            successful += 1
        else:
            print("  FAILED")
            failed += 1

    print()
    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"\nProcessed : {len(gemini_files):,}")
    print(f"Successful: {successful:,}")
    print(f"Failed    : {failed:,}")
    print(f"\nOutput filename: {OUTPUT_FILENAME}\n")


if __name__ == "__main__":
    main()
