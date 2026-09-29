import pytest

from qa.spotcheck import vision


@pytest.fixture(autouse=True)
def no_gemma_pacing(monkeypatch):
    """Gemma requests are paced (6 a minute by default) in real runs; a test that calls analyze_image a few times
    must not sit through ~10 s waits, nor depend on what GEMMA_CALLS_PER_MINUTE the developer's .env sets.
    The pacer's own tests build their own CallPacer."""
    monkeypatch.setattr(vision, "_pacer", vision.CallPacer(0))
