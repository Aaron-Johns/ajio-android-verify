"""Gemma banner/landing-page analysis, ported from inputs/image_segmentation_2.py (CLAUDE.md section 10).

Used only as the fallback for ambiguous deep links. Fixes relative to the original:
  1. verification flag is OR-ed with the model's own answer, not overridden by it;
  2. JSON parsing handles ```json, bare ``` and prose-wrapped output;
  3. no getpass / client creation at import time (the key comes from GEMINI_API_KEY or .env);
  4. the uploaded file is deleted after use.
The folder scanning, banner-number selection and thread pool of the original were dropped.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from qa.envfile import load_env
from qa.spotcheck.landing import VisionVerdict, brands_overlap

log = logging.getLogger("qa.spotcheck.vision")

MODEL = "gemma-4-31b-it"

PROMPT = """
Analyze this ecommerce fashion banner carefully.

Return ONLY valid JSON.

Identify the following:

1. CLOTHING SEEN

List every clearly visible clothing or fashion item.

Examples:
- t-shirt
- shirt
- jeans
- trousers
- jacket
- dress
- hoodie
- sneakers
- shoes
- bag

Do not invent items that are not clearly visible.

2. BRANDS MENTIONED

Identify every clothing/fashion brand represented in the banner.

A brand counts if:

- its name is visibly written
- its wordmark is visible
- its recognizable graphical logo is visible

Graphical logos count even when they contain no readable text.

Do NOT classify generic promotional text as brands.

Examples of generic text:
- SALE
- SHOP NOW
- NEW
- OFFER
- COLLECTION
- OFF
- BUY NOW

3. DEAL OFFERED

Extract the promotional offer shown in the banner.

Preserve important numbers and meaning.

Examples:
- 40% OFF
- UP TO 50% OFF
- BUY 2 GET 1 FREE
- MIN 40% OFF

Do not invent a deal if none is clearly visible.

4. SPELLING ERRORS

Check all visible text in the banner (brand names excluded) for spelling errors.

List every misspelled word you find, along with the corrected spelling.

Examples of what counts:
- "SUMER SALE" -> "sumer" is misspelled ("summer")
- "COLLCTION" -> "collction" is misspelled ("collection")

Do not flag:
- Brand names or stylized brand wordmarks
- Intentional stylization (e.g. "SHOPPN'" as a deliberate style choice)
- Non-English words

If no spelling errors are visible, return an empty list.

5. VERIFICATION

If you cannot confidently determine the brand(s), OR you cannot confidently determine
the deal offered, verification is required.

In that situation:

"verification_required" must be true

and:

"verification" must contain exactly:

"User verification required"

Do not guess information simply to avoid verification.

Return EXACTLY this structure:

{
  "clothing_seen": [],
  "brands_mentioned": [],
  "deal_offered": "",
  "spelling_errors": [
    {"word": "", "correction": ""}
  ],
  "verification_required": false,
  "verification": ""
}

Do not add explanations outside the JSON.

Do not invent information.
"""


class VisionUnavailable(RuntimeError):
    """No API key, SDK missing, or the call failed; callers fall back to non-vision evidence."""


_FENCE = re.compile(r"```[A-Za-z]*\s*(.*?)```", re.DOTALL)


def parse_model_json(raw: str) -> dict:
    """Extract the JSON object from model output, tolerating ```json / ``` fences and prose."""
    text = (raw or "").strip()
    candidates = [text, *(m.strip() for m in _FENCE.findall(text))]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found in model output")


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def apply_verification(result: dict) -> dict:
    """Verification is required if the model says so OR brands/deal are missing (never overridden to False)."""
    needs = _truthy(result.get("verification_required")) or not result.get("brands_mentioned") or not result.get("deal_offered")
    result["verification_required"] = needs
    result["verification"] = "User verification required" if needs else ""
    return result


def get_client():
    load_env()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise VisionUnavailable("GEMINI_API_KEY is not set (put it in .env)")
    try:
        from google import genai
    except ImportError as exc:
        raise VisionUnavailable(f"google-genai is not installed: {exc}") from exc
    return genai.Client(api_key=api_key)


HERO_EXTRA = ("This is a hero promotional banner. List only the brands the banner is PROMOTING: brands in its "
              "headline or footer text, wordmarks or logo band. Ignore brand names that appear on props, book spines, "
              "product labels, packaging or background objects in the photo.")


def analyze_image(image_path: str | Path, client=None, extra: str = "") -> dict:
    """Analyse one image and return the parsed, verification-checked result dict. `extra` adds instructions after PROMPT."""
    image_path = Path(image_path)
    prompt = PROMPT + (f"\n\nAdditional instructions:\n{extra}\n" if extra else "")
    client = client or get_client()
    uploaded = None
    try:
        uploaded = client.files.upload(file=str(image_path))
        interaction = client.interactions.create(
            model=MODEL,
            input=[
                {"type": "text", "text": prompt},
                {"type": "image", "uri": uploaded.uri, "mime_type": uploaded.mime_type},
            ],
        )
        result = apply_verification(parse_model_json(interaction.output_text))
        result["_metadata"] = {"source_image": image_path.name, "model": MODEL}
        return result
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception as exc:  # cleanup must never mask the real result/error
                log.warning("could not delete uploaded file %s: %s", getattr(uploaded, "name", "?"), exc)


def compare_banner_to_landing(banner: dict, landing: dict, aliases=None) -> VisionVerdict | None:
    """Same brand on the banner image and the landing page? None when either side names no brand."""
    banner_brands = [b for b in banner.get("brands_mentioned") or [] if isinstance(b, str)]
    landing_brands = [b for b in landing.get("brands_mentioned") or [] if isinstance(b, str)]
    if not banner_brands or not landing_brands:
        return None
    if brands_overlap(banner_brands, landing_brands, aliases):
        return VisionVerdict("MATCH", f"banner brands {banner_brands} appear on the landing page")
    return VisionVerdict("DIFFERENT", f"banner brands {banner_brands} vs landing page brands {landing_brands}")
