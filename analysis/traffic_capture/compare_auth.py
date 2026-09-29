from mitmproxy import http

seen = {}

def response(flow: http.HTTPFlow) -> None:
    url = flow.request.pretty_url
    auth = flow.request.headers.get("authorization", "")
    if "cohort/getUserSegmentAndCohort" in url:
        seen["cohort"] = auth
    elif "/theme/v1.0/" in url and "/home" in url:
        seen.setdefault("theme", auth)
    elif "search-edge.services.ajio.com" in url:
        seen.setdefault("search_edge_auth_present", bool(auth))

def done():
    print("cohort == theme auth:", seen.get("cohort") == seen.get("theme"), "(both present:", bool(seen.get("cohort")) and bool(seen.get("theme")), ")")
    print("cohort auth starts with 'Bearer':", seen.get("cohort", "").startswith("Bearer"))
    print("cohort auth length:", len(seen.get("cohort", "")))
    print("theme auth length:", len(seen.get("theme", "")))
    print("search-edge sends any authorization header:", seen.get("search_edge_auth_present"))
