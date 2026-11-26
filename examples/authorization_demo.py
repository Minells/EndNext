"""Serve a local authorization policy for two synthetic identities."""

import argparse
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OWNERS = {"/documents/alice-note": "alice", "/documents/bob-note": "bob"}


def handler_for(identities, *, healthy):
    class DemoHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            identity = identities.get(self.headers.get("Authorization"))
            owner = OWNERS.get(self.path)
            if identity is None:
                status = HTTPStatus.UNAUTHORIZED
            elif owner is None:
                status = HTTPStatus.NOT_FOUND
            elif identity != owner or (owner == "bob" and not healthy):
                status = HTTPStatus.FORBIDDEN
            else:
                status = HTTPStatus.NO_CONTENT
            self.send_response(status)
            if status == HTTPStatus.UNAUTHORIZED:
                self.send_header("WWW-Authenticate", "Bearer")
            if status != HTTPStatus.NO_CONTENT:
                self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    return DemoHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--healthy", action="store_true", help="Use the expected policy."
    )
    args = parser.parse_args()
    alice = os.environ.get("ENDNEXT_DEMO_ALICE_TOKEN")
    bob = os.environ.get("ENDNEXT_DEMO_BOB_TOKEN")
    if not alice or not bob or alice == bob:
        parser.error(
            "Set distinct synthetic tokens for Alice and Bob in the environment."
        )
    identities = {f"Bearer {alice}": "alice", f"Bearer {bob}": "bob"}
    with ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler_for(identities, healthy=args.healthy)
    ) as server:
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
