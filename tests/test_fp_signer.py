"""Known-answer tests: signatures captured live from the AJIO app (analysis/traffic_capture/)."""
import pytest

from qa.fp_signer import sign

HOST = "api.services.ajio.com"
BASE = "/api/service/application/theme/v1.0/6924384620d2931b59eb94ec/"

VECTORS = [
    ("home", "20260921T041433Z", "v1.1:038129638b22db0f6b87c0e9d99e3065177f21efad262cd90450efd579954ceb"),
    ("menswear", "20260921T041448Z", "v1.1:a8d5826c8fa48eafdb3d6082f2f1a96daf3d6269500ab9c1bedfe58d61569daf"),
    ("kidswear", "20260921T041448Z", "v1.1:a0a8ef446a596a6b2ce2689609989a7ea685f31df071f8d8a3dff92260bcc916"),
    ("womenswear", "20260921T041448Z", "v1.1:c376b0cd0a55a9b6b9f367d6a92472e77a1e801e20ddd516a0e991500d407651"),
]


@pytest.mark.parametrize("slug,fp_date,expected", VECTORS)
def test_matches_captured_signature(slug, fp_date, expected):
    out = sign("GET", HOST, BASE + slug, "company=1",
               headers={"x-fp-sdk-version": "1.10.6-9"}, fp_date=fp_date)
    assert out["x-fp-signature"] == expected
    assert out["x-fp-date"] == fp_date
