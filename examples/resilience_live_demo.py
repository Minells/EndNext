"""Capture one demo client's result against a live EndNext proxy."""

import argparse
import json
import subprocess
import time
from pathlib import Path
from select import select

import httpx

from endnext.resilience import demo_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", choices=("retry", "single"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--upstream", default="http://127.0.0.1:8767")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    observation = args.output / "client.json"
    trace = args.output / "proxy.jsonl"
    scenario = Path(__file__).with_name("resilience-proxy.toml")
    with subprocess.Popen(
        [
            "endnext",
            "resilience-proxy",
            str(scenario),
            "--upstream",
            args.upstream,
            "--authorized",
            "--port",
            "0",
            "--observation",
            str(observation),
        ],
        stdout=subprocess.PIPE,
        text=True,
    ) as proxy:
        try:
            if not select([proxy.stdout], [], [], 20)[0]:
                raise RuntimeError("The proxy did not announce readiness.")
            ready_line = proxy.stdout.readline()
            ready = json.loads(ready_line)
            with httpx.Client(base_url=ready["origin"], trust_env=False) as client:
                started = time.monotonic_ns()
                status = demo_client(
                    client, attempts=3 if args.client == "retry" else 1
                )
                elapsed_ms = (time.monotonic_ns() - started + 999_999) // 1_000_000
            pending = observation.with_suffix(".tmp")
            pending.write_text(
                json.dumps(
                    {
                        "run_id": ready["run_id"],
                        "status": status,
                        "elapsed_ms": elapsed_ms,
                    }
                ),
                encoding="utf-8",
            )
            pending.replace(observation)
            rest, _ = proxy.communicate(timeout=10)
            trace.write_text(ready_line + rest, encoding="utf-8")
        finally:
            if proxy.poll() is None:
                proxy.kill()
                proxy.wait(timeout=5)
    return proxy.returncode


if __name__ == "__main__":
    raise SystemExit(main())
