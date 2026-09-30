"""The vision port: the four fixes from CLAUDE.md section 10, plus prompt fidelity."""
import re
from pathlib import Path

import pytest

from qa.compare import AliasMap
from qa.spotcheck import vision

ORIGINAL = Path(__file__).resolve().parent.parent / "inputs" / "image_segmentation_2.py"


# The only intentional deviations from the ported original: two new numbered sections (inserted
# before the original's final "VERIFICATION" section, which is renumbered to make room), two new
# JSON fields (added for the gender-audience and open-ended-brand-list rules), and one paragraph
# inserted into section 2 telling the model not to drop an ambiguous but plausible brand name just
# because a more prominent brand/logo is also on the banner - a real miss seen live (a banner's
# prominent "FYRE ROSE" logo caused the model to talk itself out of also listing "Leia", reasoning
# it was "likely a collection name", even though nothing ruled that out - AJIO Feed Verify §user
# rules, none of this in the original script). The test below checks that undoing exactly these
# edits reconstructs the original verbatim.
_GENDER_SECTION = '''5. TARGET GENDER

Decide who this banner is promoting products for, using explicit text (e.g. "Men's", "Women's",
"Boys", "Girls", "Infants") and, failing that, the clothing/models shown.

Return exactly one of:
- "men" (men's/boys' products only - no women's or girls' items shown or implied)
- "women" (women's/girls' products only - no men's or boys' items shown or implied)
- "boys" (specifically boys, not men)
- "girls" (specifically girls, not women)
- "infants" (babies/toddlers)
- "men_and_women" (clearly for both men and women together, e.g. a mixed shot, or a brand/store-wide banner)
- "girls_and_boys" (clearly for both girls and boys together, a kids-wide banner, with no adult men's/women's products)
- "unclear" (cannot confidently tell, or it does not cleanly fit any category above)

Do not guess a specific category just to avoid "unclear".

'''
_BRAND_LIST_SECTION = '''6. OPEN-ENDED BRAND LIST

Check whether the banner's own text says there are more brands beyond the ones named - phrases
like "& more", "and more", "+ more", "many more brands".

Return true if such a phrase is visible on the banner. Return false if the named brand(s) appear
to be the complete list, or if no brands are mentioned at all.

Do not guess; only return true if the phrase is actually visible.

'''
_GENDER_FIELD = '  "target_gender": "",\n'
_BRAND_LIST_FIELD = '  "more_brands_than_named": false,\n'
_AMBIGUOUS_BRAND_PARAGRAPH = '''A banner can promote more than one brand or label at once. If a short name (one or two words) is
shown in its own distinct, title- or wordmark-like styling - not as part of a marketing sentence -
list it as a brand even if you suspect it might instead be a collection or product-line name, and
even if a different, more prominent logo also appears on the same banner. Do not pick only the most
prominent name and drop the rest - a downstream check reconciles this list against the actual product
listing, so it is far worse to omit a real brand than to list an extra candidate.

'''


def test_prompt_matches_the_original_plus_the_documented_additions():
    # The original can't be imported (it prompts for an API key at import time - fix #3), so read its source.
    original = re.search(r'PROMPT = """(.*?)"""', ORIGINAL.read_text(encoding="utf-8"), re.DOTALL).group(1)
    reconstructed = (vision.PROMPT
                      .replace(_GENDER_SECTION, "").replace(_BRAND_LIST_SECTION, "")
                      .replace("7. VERIFICATION", "5. VERIFICATION")
                      .replace(_GENDER_FIELD, "").replace(_BRAND_LIST_FIELD, "")
                      .replace(_AMBIGUOUS_BRAND_PARAGRAPH, ""))
    assert reconstructed == original and vision.MODEL == "gemma-4-31b-it"


# ---- fix 2: robust JSON parsing ----
GOOD = '{"brands_mentioned": ["Nike"], "deal_offered": "40% OFF"}'


@pytest.mark.parametrize("raw", [
    GOOD,
    f"```json\n{GOOD}\n```",
    f"```\n{GOOD}\n```",            # bare fence: the original's parser broke on this
    f"```JSON\n{GOOD}\n```",
    f"Here is the analysis:\n{GOOD}\nHope that helps!",
    f"  \n{GOOD}\n  ",
])
def test_parse_model_json_handles_fences_and_prose(raw):
    assert vision.parse_model_json(raw)["brands_mentioned"] == ["Nike"]


@pytest.mark.parametrize("raw", ["", "no json here", "[1, 2, 3]", "```json\nnot json\n```", None])
def test_parse_model_json_rejects_non_objects(raw):
    with pytest.raises(ValueError):
        vision.parse_model_json(raw)


# ---- fix 1: verification flag is OR-ed, never overridden ----

def test_model_saying_verification_required_is_kept_even_when_brands_and_deal_are_present():
    out = vision.apply_verification({"brands_mentioned": ["Nike"], "deal_offered": "40% OFF", "verification_required": True})
    assert out["verification_required"] is True and out["verification"] == "User verification required"


@pytest.mark.parametrize("result", [
    {"brands_mentioned": [], "deal_offered": "40% OFF"},
    {"brands_mentioned": ["Nike"], "deal_offered": ""},
    {},
])
def test_missing_brand_or_deal_requires_verification(result):
    assert vision.apply_verification(result)["verification_required"] is True


@pytest.mark.parametrize("flag", [False, "false", "False", None, 0])
def test_complete_result_without_model_flag_needs_no_verification(flag):
    out = vision.apply_verification({"brands_mentioned": ["Nike"], "deal_offered": "40% OFF", "verification_required": flag})
    assert out["verification_required"] is False and out["verification"] == ""


# ---- fix 3: nothing happens at import; missing key is a handled condition, never a prompt ----

def test_missing_key_raises_vision_unavailable_instead_of_prompting(monkeypatch):
    monkeypatch.setattr(vision, "load_env", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(vision.VisionUnavailable, match="GEMINI_API_KEY"):
        vision.get_client()
    assert "getpass" not in Path(vision.__file__).read_text(encoding="utf-8").replace("no getpass", "")


def test_client_is_created_with_a_request_timeout(monkeypatch):
    # No timeout is set by the SDK itself - a stalled connection (e.g. after a 5xx) would otherwise
    # hang the calling worker thread forever instead of raising something _analyze()'s retry loop
    # can catch (a live run got stuck on exactly this - see qa/spotcheck/vision.py's TIMEOUT_MS).
    monkeypatch.setattr(vision, "load_env", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []

    class FakeGenaiClient:
        def __init__(self, **kw):
            calls.append(kw)

    import google.genai as genai_module
    monkeypatch.setattr(genai_module, "Client", FakeGenaiClient)
    vision.get_client()
    assert calls == [{"api_key": "test-key", "http_options": {"timeout": vision.TIMEOUT_MS}}]


# ---- fix 4: uploaded file deleted after use ----

class FakeClient:
    def __init__(self, output, fail_create=False):
        self.output, self.fail_create, self.deleted = output, fail_create, []
        self.files = self
        self.interactions = self

    def upload(self, file):
        class U:
            uri, mime_type, name = "files/uri", "image/png", "files/abc"
        return U()

    def create(self, model, input):
        if self.fail_create:
            raise RuntimeError("model error")
        class R:
            output_text = self.output
        return R()

    def delete(self, name):
        self.deleted.append(name)


def test_analyze_image_parses_verifies_and_deletes_the_upload(tmp_path):
    client = FakeClient(f"```\n{GOOD}\n```")
    result = vision.analyze_image(tmp_path / "x.png", client)
    assert result["brands_mentioned"] == ["Nike"] and result["verification_required"] is False
    assert result["_metadata"]["model"] == vision.MODEL and client.deleted == ["files/abc"]


@pytest.mark.parametrize("client", [FakeClient("garbage"), FakeClient(GOOD, fail_create=True)])
def test_upload_is_deleted_even_when_the_call_or_parse_fails(tmp_path, client):
    with pytest.raises(Exception):
        vision.analyze_image(tmp_path / "x.png", client)
    assert client.deleted == ["files/abc"]


def test_failed_cleanup_does_not_mask_the_result(tmp_path):
    client = FakeClient(GOOD)
    client.delete = lambda name: (_ for _ in ()).throw(RuntimeError("delete failed"))
    assert vision.analyze_image(tmp_path / "x.png", client)["brands_mentioned"] == ["Nike"]


# ---- banner vs landing comparison ----


def test_extra_instructions_are_appended_without_changing_the_ported_prompt():
    seen = []

    class Files:
        def upload(self, file): return type("U", (), {"uri": "u", "mime_type": "image/png", "name": "n"})()
        def delete(self, name): pass

    class Interactions:
        def create(self, model, input):
            seen.append(input[0]["text"])
            return type("I", (), {"output_text": '{"brands_mentioned": [], "deal_offered": ""}'})()

    client = type("C", (), {"files": Files(), "interactions": Interactions()})()
    vision.analyze_image("x.png", client=client)
    vision.analyze_image("x.png", client=client, extra=vision.HERO_EXTRA)
    assert seen[0] == vision.PROMPT and seen[1].startswith(vision.PROMPT) and "PROMOTING" in seen[1]


# ---- pacing: Gemma requests are spaced out across all worker threads ----------------------------------

class FakeClock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_pacer_spaces_requests_by_the_per_minute_limit_plus_a_margin():
    clock = FakeClock()
    pacer = vision.CallPacer(6, clock=clock, sleep=clock.sleep)
    waits = [pacer.acquire() for _ in range(4)]
    assert waits[0] == 0                                       # nothing to wait for on an idle pacer
    assert all(abs(w - 10.5) < 1e-6 for w in waits[1:])        # 60 / 6 = 10 s, +5% margin
    assert clock.slept == [pytest.approx(10.5)] * 3


def test_a_burst_of_requests_never_exceeds_the_limit_in_any_minute():
    clock = FakeClock()
    pacer = vision.CallPacer(6, clock=clock, sleep=clock.sleep)
    starts = []
    for _ in range(30):                                        # 30 workers all asking at once
        pacer.acquire()
        starts.append(clock.now)
    for i, t in enumerate(starts):
        assert sum(1 for s in starts if t <= s < t + 60) <= 6, f"more than 6 requests within a minute of #{i}"


def test_an_idle_pacer_lets_the_next_request_straight_through():
    clock = FakeClock()
    pacer = vision.CallPacer(6, clock=clock, sleep=clock.sleep)
    pacer.acquire()
    clock.now += 120                                           # a quiet two minutes
    assert pacer.acquire() == 0 and clock.slept == []


def test_zero_or_negative_means_no_limit():
    for value in (0, -1):
        pacer = vision.CallPacer(value, sleep=lambda s: pytest.fail("must not sleep"))
        assert [pacer.acquire() for _ in range(5)] == [0.0] * 5


def test_waiting_is_announced_once_and_only_when_there_is_a_wait():
    clock = FakeClock()
    pacer = vision.CallPacer(6, clock=clock, sleep=clock.sleep)
    calls = []
    pacer.acquire(on_wait=lambda: calls.append(1))
    assert calls == []
    pacer.acquire(on_wait=lambda: calls.append(1))
    assert calls == [1]


def test_threads_really_are_held_back_and_never_share_a_slot():
    import threading
    pacer = vision.CallPacer(600)                              # 10/s (0.105 s apart) - fast enough to run for real
    starts, lock = [], threading.Lock()

    def worker():
        pacer.acquire()
        with lock:
            starts.append(vision.time.monotonic())
    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    starts.sort()
    assert len(starts) == 6 and all(b - a >= 0.09 for a, b in zip(starts, starts[1:])), starts


def test_analyze_image_waits_its_turn_before_uploading(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(vision, "_pacer", type("P", (), {"acquire": lambda self, on_wait=None: order.append("turn") or 0.0})())
    client = FakeClient(GOOD)
    real_upload = client.upload
    client.upload = lambda file: (order.append("upload"), real_upload(file))[1]
    vision.analyze_image(tmp_path / "x.png", client)
    assert order == ["turn", "upload"]                         # the turn comes first: a waiting request holds no upload


def test_a_request_that_waits_says_so_then_puts_back_its_step(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(vision, "_pacer", vision.CallPacer(6, clock=clock, sleep=clock.sleep))
    vision._pacer.acquire()                                    # use up the free slot
    seen = []
    with vision.reporting(seen.append) as note:
        note("Reading image")
        vision._wait_for_turn()
    assert seen == ["Reading image", "Waiting turn", "Reading image"]


def test_a_request_with_no_wait_reports_nothing_extra():
    seen = []
    with vision.reporting(seen.append) as note:
        note("Reading image")
        vision._wait_for_turn()                                # conftest's pacer never waits
    assert seen == ["Reading image"]


# ---- pacing across processes: the next free slot lives in a shared file -------------------------------

def test_two_pacers_sharing_a_file_queue_behind_each_other(tmp_path):
    """Stands in for two run processes: separate pacer objects (own thread locks, own memory), one state file."""
    clock = FakeClock()
    state = tmp_path / "pacer.json"
    a = vision.CallPacer(6, clock=clock, sleep=clock.sleep, shared_path=state)
    b = vision.CallPacer(6, clock=clock, sleep=clock.sleep, shared_path=state)
    assert a.acquire() == 0
    assert b.acquire() == pytest.approx(10.5)                  # b never asked before, yet it waits behind a's call
    assert a.acquire() == pytest.approx(10.5)                  # ...and a then waits behind b's


def test_without_a_shared_file_each_pacer_only_paces_itself(tmp_path):
    clock = FakeClock()
    a = vision.CallPacer(6, clock=clock, sleep=clock.sleep)
    b = vision.CallPacer(6, clock=clock, sleep=clock.sleep)
    a.acquire()
    assert b.acquire() == 0                                    # the old per-process behaviour


def test_a_booking_far_in_the_future_is_capped_not_waited_out(tmp_path):
    clock = FakeClock()
    state = tmp_path / "pacer.json"
    state.write_text('{"next": %f}' % (clock.now + 10 ** 7), encoding="utf-8")     # a leftover / clock jump
    pacer = vision.CallPacer(6, clock=clock, sleep=clock.sleep, shared_path=state)
    assert pacer.acquire() == pytest.approx(vision.MAX_AHEAD_S)


def test_a_corrupt_state_file_is_treated_as_empty(tmp_path):
    state = tmp_path / "pacer.json"
    state.write_text("not json", encoding="utf-8")
    clock = FakeClock()
    assert vision.CallPacer(6, clock=clock, sleep=clock.sleep, shared_path=state).acquire() == 0


def test_an_unusable_state_folder_falls_back_to_pacing_this_process(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    clock = FakeClock()
    pacer = vision.CallPacer(6, clock=clock, sleep=clock.sleep, shared_path=blocker / "sub" / "pacer.json")   # parent is a file
    assert pacer.acquire() == 0
    assert pacer.acquire() == pytest.approx(10.5)              # still paced, just alone


def test_real_processes_at_once_never_beat_the_limit(tmp_path):
    """Three real OS processes hammering one shared pacer (300/min = 0.21 s apart): every call, from any process,
    is at least that far from the previous one."""
    import subprocess
    import sys
    state = tmp_path / "pacer.json"
    code = ("import sys, time\n"
            "from pathlib import Path\n"
            "from qa.spotcheck import vision\n"
            "p = vision.CallPacer(300, shared_path=Path(sys.argv[1]))\n"
            "for _ in range(4):\n"
            "    p.acquire()\n"
            "    print(time.time(), flush=True)\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(state)], stdout=subprocess.PIPE, text=True,
                              cwd=str(Path(__file__).resolve().parents[1])) for _ in range(3)]
    stamps = sorted(float(line) for p in procs for line in p.communicate(timeout=60)[0].split())
    assert len(stamps) == 12
    interval = 60.0 / 300 * 1.05
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= interval * 0.85, f"two calls only {min(gaps):.3f}s apart; wanted >= {interval:.3f}s"
