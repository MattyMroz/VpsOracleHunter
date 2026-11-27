"""Reject lines with broken encoding (U+FFFD or Polish mojibake) or, with --ascii, any byte above 0x7F."""

import argparse
import sys
from pathlib import Path
from typing import Final

REPLACEMENT_CHAR: Final[bytes] = b"\xef\xbf\xbd"
"""U+FFFD in UTF-8, the trace a broken decode leaves behind."""

POLISH_TEXT_CHARS: Final[str] = "".join(
    map(
        chr,
        (
            *(0x0105, 0x0107, 0x0119, 0x0142, 0x0144, 0x00F3, 0x015B, 0x017A, 0x017C),
            *(0x0104, 0x0106, 0x0118, 0x0141, 0x0143, 0x00D3, 0x015A, 0x0179, 0x017B),
            *(0x201E, 0x201D, 0x2013, 0x2014),
        ),
    )
)
"""The Polish letters, quotes and dashes whose UTF-8 bytes turn into mojibake when read in a Windows code page."""

MOJIBAKE_CODE_PAGES: Final[tuple[str, ...]] = ("cp1250", "cp1252")
"""The Windows code pages that PowerShell uses to read a UTF-8 file without a byte order mark."""

SAVE_HINT: Final[str] = "save the file as UTF-8; read and write it with an explicit UTF-8 encoding"
"""The fix for a line that a read or write without an explicit encoding damaged."""


def _windows_char(byte: int, code_page: str) -> str:
    """Return the character of the byte in the code page, or the character with its number, as Windows decodes it."""
    try:
        return bytes((byte,)).decode(code_page)
    except UnicodeDecodeError:
        return chr(byte)


def _misread(text: str, code_page: str) -> str:
    """Return the text after one wrong read of its UTF-8 bytes in the code page."""
    return "".join(_windows_char(byte, code_page) for byte in text.encode())


def _mojibake() -> dict[bytes, str]:
    """Return the Polish character by the UTF-8 bytes of its mojibake after one or two wrong reads, longest first."""
    sequences: dict[bytes, str] = {}
    for char in POLISH_TEXT_CHARS:
        for first in MOJIBAKE_CODE_PAGES:
            once: str = _misread(char, first)
            sequences.setdefault(once.encode(), char)
            for second in MOJIBAKE_CODE_PAGES:
                sequences.setdefault(_misread(once, second).encode(), char)
    return dict(sorted(sequences.items(), key=lambda item: len(item[0]), reverse=True))


MOJIBAKE: Final[dict[bytes, str]] = _mojibake()
"""The Polish character by the UTF-8 bytes of its mojibake."""


def _problem(line: bytes, *, ascii_only: bool) -> str | None:
    """Return "<rule>: <what>: <fix>" for the line under the selected rule, or None."""
    if ascii_only:
        return None if line.isascii() else "ascii only: non-ASCII byte: write the text in ASCII"
    if REPLACEMENT_CHAR in line:
        return f"encoding: U+FFFD replacement character: retype the lost character and {SAVE_HINT}"
    for sequence, char in MOJIBAKE.items():
        if sequence in line:
            code_points: str = " ".join(f"U+{ord(found):04X}" for found in sequence.decode())
            return (
                f"encoding: mojibake {code_points} of {char!a} "
                f"(UTF-8 read once or twice as {' or '.join(MOJIBAKE_CODE_PAGES)}): retype {char!a} and {SAVE_HINT}"
            )
    return None


def main() -> int:
    """Report every offending line of the argv files; return 1 if there is one."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="text_guard")
    parser.add_argument("--ascii", action="store_true", help="reject every non-ASCII byte")
    parser.add_argument("paths", nargs="*", type=Path)
    args: argparse.Namespace = parser.parse_args()
    paths: list[Path] = args.paths
    ascii_only: bool = args.ascii

    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_bytes().splitlines(), start=1):
            problem: str | None = _problem(line, ascii_only=ascii_only)
            if problem is not None:
                problems.append(f"{path}:{number}: {problem}")

    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
