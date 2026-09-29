from mitmproxy import http

def response(flow: http.HTTPFlow) -> None:
    try:
        body = flow.response.get_text(strict=False) or ""
    except Exception:
        return
    if "cohort" in body.lower() or "genesys" in body.lower():
        print("=== MATCH ===")
        print(flow.request.pretty_url)
        print(body[:4000])
        print()
