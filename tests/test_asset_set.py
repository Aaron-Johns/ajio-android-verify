"""qa/asset_set.py: extracting the ST/PR art-set code from a banner's CDN filename (FINDINGS 6.10)."""
from qa.asset_set import detect_asset_set

ST_URL = ("https://assets-jiocdn.ajio.com/v2/dry-wildflower-b77541/ajprod/original/ajio-prod/company/1/"
          "applications/6920511ba88a0b4351d486dc/theme/pictures/free/original/"
          "M-UHP-ST-MB-S1-P1-FS-NewUser-Flat30-15092026-1789553927697.jpeg")
PR_URL = ("https://assets-jiocdn.ajio.com/v2/dry-wildflower-b77541/ajprod/original/ajio-prod/company/1/"
          "applications/6920511ba88a0b4351d486dc/theme/pictures/free/original/"
          "M-UHP-PR-MB-S1-P1-AASSCR-Curtainraiser-50to90off-25092026-(1)-1790253692779.jpeg")
PREFIXED_ST_URL = ("https://assets-jiocdn.ajio.com/.../Rest-of-india-M-UHP-ST-MB-S1-P1-AASSCR-"
                   "Curtainraiser-50to90off-25092026-1790259060585.jpeg")
NO_SET_URL = ("https://assets-jiocdn.ajio.com/v2/dry-wildflower-b77541/ajprod/original/ajio-prod/company/1/"
             "applications/6920511ba88a0b4351d486dc/theme/pictures/free/original/"
             "Floating-widget-Front-facing-1788348575159.jpeg")
PR_ALS_URL = ("https://assets-jiocdn.ajio.com/v2/dry-wildflower-b77541/ajprod/original/ajio-prod/company/1/"
             "applications/6920511ba88a0b4351d486dc/theme/pictures/free/original/"
             "M-UHP-PR-ALS-S2-P1-FS-AASSCR-27092026-1790496601176.jpeg")


def test_detects_standard_set():
    assert detect_asset_set(ST_URL) == "ST"


def test_detects_premium_set():
    assert detect_asset_set(PR_URL) == "PR"


def test_detects_premium_set_with_a_non_mb_segment_code():
    # a live premium/men pull (2026-09-28) showed real PR banners using ALS/RE/SBI/BBX/FC/NB segment
    # codes and none using MB - the original regex was anchored on "-MB-" specifically and matched none
    # of them, so --confirm-asset-set PR always reported "never saw a PR banner" even when 15 existed
    assert detect_asset_set(PR_ALS_URL) == "PR"


def test_detects_set_even_with_a_prefix_before_the_convention():
    assert detect_asset_set(PREFIXED_ST_URL) == "ST"


def test_returns_none_for_a_url_that_does_not_follow_the_convention():
    assert detect_asset_set(NO_SET_URL) is None


def test_returns_none_for_no_url():
    assert detect_asset_set(None) is None
    assert detect_asset_set("") is None
