"""Resolve command-line path arguments into concrete files, the same way on every OS.

PowerShell and ``cmd.exe`` do not expand ``*.pdf`` the way POSIX shells do, so
the CLI expands patterns itself and also accepts directories. Suffix matching
is case-insensitive (``LETTER.PDF`` counts) on every platform.
"""

from __future__ import annotations

import glob
from collections.abc import Iterable, Iterator
from pathlib import Path

_GLOB_CHARS = frozenset("*?[")


class InputError(ValueError):
    """Raised when a path argument matches nothing usable."""


def _has_glob(text: str) -> bool:
    return any(ch in _GLOB_CHARS for ch in text)


def _files_in(directory: Path, suffix: str, recursive: bool) -> Iterator[Path]:
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    for p in candidates:
        if p.is_file() and p.suffix.lower() == suffix:
            yield p


def iter_files(args: Iterable[Path | str], suffix: str, recursive: bool = False) -> Iterator[Path]:
    """Yield files with ``suffix`` named by ``args``, lazily and without duplicates.

    Each argument may be a file, a directory (its ``suffix`` files, sorted by
    name; with ``recursive``, also those in subfolders) or a glob pattern such as
    ``letters/*.pdf``. Files given explicitly are yielded whatever their suffix.

    Args:
        args: Paths, directories or glob patterns as typed by the user.
        suffix: File suffix to collect from directories, e.g. ``".pdf"``.
        recursive: Also descend into subdirectories of directory arguments.

    Raises:
        InputError: If an argument does not exist and matches no files.
    """
    suffix = suffix.lower()
    seen: set[Path] = set()
    for arg in args:
        path = Path(arg)
        if path.is_dir():
            found: Iterable[Path] = sorted(
                _files_in(path, suffix, recursive), key=lambda p: str(p).lower()
            )
        elif path.is_file():
            found = [path]
        elif _has_glob(str(arg)):
            matches = sorted(
                # glob.glob, unlike Path.glob, accepts absolute and drive-letter patterns.
                (Path(m) for m in glob.glob(str(arg), recursive=True)),  # noqa: PTH207
                key=lambda p: str(p).lower(),
            )
            found = [m for m in matches if m.is_file() and m.suffix.lower() == suffix]
            if not found:
                raise InputError(f"pattern matched no {suffix} files: {arg}")
        else:
            raise InputError(f"no such file or directory: {arg}")
        for f in found:
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                yield f


def collect_files(args: Iterable[Path | str], suffix: str, recursive: bool = False) -> list[Path]:
    """Like :func:`iter_files` but returns a list and fails if it is empty.

    Raises:
        InputError: If an argument is invalid or nothing at all was found.
    """
    arg_list = list(args)
    files = list(iter_files(arg_list, suffix, recursive))
    if not files:
        raise InputError(f"no {suffix} files found in: {', '.join(str(a) for a in arg_list)}")
    return files


def unique_stems(paths: list[Path]) -> dict[Path, str]:
    """Map each path to an output-safe name stem that is unique within ``paths``.

    Files with the same stem in different folders get the parent folder name
    prepended (``a/letter.pdf`` -> ``a__letter``), then a counter if needed.
    """
    out: dict[Path, str] = {}
    used: set[str] = set()
    counts: dict[str, int] = {}
    for p in paths:
        counts[p.stem.lower()] = counts.get(p.stem.lower(), 0) + 1
    for p in paths:
        stem = p.stem if counts[p.stem.lower()] == 1 else f"{p.parent.name}__{p.stem}"
        candidate, n = stem, 2
        while candidate.lower() in used:
            candidate, n = f"{stem}-{n}", n + 1
        used.add(candidate.lower())
        out[p] = candidate
    return out
