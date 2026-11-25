"""Shared-text discovery: wording that recurs across the letters of a portfolio.

Deterministic, no model involved. Every body paragraph, letterhead and footer block of every
letter is normalized like the duplicate detection does it (lower case, digit runs become
``#``) and turned into a set of 5-word shingles. Texts in the same place (header, body or
footer) whose Jaccard similarity reaches ``shared_text_threshold`` are linked; linked texts
found in at least ``shared_text_min_letters`` letters form a group.

A group is a *candidate* for a reusable text fragment, nothing more. ``UNVERIFIED``: how a
group would map to an AEM document fragment has not been checked against Adobe documentation.

Limits: a paragraph that continues on the next page is read as two texts; addresses,
subjects, greetings, closings, tables and unlabeled blocks are not compared.
"""

from __future__ import annotations

import csv
import difflib
import re
import zlib
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, ScoringConfig
from .extract import LetterStructure
from .llm import LLMCallLimit, LLMError

#: Words per shingle, as in the near-duplicate measure.
SHINGLE_WORDS = 5
#: Headers and footers are short, so they need fewer words than a body paragraph.
MIN_EDGE_WORDS = 2
#: Naming stops after this many failures in a row.
MAX_ERRORS_IN_A_ROW = 3
#: Most characters of a group's text that are sent to a model.
MAX_SENT_TEXT = 800
#: Stands for wording that is not the same in every letter of a group.
GAP = "[...]"

PLACES = {"letterhead": "header", "body": "body", "footer": "footer"}

SHARED_COLUMNS = [
    "group",
    "place",
    "letters",
    "occurrences",
    "min_similarity",
    "text",
    "files",
    "ai_name",
    "ai_description",
]


@dataclass(frozen=True)
class TextUnit:
    """One paragraph, header or footer of one letter."""

    letter: str
    place: str
    text: str
    #: ``text`` with the letter's own address lines masked; the only form a model may see.
    masked: str


@dataclass
class SharedGroup:
    """Texts that are (nearly) the same in several letters."""

    id: str
    place: str
    units: list[TextUnit]
    #: Lowest similarity among the links that formed the group.
    min_similarity: float
    ai: dict[str, Any] = field(default_factory=dict)
    ai_error: str = ""

    @property
    def letters(self) -> list[str]:
        """Names of the letters the text appears in."""
        return sorted({u.letter for u in self.units})

    @property
    def text(self) -> str:
        """The most common wording of the group."""
        return Counter(u.text for u in self.units).most_common(1)[0][0]


def normalize(text: str) -> list[str]:
    """Lower-case words of ``text`` with every digit run replaced by ``#``."""
    return re.findall(r"[^\W\d_]+|#", re.sub(r"\d+", "#", text.lower()))


def shingles(words: Sequence[str]) -> frozenset[int]:
    """Hashed 5-word shingles; a shorter text is one shingle, so it only matches itself."""
    k = min(SHINGLE_WORDS, len(words)) or 1
    return frozenset(
        zlib.crc32(" ".join(words[i : i + k]).encode("utf-8")) for i in range(len(words) - k + 1)
    )


def letter_units(letter: LetterStructure, name: str) -> list[TextUnit]:
    """The texts of ``letter`` that can be shared with other letters."""
    own = [ln for b in letter.blocks_with("address") for ln in b.lines if len(ln) > 2]
    out = []
    for b in letter.blocks:
        place = PLACES.get(b.role)
        if place is None:
            continue
        masked = b.text
        for line in sorted(own, key=len, reverse=True):
            masked = re.sub(re.escape(line), GAP, masked, flags=re.IGNORECASE)
        out.append(TextUnit(name, place, b.text, masked))
    return out


def find_shared(
    units: Sequence[TextUnit], config: ScoringConfig | None = None
) -> list[SharedGroup]:
    """Group the texts that recur across letters, most letters first (ids ``F1``, ``F2``, ...)."""
    cfg = config or DEFAULT_CONFIG
    # Identical normalized texts are one item, so a common footer is compared once.
    items: dict[tuple[str, str], list[TextUnit]] = defaultdict(list)
    for u in units:
        words = normalize(u.text)
        if len(words) >= (cfg.shared_text_min_words if u.place == "body" else MIN_EDGE_WORDS):
            items[(u.place, " ".join(words))].append(u)
    keys = sorted(items)
    sets = [shingles(text.split()) for _, text in keys]
    parent = list(range(len(keys)))
    lowest: dict[int, float] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[tuple[str, int], list[int]] = defaultdict(list)
    for j, (place, _) in enumerate(keys):
        overlap: Counter[int] = Counter()
        for h in sets[j]:
            overlap.update(seen[(place, h)])
            seen[(place, h)].append(j)
        for i, common in sorted(overlap.items()):
            similarity = common / (len(sets[i]) + len(sets[j]) - common)
            if similarity >= cfg.shared_text_threshold:
                a, b = find(i), find(j)
                low = min(lowest.get(a, 1.0), lowest.get(b, 1.0), similarity)
                parent[b] = a
                lowest[a] = low
    linked: dict[int, list[TextUnit]] = defaultdict(list)
    for i, key in enumerate(keys):
        linked[find(i)].extend(items[key])
    groups = [
        SharedGroup("", members[0].place, members, round(lowest.get(root, 1.0), 2))
        for root, members in linked.items()
        if len({u.letter for u in members}) >= cfg.shared_text_min_letters
    ]
    groups.sort(key=lambda g: (-len(g.letters), g.place, g.text))
    for n, g in enumerate(groups, start=1):
        g.id = f"F{n}"
    return groups


# ---------------------------------------------------------------- AI part
def common_text(texts: Sequence[str]) -> str:
    """The words every text has in common, in order; the rest becomes :data:`GAP`.

    Digits are always replaced by ``#``, even where they happen to agree.
    """
    split = [re.sub(r"\d+", "#", t).split() for t in dict.fromkeys(texts)]
    common = split[0]
    for other in split[1:]:
        match = difflib.SequenceMatcher(None, common, other, autojunk=False)
        merged: list[str] = []
        a = b = 0
        for block in match.get_matching_blocks():
            if block.a > a or block.b > b:
                merged.append(GAP)
            merged += common[block.a : block.a + block.size]
            a, b = block.a + block.size, block.b + block.size
        common = merged
    out: list[str] = []
    for word in common:
        if word != GAP or out[-1:] != [GAP]:
            out.append(word)
    return " ".join(out)


def fragment_payload(group: SharedGroup) -> dict[str, Any]:
    """What the ``fragments`` task sends: the wording common to every letter of the group."""
    return {
        "place": group.place,
        "letters": len(group.letters),
        "text": common_text([u.masked for u in group.units])[:MAX_SENT_TEXT],
    }


def name_with_ai(groups: Sequence[SharedGroup], assistant: Any) -> str:
    """Ask for a name and description per group; return why it stopped early, if it did."""
    failures = 0
    for g in groups:
        try:
            g.ai = assistant.name_fragment(fragment_payload(g)) or {}
            failures = 0
        except LLMCallLimit as exc:
            return f"AI step stopped: {exc}."
        except LLMError as exc:
            g.ai_error = str(exc)
            failures += 1
            if failures >= MAX_ERRORS_IN_A_ROW:
                return f"Fragment naming stopped after {failures} failures in a row (last: {exc})."
    return ""


# ---------------------------------------------------------------- output
def write_shared_csv(groups: Sequence[SharedGroup], path: Path) -> Path:
    """Write one row per group (UTF-8 with BOM so Excel opens it correctly)."""
    from .portfolio import _csv_safe  # portfolio imports this module

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(SHARED_COLUMNS)
        for g in groups:
            row = [g.id, g.place, len(g.letters), len(g.units), g.min_similarity, g.text,
                   "; ".join(g.letters), g.ai.get("name", ""),
                   g.ai.get("description", "")]  # fmt: skip
            writer.writerow([_csv_safe(v) for v in row])
    return path


def _md_text(text: str, limit: int = 160) -> str:
    short = text if len(text) <= limit else text[:limit].rstrip() + "..."
    return re.sub(r"([\\`*_|<>\[\]#])", r"\\\1", short)


def shared_markdown(groups: Sequence[SharedGroup], config: ScoringConfig | None) -> list[str]:
    """The "Shared text" section of ``portfolio.md``."""
    cfg = config or DEFAULT_CONFIG
    out = ["", f"## Shared text ({len(groups)} candidate(s))", ""]
    out.append(
        "Texts that recur across letters, found by rules (5-word shingles, similarity at or "
        f"above {cfg.shared_text_threshold:g}, digits normalized). Each is a candidate for a "
        "reusable text fragment; review before use. `UNVERIFIED`: any mapping to AEM document "
        "fragments. The full texts and letters are in `shared-text.csv`."
    )
    out.append("")
    if not groups:
        return [*out, "None found."]
    out += ["| Candidate | Place | Letters | Occurrences | Text |", "|---|---|---:|---:|---|"]
    for g in groups:
        out.append(
            f"| {g.id} | {g.place} | {len(g.letters)} | {len(g.units)} | {_md_text(g.text)} |"
        )
    if any(g.ai or g.ai_error for g in groups):
        out.append("")
        for g in groups:
            if g.ai:
                out.append(f"- {g.id} (AI): {g.ai['name']}. {g.ai['description']}")
            if g.ai_error:
                out.append(f"- {g.id}: AI step warning: {g.ai_error}")
    return out
