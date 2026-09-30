"""Alias map and reference-file loading."""
from pathlib import Path

from qa.compare import AliasMap
from qa.reference import load_reference

REPO = Path(__file__).resolve().parent.parent


def test_all_22_approved_alias_groups_load_and_compare():
    aliases = AliasMap.from_file()
    assert aliases.same_brand("LEVI'S", "LEVIS") and aliases.same_brand("U.S. Polo Assn.", "US POLO ASSN.")
    assert aliases.same_brand("RED TAPE", "REDTAPE") and not aliases.same_brand("Nike", "ADIDAS")
    assert not aliases.same_brand("", "")


def test_a_group_matches_through_its_first_spelling():
    aliases = AliasMap([["LEVI'S", "LEVIS"], ["Oomph", "OOMPH!"]])
    assert aliases.same_brand("LEVIS", "Levi's") and aliases.same_brand("OOMPH!", "oomph")


def test_sample_reference_loads():
    rows = load_reference(REPO / "config" / "reference.sample.csv")
    assert len(rows) == 3
    assert rows["6a7c9d27d63402ac4bb86c11:14"].expected_category == "Clearance Store"


def test_reference_loader_ignores_comments_and_normalizes_type(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("# note\nbanner_id,expected_brand,expected_category,expected_deeplink_type,notes\n"
                 "x1, Nike ,,brand,hi\n,,,,\n", encoding="utf-8")
    rows = load_reference(p)
    assert list(rows) == ["x1"] and rows["x1"].expected_brand == "Nike" and rows["x1"].expected_deeplink_type == "BRAND"
