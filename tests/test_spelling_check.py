"""A misspelt word on the banner's own picture (Gemma's `spelling_errors`) makes the banner FAIL, with the word and its correction
in the reason. Nothing here touches the network."""
from qa import feed_verify as fv
from tests.test_feed_verify import Fetcher, INFO, banner, hotspot, listing, real_image, run

FETCHER = lambda: Fetcher({"a-1": listing()})                      # noqa: E731 - the listing agrees with INFO, so only spelling can fail it


def read(errors):
    return lambda _image: {**INFO, "spelling_errors": errors}


def test_a_misspelt_word_fails_a_banner_that_is_otherwise_right(tmp_path):
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, FETCHER(), analyzer=read([{"word": "SUMER", "correction": "summer"}]))[0]
    assert r["result"] == "FAIL" and r["reason"] == 'spelling: "SUMER" should be "summer"'
    assert r["banner_check"]["spelling_errors"] == [{"word": "SUMER", "correction": "summer"}]


def test_no_misspelling_or_a_word_listed_as_its_own_fix_changes_nothing(tmp_path):
    for n, errors in enumerate(([], None, [{"word": "Sale", "correction": "sale"}], [{"word": "", "correction": "x"}], ["junk"])):
        r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path / str(n), FETCHER(), analyzer=read(errors))[0]
        assert r["result"] == "PASS" and "spelling_errors" not in r["banner_check"]


def test_a_misspelling_sits_beside_the_other_reasons(tmp_path):
    bad_brands = {**INFO, "brands_mentioned": ["nike"], "spelling_errors": [{"word": "COLLCTION", "correction": "collection"}]}
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, FETCHER(), analyzer=lambda _image: bad_brands)[0]
    assert r["result"] == "FAIL" and "missing brands" in r["reason"] and 'spelling: "COLLCTION" should be "collection"' in r["reason"]


def test_a_hotspot_crop_is_not_read_for_spelling(tmp_path):
    """A crop can cut a word in half, so only the banner's own picture counts: the same reading on a hotspot crop is ignored."""
    b = banner("b", "https://ajio.com/s/a-1", hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100)
    seen = []

    def analyzer(path):
        seen.append(path.name)
        return {**INFO, "spelling_errors": [{"word": "COLLCTION", "correction": "collection"}]} if "__hs" not in path.name else INFO

    r = run([b], tmp_path, FETCHER(), analyzer=analyzer, image_fetcher=real_image)[0]
    assert r["result"] == "FAIL" and any("__hs" in n for n in seen)
    assert "spelling_errors" in r["banner_check"] and "spelling_errors" not in r["hotspot_checks"][0]["banner_check"]
    assert r["hotspot_checks"][0]["result"] == "PASS"


def test_the_cli_summary_names_the_misspelling():
    result = {"banner_id": "b", "alt_text": "b", "destination_raw": "u", "result": "FAIL", "reason": "",
              "banner_check": {"spelling_errors": [{"word": "SUMER", "correction": "summer"}]}}
    assert 'spelling: "SUMER" should be "summer"' in fv.format_summary([result])


def test_words_read_off_a_picture_cannot_carry_html_into_the_reason(tmp_path):
    """The classic UI shows reasons as HTML, and the words come from text on a banner picture: no < > & or quotes get through."""
    evil = [{"word": "<img src=x onerror=alert(1)>", "correction": 'sum"mer'}]
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, FETCHER(), analyzer=read(evil))[0]
    assert r["result"] == "FAIL" and not any(ch in r["reason"].replace('"', "", 4) for ch in "<>&")
    assert r["banner_check"]["spelling_errors"] == [{"word": "img src=x onerror=alert(1)", "correction": "summer"}]
