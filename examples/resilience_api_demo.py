"""Serve a synthetic API that uses the local resilience proxy as a dependency."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from endnext.resilience import demo_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("retry", "single"), default="single")
    args = parser.parse_args()

    class API(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/items/1":
                self.send_error(404)
                return
            with httpx.Client(
                base_url="http://127.0.0.1:8766", trust_env=False
            ) as dependency:
                status = demo_client(
                    dependency, attempts=3 if args.client == "retry" else 1
                )
            body = b'{"quantity": 3}' if status == 200 else b""
            self.send_response(status or 503)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 8768), API) as server:
        print("http://127.0.0.1:8768", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
