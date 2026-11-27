"""Load explicitly approved, time-limited exceptions for finding fingerprints."""

import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from .inputs import InputError, read_file

_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class FindingException:
    reason: str
    expires_on: str
    status: Literal["active", "expired"]


def load_exceptions(path: Path, *, today: date) -> dict[str, FindingException]:
    try:
        data = tomllib.loads(read_file(path, "exceptions policy").decode("utf-8-sig"))
    except (ValueError, RecursionError):
        raise InputError("The exceptions policy contains invalid UTF-8 TOML.") from None
    table = data.get("exceptions")
    if set(data) != {"exceptions"} or not isinstance(table, dict):
        raise InputError("The policy requires a single exceptions table.")

    entries = {}
    for fingerprint, entry in table.items():
        if not _FINGERPRINT.fullmatch(fingerprint) or not isinstance(entry, dict):
            raise InputError(
                "Each exception must be a table keyed by a SHA-256 fingerprint."
            )
        reason = entry.get("reason")
        expiry = entry.get("expires_on")
        if (
            set(entry) != {"reason", "expires_on"}
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 300
            or not reason.isprintable()
            or type(expiry) is not date
        ):
            raise InputError(
                "Each exception requires a printable reason of up to 300 characters "
                "and an unquoted expires_on date."
            )
        entries[fingerprint] = FindingException(
            reason.strip(),
            expiry.isoformat(),
            "active" if today < expiry else "expired",
        )
    return entries
