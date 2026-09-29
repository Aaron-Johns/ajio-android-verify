from mitmproxy import http

def response(flow: http.HTTPFlow) -> None:
    if "/theme/v1.0/" not in flow.request.pretty_url or "/home" not in flow.request.pretty_url:
        return
    print("URL:", flow.request.pretty_url)
    for k, v in flow.request.headers.items():
        if k.lower() in ("authorization", "cookie", "x-fp-signature"):
            v = "<redacted>"
        print(f"  {k}: {v}")
    print()
