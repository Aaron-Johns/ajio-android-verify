"""The vision port: the four fixes from CLAUDE.md section 10, plus prompt fidelity."""
import re
from pathlib import Path

import pytest

from qa.compare import AliasMap
from qa.spotcheck import vision

ORIGINAL = Path(__file__).resolve().parent.parent / "inputs" / "image_segmentation_2.py"


def test_prompt_is_verbatim_from_the_original():
    # The original can't be imported (it prompts for an API key at import time - fix #3), so read its source.
    original = re.search(r'PROMPT = """(.*?)"""', ORIGINAL.read_text(encoding="utf-8"), re.DOTALL).group(1)
    assert vision.PROMPT == original and vision.MODEL == "gemma-4-31b-it"


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

def test_compare_banner_to_landing():
    aliases = AliasMap([["LEVI'S", "LEVIS"]])
    assert vision.compare_banner_to_landing({"brands_mentioned": ["LEVIS"]}, {"brands_mentioned": ["LEVI'S"]}, aliases).verdict == "MATCH"
    assert vision.compare_banner_to_landing({"brands_mentioned": ["Nike"]}, {"brands_mentioned": ["Puma"]}).verdict == "DIFFERENT"
    assert vision.compare_banner_to_landing({"brands_mentioned": []}, {"brands_mentioned": ["Puma"]}) is None
    assert vision.compare_banner_to_landing({}, {}) is None


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
