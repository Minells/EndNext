"""Render the EndNext help heading for the current terminal."""

import os

_MARK = (" █████", "▄ ▀▀▄ ", "█████ ")
_GRAPHITE_ON_SIGNAL = "\033[38;2;20;22;24;48;2;230;255;82m"
_RESET = "\033[0m"


def help_heading(output: object) -> str:
    isatty = getattr(output, "isatty", None)
    encoding = getattr(output, "encoding", None)
    if (
        not callable(isatty)
        or not isatty()
        or os.environ.get("TERM") == "dumb"
        or (encoding or "").lower() not in {"utf-8", "utf8"}
    ):
        return "EndNext"

    top, center, bottom = _MARK
    if not os.environ.get("NO_COLOR") and os.environ.get("COLORTERM", "").lower() in {
        "24bit",
        "truecolor",
    }:
        return (
            f"{_GRAPHITE_ON_SIGNAL}{top}{_RESET}\n"
            f"{_GRAPHITE_ON_SIGNAL}{center}{_RESET}  EndNext\n"
            f"{_GRAPHITE_ON_SIGNAL}{bottom}{_RESET}"
        )
    return f"{top}\n{center}  EndNext\n{bottom.rstrip()}"
