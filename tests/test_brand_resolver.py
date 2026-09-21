"""The ported resolver must behave exactly like inputs/brand_verify_combined.py (CLAUDE.md 7.1)."""
import importlib.util
import inspect
import json
from pathlib import Path

import pytest

from qa import brand_resolver as port

INPUTS = Path(__file__).resolve().parent.parent / "inputs"
FIXTURES = json.loads((INPUTS / "resolver_fixtures.json").read_text(encoding="utf-8"))["cases"]

VERBATIM = ["normalize_brand", "build_brand_index", "word_boundary_match", "similarity", "fuzzy_find", "resolve_brand"]


@pytest.fixture(scope="module")
def original():
    spec = importlib.util.spec_from_file_location("brand_verify_combined", INPUTS / "brand_verify_combined.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def resolver():
    # Fixtures were generated against the ORIGINAL 7,000-brand list (they include the colliding pairs).
    return port.BrandResolver(port.load_brand_list(INPUTS / "ajio_brand_names_fixed.json"))


@pytest.mark.parametrize("name", VERBATIM)
def test_function_source_is_verbatim(original, name):
    assert inspect.getsource(getattr(port, name)) == inspect.getsource(getattr(original, name))


def test_thresholds_unchanged(original):
    assert (port.FUZZY_THRESHOLD, port.FUZZY_MARGIN) == (original.FUZZY_THRESHOLD, original.FUZZY_MARGIN)


@pytest.mark.parametrize("case", FIXTURES, ids=[c["input"] for c in FIXTURES])
def test_matches_fixture_exactly(resolver, case):
    assert resolver.resolve(case["input"]) == case["expected"]


def test_deduped_copy_resolves_the_three_colliding_pairs():
    deduped = port.BrandResolver()  # default path is config/ajio_brand_names_deduped.json
    assert len(deduped.brands) == 6997
    for query, keeper in [("Gini & Jony", "GINI & JONY"), ("Oomph", "Oomph"), ("ZRI", "ZRI")]:
        result = deduped.resolve(query)
        assert (result["match_type"], result["ajio_brand"]) == ("normalized_exact", keeper), query


def test_original_list_is_untouched_and_still_ambiguous(resolver):
    assert len(resolver.brands) == 7000
    assert resolver.resolve("Oomph")["match_type"] == "ambiguous_exact"


def test_fixture_count():
    assert len(FIXTURES) == 42
