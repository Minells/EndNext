import json
import subprocess
import sys

from test_check import EXAMPLES


def test_offline_benchmark_confirms_reference_verdicts_and_inventory():
    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLES / "benchmark_offline.py"),
            "--operations",
            "6",
            "--rounds",
            "2",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    benchmark = json.loads(result.stdout)
    assert benchmark["operations"] == 6 and benchmark["rounds"] == 2
    assert benchmark["fixtures"] == 5 and benchmark["assertions"] == 24
    assert benchmark["repeatable"] is True
    assert benchmark["false_positives"] == benchmark["false_negatives"] == 0
    assert benchmark["mismatches"] == []
    assert set(benchmark["median_seconds"]) == {"reload", "reuse"}
    assert all(duration >= 0 for duration in benchmark["median_seconds"].values())
