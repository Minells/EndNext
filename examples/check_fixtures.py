"""Verify multiple saved responses using one loaded contract."""

import argparse
import json
import sys
from pathlib import Path

from endnext.api_v1 import ExecutionError, FixtureChecker, InputError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("fixtures", type=Path, nargs="+")
    args = parser.parse_args()
    exit_code = 0
    try:
        checker = FixtureChecker(args.contract)
        for fixture in args.fixtures:
            result = checker.check(fixture)
            print(json.dumps(result.to_report()))
            exit_code = max(exit_code, result.exit_code)
    except (InputError, ExecutionError) as error:
        print(error, file=sys.stderr)
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
