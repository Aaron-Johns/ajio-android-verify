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
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from qa.envfile import load_env

log = logging.getLogger("qa.spotcheck.vision")

MODEL = "gemma-4-31b-it"
TIMEOUT_MS = 180_000  # [A] per-HTTP-call cap (files.upload, interactions.create) - the SDK has no
                      # default, so a stalled connection (e.g. after a 5xx) would otherwise hang the
                      # calling worker thread forever instead of raising something _analyze()'s own
                      # retry loop can catch. 180s (not something shorter) because a normal, healthy
                      # banner check was measured taking 90-115s on average across real runs, with
                      # legitimate outliers up to ~250s (image analysis + the listing lookup combined) -
                      # a tighter cap would cut off working-but-slow calls, not just genuine hangs
                      # (an actual observed hang ran ~960s/16min, so this still catches that easily)

CALLS_PER_MINUTE = 7   # [A] cap on Gemma requests (every try counts), whatever the worker count. Sized to the account's
                       # 16K tokens/min: a banner call sends ~1,770 input tokens (the reply's own usage, 2026-10-05: prompt 681 + image
                       # 1,089, the same at any picture size; count_tokens says 258 for the image and is wrong) plus ~100 reply tokens
                       # and ~420 thinking tokens, so ~9 a minute would be the input ceiling; 7 (~12.4K) leaves the same headroom 6 had with the old, longer prompt (9 with it was over). Override with GEMMA_CALLS_PER_MINUTE (0 = no limit).


PACER_STATE = Path(__file__).resolve().parents[1] / ".cache" / "gemma_pacer.json"   # qa/.cache/ (gitignored)
MAX_AHEAD_S = 3600.0   # a reserved slot further away than this is a leftover / a clock change, not a real queue


@contextmanager
def _file_lock(path: Path):
    """An exclusive lock on `path` that every process on this machine respects (msvcrt on Windows, flock elsewhere).
    Held for microseconds - only around reading and writing the pacer's one number."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)     # not LK_LOCK: that one retries only once a second
                    break
                except OSError:
                    time.sleep(0.005)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()


class CallPacer:
    """Spaces Gemma requests at least 60/calls_per_minute seconds apart (+5% margin), across every worker thread of
    this process, first come first served. More workers than the limit needs don't send faster - the extra ones
    just wait their turn - so raising the worker count can never push the account over its per-minute limits.

    With `shared_path` the next free slot is kept in a small file (under a file lock) instead of in memory, so the
    limit holds across every process on the machine too: two runs going at once, a scheduled run plus a manual one,
    or a per-banner Retry all queue behind each other instead of each pacing only itself. If the file can't be used
    (read-only folder, ...) it quietly falls back to pacing this process alone."""

    def __init__(self, calls_per_minute: float, clock=None, sleep=time.sleep, shared_path: Path | None = None):
        self.interval = 60.0 / calls_per_minute * 1.05 if calls_per_minute and calls_per_minute > 0 else 0.0
        self._shared = shared_path
        # processes must agree on "now", so a shared pacer runs on the wall clock (a monotonic clock is per-process)
        self._clock = clock or (time.time if shared_path else time.monotonic)
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0

    def _reserve(self) -> tuple[float, float]:
        """(the time this request may go out, the time it asked), and books the slot after it. The clock is read only
        once the lock is held: reading it first and then waiting for a busy lock would book a slot from a stale "now"
        and let two processes fire together."""
        if self._shared is not None:
            try:
                with _file_lock(self._shared.with_suffix(".lock")):
                    now = self._clock()
                    try:
                        booked = float(json.loads(self._shared.read_text(encoding="utf-8"))["next"])
                    except (OSError, ValueError, KeyError, TypeError):
                        booked = 0.0
                    slot = max(now, min(booked, now + MAX_AHEAD_S))
                    self._shared.write_text(json.dumps({"next": slot + self.interval}), encoding="utf-8")
                    return slot, now
            except OSError as exc:
                log.warning("shared Gemma pacer unavailable (%s) - pacing this process only", exc)
        now = self._clock()
        slot = max(now, self._next)
        self._next = slot + self.interval
        return slot, now

    def acquire(self, on_wait=None) -> float:
        """Blocks until this request may go out; returns how long it waited (seconds). `on_wait` is called once,
        before sleeping, only if there is a wait."""
        if not self.interval:
            return 0.0
        with self._lock:
            slot, now = self._reserve()
        delay = slot - now
        if delay > 0.05:
            if on_wait:
                on_wait()
            self._sleep(delay)
        return delay


_pacer: CallPacer | None = None
_pacer_lock = threading.Lock()


def get_pacer() -> CallPacer:
    """The process-wide pacer, built on first use from GEMMA_CALLS_PER_MINUTE (default CALLS_PER_MINUTE); shared with every
    other process through qa/.cache/gemma_pacer.json unless GEMMA_PACER_SHARED=0."""
    global _pacer
    with _pacer_lock:
        if _pacer is None:
            load_env()
            try:
                per_minute = float(os.environ.get("GEMMA_CALLS_PER_MINUTE", CALLS_PER_MINUTE))
            except ValueError:
                per_minute = CALLS_PER_MINUTE
            shared = os.environ.get("GEMMA_PACER_SHARED", "1") != "0"    # 0 = pace this process alone
            _pacer = CallPacer(per_minute, shared_path=PACER_STATE if shared else None)
        return _pacer


_local = threading.local()


@contextmanager
def reporting(note):
    """Lets a request that has to wait its turn say so through `note` (the UI's "Waiting turn" tag) and then put
    back the step it was on. Yields `note` wrapped to remember the latest step; use the wrapped one."""
    last = {"text": None}

    def wrapped(text: str) -> None:
        last["text"] = text
        note(text)
    _local.note, _local.last = note, last
    try:
        yield wrapped
    finally:
        _local.note = _local.last = None


def _wait_for_turn() -> None:
    note, last = getattr(_local, "note", None), getattr(_local, "last", None)
    waited = get_pacer().acquire(on_wait=(lambda: note("Waiting turn")) if note else None)
    if waited > 0.05 and note and last and last["text"]:
        note(last["text"])


# Shortened 2026-10-05 from the ported prompt (1,061 tokens) to 681 tokens with the same output fields minus `clothing_seen` (nothing read it).
# Compared on 14 saved banners, old prompt twice vs new: same deal / audience (13 of 14) / "& more" / spelling / verification reads, brands within the
# old prompt's own run-to-run noise; ~20% fewer tokens a call (input 2,153 -> 1,773, thinking ~590 -> 418). The original is in git history.
PROMPT = """
Analyze this ecommerce fashion banner carefully. Return ONLY valid JSON.

1. brands_mentioned
List every clothing/fashion brand on the banner: a visibly written name, a wordmark, or a recognizable graphical logo (a logo with no readable text counts). A banner can promote several brands: a short name in its own distinct, title- or wordmark-like styling counts even if it might be a collection or product-line name, and even if a different, more prominent logo is also shown. Do not keep only the most prominent one: a downstream check reconciles this list with the product listing, so omitting a real brand is far worse than listing an extra candidate. Generic promotional text (SALE, SHOP NOW, NEW, OFFER, COLLECTION, OFF, BUY NOW) is not a brand.

2. deal_offered
The promotional offer shown, keeping the important numbers and meaning (e.g. "40% OFF", "UP TO 50% OFF", "BUY 2 GET 1 FREE", "MIN 40% OFF"). Do not invent a deal if none is clearly visible.

3. spelling_errors
Misspelled words in the visible text (brand names excluded), each with its correction, e.g. "SUMER SALE" -> {"word": "sumer", "correction": "summer"}. Do not flag brand names or stylized wordmarks, intentional stylization (e.g. "SHOPPN'"), or non-English words. Empty list if there are none.

4. target_gender
Who the banner promotes products for, from explicit text ("Men's", "Women's", "Boys", "Girls", "Infants") and, failing that, the clothing/models shown. Exactly one of:
"men" (men's/boys' only), "women" (women's/girls' only), "boys" (specifically boys, not men), "girls" (specifically girls, not women), "infants" (babies/toddlers), "men_and_women" (clearly both together, or a brand/store-wide banner), "girls_and_boys" (kids-wide, no adult products), "unclear" (cannot confidently tell, or fits no category). Do not guess a category just to avoid "unclear".

5. more_brands_than_named
true only if the banner's own text says there are more brands beyond those named ("& more", "and more", "+ more", "many more brands"); otherwise false. Do not guess.

6. verification_required
true if you cannot confidently determine the brand(s) or the deal; then "verification" must be exactly "User verification required", otherwise "". Do not guess just to avoid verification.

Return EXACTLY this structure and nothing outside the JSON:

{
  "brands_mentioned": [],
  "deal_offered": "",
  "spelling_errors": [
    {"word": "", "correction": ""}
  ],
  "target_gender": "",
  "more_brands_than_named": false,
  "verification_required": false,
  "verification": ""
}

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
    return genai.Client(api_key=api_key, http_options={"timeout": TIMEOUT_MS})


HERO_EXTRA = ("This is a hero promotional banner. List only the brands the banner is PROMOTING: brands in its "
              "headline or footer text, wordmarks or logo band. Ignore brand names that appear on props, book spines, "
              "product labels, packaging or background objects in the photo.")


def analyze_image(image_path: str | Path, client=None, extra: str = "") -> dict:
    """Analyse one image and return the parsed, verification-checked result dict. `extra` adds instructions after PROMPT."""
    image_path = Path(image_path)
    prompt = PROMPT + (f"\n\nAdditional instructions:\n{extra}\n" if extra else "")
    client = client or get_client()
    uploaded = None
    _wait_for_turn()          # before the upload, so a waiting request holds nothing on Google's side
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


