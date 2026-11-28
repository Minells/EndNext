"""Bound contract processing in a separate, terminable CLI process."""

import subprocess
import sys

CONTRACT_TIMEOUT = 30.0
SCAN_TIMEOUT = 90.0


def main() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] in {"inspect", "check", "plan", "scan"}:
        timeout = SCAN_TIMEOUT if arguments[0] == "scan" else CONTRACT_TIMEOUT
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-X",
                    f"utf8={sys.flags.utf8_mode}",
                    "-m",
                    "endnext.cli",
                    *arguments,
                ],
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("ERROR [execution] Contract processing timed out.", file=sys.stderr)
            return 2
        return result.returncode if result.returncode in (0, 1, 2) else 2

    from .cli import main as run

    return run(arguments)
