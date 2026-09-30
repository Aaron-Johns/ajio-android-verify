"""Hero carousel helpers for the feed check: which banners are carousel slides, and a retrying vision call."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from qa.feed_client import Banner
from qa.spotcheck import vision

log = logging.getLogger("qa.spotcheck.hero")


def hero_candidates(banners: list[Banner]) -> list[Banner]:
    return [b for b in banners if b.block_index is not None and b.section_type == "hybrid-dynamic-banner" and b.image_url]


def _analyze(analyzer, hero_shot: str, attempts: int = 3) -> dict:
    """Read the banner with the vision model, retrying transient failures (timeouts, 5xx)."""
    for attempt in range(1, attempts + 1):
        try:
            return analyzer(Path(hero_shot))
        except vision.VisionUnavailable:
            raise
        except Exception as exc:
            if attempt == attempts:
                raise
            log.warning("vision call failed (%s), retrying", type(exc).__name__)
            time.sleep(2.0 * attempt)
