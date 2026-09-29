"""Feed client tests: parsing runs on the saved redacted sample, HTTP behaviour on a fake session."""
import json
from datetime import datetime, timezone
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


def test_hotspots_are_read_with_their_bounding_boxes_and_the_images_own_dimensions():
    hotspots = [{"url": "https://ajio.com/s/a-1", "x": 7.4, "y": -1.8, "width": 480.2, "height": 601.5, "alt": "Brand A"},
               {"url": "https://ajio.com/s/b-2", "x": 519.4, "y": 2.4, "width": 495.4, "height": 613.9}]
    props = {"image": {"value": {"image": "https://x/y.png", "imageWidth": 1024, "imageHeight": 672, "hotspots": hotspots}}}
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.image_width == 1024 and b.image_height == 672
    assert [h.url for h in b.hotspots] == ["https://ajio.com/s/a-1", "https://ajio.com/s/b-2"]
    assert b.hotspots[0].x == 7.4 and b.hotspots[0].width == 480.2 and b.hotspots[0].alt == "Brand A"
    assert b.hotspots[1].alt == ""   # alt is optional per-hotspot


def test_hotspots_without_a_url_are_dropped():
    props = {"image": {"value": {"image": "https://x/y.png", "hotspots": [{"x": 0, "y": 0, "width": 10, "height": 10}]}}}
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.hotspots == []


def test_hotspots_come_from_the_displayed_image_not_an_unrelated_image_prop():
    # popup_image also carries an "image" value with its own hotspots - they must not leak onto
    # the banner's displayed image (bannerImage/image), since x/y only make sense per-image.
    props = {
        "bannerImage": {"value": {"image": "https://x/shown.png", "hotspots": []}},
        "popup_image": {"value": {"image": "https://x/popup.png", "hotspots": [{"url": "https://ajio.com/s/p-1", "x": 0, "y": 0, "width": 5, "height": 5}]}},
    }
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.image_url == "https://x/shown.png"
    assert b.hotspots == []


def test_block_banners_get_block_scoped_ids(theme):
    gallery = [b for b in feed_client.parse_banners(theme) if b.section_type == "hybrid-swipe-gallery"]
    assert gallery and all(":" in b.banner_id and b.block_index is not None for b in gallery)


# ---- hidden: a CMS visibility flag off, and/or outside its schedule window as of fetch time ----

def _dest_props(extra=None):
    props = {"redirectURL": {"value": "https://ajio.com/s/x-1"}}
    props.update(extra or {})
    return props


def _block_with_schedule(props, schedule):
    section = _block(props)
    section["blocks"][0]["predicate"] = {"schedule": schedule}
    return section


def test_default_visible_banner_is_not_hidden():
    (b,) = feed_client.parse_banners({"sections": [_block(_dest_props())]})
    assert b.hidden is False and b.hidden_reason == ""


def test_showblock_false_marks_hidden_with_reason():
    props = _dest_props({"showBlock": {"type": "checkbox", "value": False}})
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.hidden is True and b.hidden_reason == "block_hidden"


def test_showcomponent_false_marks_hidden_with_reason():
    props = _dest_props({"showComponent": {"type": "checkbox", "value": False}})
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.hidden is True and b.hidden_reason == "component_hidden"


def test_both_showblock_and_showcomponent_false_combine_reasons():
    props = _dest_props({"showBlock": {"type": "checkbox", "value": False},
                         "showComponent": {"type": "checkbox", "value": False}})
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.hidden is True and b.hidden_reason == "block_hidden, component_hidden"


def test_showblock_true_is_not_hidden():
    props = _dest_props({"showBlock": {"type": "checkbox", "value": True}})
    (b,) = feed_client.parse_banners({"sections": [_block(props)]})
    assert b.hidden is False


def test_outside_schedule_window_is_hidden():
    schedule = [{"cron": "* * * * * *", "start": "2026-01-01T00:00:00.000Z", "end": "2026-01-02T00:00:00.000Z"}]
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    (b,) = feed_client.parse_banners({"sections": [_block_with_schedule(_dest_props(), schedule)]}, now=now)
    assert b.hidden is True and b.hidden_reason == "outside_schedule"


def test_inside_schedule_window_is_not_hidden():
    schedule = [{"cron": "* * * * * *", "start": "2026-01-01T00:00:00.000Z", "end": "2027-01-01T00:00:00.000Z"}]
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    (b,) = feed_client.parse_banners({"sections": [_block_with_schedule(_dest_props(), schedule)]}, now=now)
    assert b.hidden is False


def test_one_matching_window_among_several_is_enough():
    schedule = [{"start": "2020-01-01T00:00:00.000Z", "end": "2020-01-02T00:00:00.000Z"},
               {"start": "2026-01-01T00:00:00.000Z", "end": "2027-01-01T00:00:00.000Z"}]
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    (b,) = feed_client.parse_banners({"sections": [_block_with_schedule(_dest_props(), schedule)]}, now=now)
    assert b.hidden is False


def test_both_cms_hidden_and_outside_schedule_combine_reasons():
    props = _dest_props({"showBlock": {"type": "checkbox", "value": False}})
    schedule = [{"start": "2020-01-01T00:00:00.000Z", "end": "2020-01-02T00:00:00.000Z"}]
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    (b,) = feed_client.parse_banners({"sections": [_block_with_schedule(props, schedule)]}, now=now)
    assert b.hidden is True and b.hidden_reason == "block_hidden, outside_schedule"


def test_malformed_schedule_window_is_skipped_not_fatal():
    schedule = [{"start": "not-a-date", "end": "also-not-a-date"}]
    (b,) = feed_client.parse_banners({"sections": [_block_with_schedule(_dest_props(), schedule)]})
    assert b.hidden is True and b.hidden_reason == "outside_schedule"   # no valid window matched, so it reads as outside


def test_empty_schedule_list_is_always_in_schedule():
    (b,) = feed_client.parse_banners({"sections": [_block_with_schedule(_dest_props(), [])]})
    assert b.hidden is False


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
    # Without this, _resolve_user_groups falls through to a real, live cohort_client call - these
    # tests use a fake `session` for fetch_theme itself, but cohort_client talks to the real network
    # directly (see test_cohort_client.py for that path's own offline-only coverage).
    monkeypatch.setenv("AJIO_USER_GROUPS", "l1:test|l2:test")


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


# ---- _resolve_user_groups: explicit override wins; otherwise live cohort resolution, with a
# fallback to the confirmed default baseline if that fails (FINDINGS 6.10) ----

def test_explicit_override_wins_and_never_calls_the_cohort_client(monkeypatch):
    monkeypatch.setenv("AJIO_USER_GROUPS", "l1:nonpremium|l2:men")
    monkeypatch.setattr(feed_client.cohort_client, "resolve_user_groups",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert feed_client._resolve_user_groups("dev-1") == "l1:nonpremium|l2:men"


def test_unset_override_uses_the_live_resolved_value(monkeypatch):
    monkeypatch.delenv("AJIO_USER_GROUPS", raising=False)
    monkeypatch.setattr(feed_client.cohort_client, "resolve_user_groups", lambda device_id, run_log=None: "l1:x|l2:y")
    assert feed_client._resolve_user_groups("dev-1") == "l1:x|l2:y"


def test_unset_override_falls_back_to_the_default_baseline_if_resolution_fails(monkeypatch):
    monkeypatch.delenv("AJIO_USER_GROUPS", raising=False)

    def boom(*a, **k):
        raise feed_client.cohort_client.CohortError("network down")
    monkeypatch.setattr(feed_client.cohort_client, "resolve_user_groups", boom)
    assert feed_client._resolve_user_groups("dev-1") == feed_client.cohort_client.DEFAULT_USER_GROUPS
