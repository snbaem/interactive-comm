"""The docs must match the program: options, links, images and the UNVERIFIED list."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from icsel.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
DOCS = [
    *sorted(ROOT.glob("*.md")),
    *sorted((ROOT / "docs").glob("*.md")),
    ROOT / "src" / "icsel" / "prompts" / "README.md",
]
OPTION = re.compile(r"(?<![\w-])--[a-z][a-z-]*[a-z]")
#: Lines about other programs.
OTHER_PROGRAM = re.compile(r"\b(?:ruff|pip|git|scripts/\w+\.py)\b")
#: "icsel <command> ..." up to the end of the code span, line or comment.
COMMAND = re.compile(r"\bicsel ([a-z][a-z-]+)([^`#\n]*)")


def _help() -> dict[str, set[str]]:
    """Options in the ``--help`` text of every subcommand."""
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    ansi = re.compile(r"\x1b\[[0-9;]*m")  # Python 3.14 can color help text
    return {
        name: set(OPTION.findall(ansi.sub("", p.format_help()))) for name, p in sub.choices.items()
    }


HELP = _help()
KNOWN = set().union(*HELP.values()) | {"--version"}


def option_problems(text: str) -> list[str]:
    """Options in ``text`` that the named command, or any command, does not have."""
    problems = []
    for line in re.sub(r"\\\n\s*", "", text).splitlines():
        if not OTHER_PROGRAM.search(line):
            problems += [f"{o}: no such option" for o in OPTION.findall(line) if o not in KNOWN]
        for command, rest in COMMAND.findall(line):
            if command not in HELP:
                continue
            for option in OPTION.findall(rest):
                if option not in HELP[command]:
                    problems.append(f"icsel {command} has no {option}")
    return problems


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_options_exist(path: Path) -> None:
    assert option_problems(path.read_text(encoding="utf-8")) == []


def test_the_check_itself_finds_mistakes() -> None:
    assert option_problems("use `--no-such-thing`") == ["--no-such-thing: no such option"]
    assert option_problems("icsel samples --yes") == ["icsel samples has no --yes"]
    assert option_problems("icsel eval x \\\n  --proposals") == ["icsel eval has no --proposals"]
    assert option_problems("ruff format --check .\nicsel match x --llm anthropic") == []


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_local_links_and_images_exist(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        file = target.split("#")[0]
        if file and "://" not in target:
            assert (path.parent / file).exists(), f"{path.name}: {target}"


def test_review_lists_every_unverified_file() -> None:
    review = (ROOT / "REVIEW.md").read_text(encoding="utf-8")
    sources = [*(ROOT / "src").rglob("*.py"), *(ROOT / "src").rglob("*.md"), *DOCS]
    marked = {
        p.relative_to(ROOT).as_posix()
        for p in sources
        if p.name != "REVIEW.md" and "UNVERIFIED" in p.read_text(encoding="utf-8")
    }
    missing = {name for name in marked if f"`{name}`" not in review}
    assert missing == set()
