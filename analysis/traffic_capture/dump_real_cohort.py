from mitmproxy import http

def response(flow: http.HTTPFlow) -> None:
    if "cohort/getUserSegmentAndCohort" not in flow.request.pretty_url:
        return
    auth = flow.request.headers.get("authorization", "")
    if len(auth) < 700:   # skip the guest-token call, only show the real logged-in one
        return
    print("auth length:", len(auth))
    body = flow.response.get_text(strict=False)
    print(body[:2000])
