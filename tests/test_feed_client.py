"""Feed client tests: parsing runs on the saved redacted sample, HTTP behaviour on a fake session."""
import json
from pathlib import Path

import pytest

from qa import feed_client

SAMPLE = Path(__file__).resolve().parent.parent / "analysis" / "traffic_capture" / "home_theme_response_sample.json"


@pytest.fixture(scope="module")
def theme():
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return {"sections": sample["one_example_section_per_type"]}


def test_parse_finds_known_destinations(theme):
    banners = feed_client.parse_banners(theme)
    by_type = {}
    for b in banners:
        by_type.setdefault(b.section_type, []).append(b)
    assert by_type["floating-widget"][0].destination_raw == "https://www.ajio.com/s/new30-166553"
    assert by_type["hybrid-banner"][0].destination_raw == "https://www.ajio.com/s/4hoursdelivery-160865?isPDBanner=true"
    assert by_type["hybrid-banner"][0].image_url.startswith("https://assets-jiocdn.ajio.com/")


def test_positions_are_sequential_and_ids_present(theme):
    banners = feed_client.parse_banners(theme)
    assert banners
    assert [b.position for b in banners] == list(range(len(banners)))
    assert all(b.banner_id for b in banners)
    assert len({b.banner_id for b in banners}) == len(banners)


def _block(props):
    return {"_id": "S1", "name": "hybrid-swipe-gallery", "label": "L", "blocks": [{"props": props}]}


def test_destination_prefers_non_empty_key_over_empty_one():
    props = {"redirectURL": {"value": ""}, "redirectImageURL": {"value": "https://ajio.com/s/x-1"}}
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.destination_raw == "https://ajio.com/s/x-1"


def test_banner_with_only_empty_destination_is_kept_and_flagged_empty():
    (b,) = feed_client.parse_banners({"sections": [_block({"redirectURL": {"value": ""}})]})
    assert b.destination_raw == ""


def test_non_banner_widget_without_destination_is_skipped():
    img = {"type": "image_picker_hotspot", "value": {"image": "https://x/y.png", "hotspots": []}}
    sec = {"_id": "W1", "name": "osmos", "props": {"image": img}}
    assert feed_client.parse_banners({"sections": [sec]}) == []


def test_block_banners_get_block_scoped_ids(theme):
    gallery = [b for b in feed_client.parse_banners(theme) if b.section_type == "hybrid-swipe-gallery"]
    assert gallery and all(":" in b.banner_id and b.block_index is not None for b in gallery)


class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._body).encode()
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    monkeypatch.setenv("AJIO_THEME_BEARER", "dGVzdC1iZWFyZXI=")
    monkeypatch.setenv("AJIO_DEVICE_ID", "dev-R")


def test_retries_on_503_then_succeeds():
    s = FakeSession(FakeResp(503), FakeResp(200, {"sections": []}))
    assert feed_client.fetch_theme("home", session=s, sleep=lambda _: None) == {"sections": []}
    assert s.calls == 2


def test_gives_up_after_max_attempts():
    s = FakeSession(*[FakeResp(500) for _ in range(feed_client.MAX_ATTEMPTS)])
    with pytest.raises(feed_client.FeedError):
        feed_client.fetch_theme("home", session=s, sleep=lambda _: None)
    assert s.calls == feed_client.MAX_ATTEMPTS


def test_non_retryable_status_fails_fast():
    s = FakeSession(FakeResp(403))
    with pytest.raises(feed_client.FeedError):
        feed_client.fetch_theme("home", session=s, sleep=lambda _: None)
    assert s.calls == 1


def test_rejected_bearer_fails_fast_without_leaking_it():
    s = FakeSession(FakeResp(401, {"error": "Invalid authorization token"}))
    with pytest.raises(feed_client.FeedError) as exc:
        feed_client.fetch_theme("home", session=s, sleep=lambda _: None)
    assert s.calls == 1 and "dGVzdC1iZWFyZXI=" not in str(exc.value)


def test_missing_bearer_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("AJIO_THEME_BEARER")
    with pytest.raises(feed_client.FeedError, match="AJIO_THEME_BEARER"):
        feed_client.fetch_theme("home", session=FakeSession(), sleep=lambda _: None)


def test_request_is_signed_and_carries_bearer():
    seen = {}

    class Spy(FakeSession):
        def get(self, url, headers=None, timeout=None):
            seen.update(headers)
            return super().get(url, headers=headers, timeout=timeout)

    feed_client.fetch_theme("home", session=Spy(FakeResp(200, {"sections": []})), sleep=lambda _: None)
    assert seen["authorization"] == "Bearer dGVzdC1iZWFyZXI="
    assert seen["x-fp-signature"].startswith("v1.1:") and len(seen["x-fp-signature"]) == 5 + 64
    assert len(seen["x-fp-date"]) == 16 and seen["x-fp-date"].endswith("Z")


def test_run_log_redacts_secrets(tmp_path):
    log = feed_client.RunLog(base=tmp_path)
    log.record("home", {"authorization": "Bearer SECRET", "device-id": "abc", "accept": "x"},
               "https://example/", FakeResp(200, {"a": 1}, {"set-cookie": "s=1"}), 1)
    text = "".join(p.read_text(encoding="utf-8") for p in log.dir.glob("*.attempt1.json"))
    assert "SECRET" not in text and "s=1" not in text and "abc" not in text
    assert '"accept": "x"' in text
