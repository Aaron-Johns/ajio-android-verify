from mitmproxy import http

count = 0

def response(flow: http.HTTPFlow) -> None:
    global count
    url = flow.request.pretty_url
    if "cohort/getUserSegmentAndCohort" not in url and ("/theme/v1.0/" not in url or "/home" not in url):
        return
    count += 1
    auth = flow.request.headers.get("authorization", "")
    kind = "cohort" if "cohort" in url else "theme"
    print(f"#{count} [{kind}] auth_len={len(auth)} status={flow.response.status_code if flow.response else '?'}")
