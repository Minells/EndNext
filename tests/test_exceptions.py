from datetime import date

import pytest

from endnext.exceptions import load_exceptions
from endnext.inputs import InputError

FINGERPRINT = "sha256:" + "a" * 64
HEADER = f'[exceptions."{FINGERPRINT}"]\n'
VALID = 'reason = "Temporary test policy"\nexpires_on = 2030-04-08\n'


def test_exception_expires_at_the_start_of_the_specified_day(tmp_path):
    policy = tmp_path / "exceptions.toml"
    policy.write_text(HEADER + VALID)
    assert (
        load_exceptions(policy, today=date(2030, 4, 7))[FINGERPRINT].status == "active"
    )
    assert (
        load_exceptions(policy, today=date(2030, 4, 8))[FINGERPRINT].status == "expired"
    )
    assert (
        load_exceptions(policy, today=date(2030, 4, 9))[FINGERPRINT].status == "expired"
    )
    policy.write_text("[exceptions]\n")
    assert load_exceptions(policy, today=date(2030, 4, 9)) == {}


@pytest.mark.parametrize(
    "data",
    [
        "",
        "exceptions = []",
        "version = 1\n[exceptions]",
        "[exceptions.invalid]\n" + VALID,
        f'[exceptions]\n"{FINGERPRINT}" = false',
        HEADER + VALID + "enabled = false",
        HEADER + "reason = 1\nexpires_on = 2030-04-08",
        HEADER + 'reason = "   "\nexpires_on = 2030-04-08',
        HEADER + f'reason = "{"a" * 301}"\nexpires_on = 2030-04-08',
        HEADER + 'reason = "one\\ntwo"\nexpires_on = 2030-04-08',
        HEADER + 'reason = "Temporary"\nexpires_on = "2030-04-08"',
        HEADER + 'reason = "Temporary"\nexpires_on = 2030-04-08T12:00:00Z',
        HEADER + VALID + HEADER + VALID,
    ],
)
def test_rejects_invalid_exception_policy(tmp_path, data):
    path = tmp_path / "exceptions.toml"
    path.write_text(data)
    with pytest.raises(InputError):
        load_exceptions(path, today=date(2030, 4, 7))
