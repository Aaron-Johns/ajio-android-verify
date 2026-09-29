from mitmproxy import http

def request(flow: http.HTTPFlow) -> None:
    print(flow.request.pretty_url)
