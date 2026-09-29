from mitmproxy import http

def response(flow: http.HTTPFlow) -> None:
    if "cohort/getUserSegmentAndCohort" not in flow.request.pretty_url:
        return
    print("URL:", flow.request.pretty_url)
    print("METHOD:", flow.request.method)
    print("REQUEST HEADERS:")
    for k, v in flow.request.headers.items():
        if k.lower() in ("authorization", "cookie", "x-fp-signature"):
            v = "<redacted>"
        print(f"  {k}: {v}")
    print("REQUEST BODY:", flow.request.get_text(strict=False) or "(none)")
    print()
