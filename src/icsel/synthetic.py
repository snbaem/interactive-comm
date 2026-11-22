"""Synthetic test data: layout-placed letters for the fictional Brightwater Utilities.

Everything produced here is invented. Names, addresses, account numbers and
letter text are placeholders and do not describe any real organization or
person. Each letter is drawn into the zones of one layout profile (its
"planted" expected layout), optionally with small offsets, so the matcher can
be tested against a known answer.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from .profile import LayoutProfile, Zone, load_profiles

COMPANY = "Brightwater Utilities"
COMPANY_FOOTER = "Brightwater Utilities | 100 Example Way, Riverton | brightwater.example"

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
SIZE = 10.0
LEADING = 12.0
PARA_GAP = 8.0

_SENTENCES = [
    "Thank you for being a Brightwater Utilities customer.",
    "This letter explains a change to your account that takes effect next billing cycle.",
    "Our records show a meter reading that differs from the estimate on your last bill.",
    "No action is needed if the details below are correct.",
    "You can review your usage history at any time in the customer portal.",
    "If you set up automatic payments, the new amount will be collected on the usual date.",
    "We adjusted the service charge to reflect the updated tariff for your area.",
    "A technician may visit to confirm the meter location; we will contact you first.",
    "Please keep this letter for your records.",
    "Questions about this notice can be sent to the customer care team.",
    "The adjustment appears as a separate line on your next statement.",
    "Customers on the budget plan will see the change spread across the next six months.",
]

ADDRESSES = {
    "short": ["Alex Sample", "42 Placeholder Lane", "Riverton ZZ 00001"],
    "medium": ["Jordan Example", "Unit 7", "18 Fictional Road", "Riverton ZZ 00002"],
    "long7": [
        "Morgan Testperson",
        "c/o Sample Property Services",
        "Building C, Floor 2",
        "1200 Imaginary Boulevard",
        "Northside District",
        "Riverton ZZ 00003",
        "Account 0000-0007",
    ],
    "long8": [
        "Casey Placeholder",
        "Attn: Accounts Payable",
        "Demo Holdings (Fictional)",
        "Suite 400",
        "77 Nowhere Avenue",
        "Eastgate Quarter",
        "Riverton ZZ 00004",
        "Account 0000-0008",
    ],
}


@dataclass
class LetterSpec:
    """Recipe for one synthetic letter."""

    id: str
    layout: str
    address: list[str]
    subject: str
    date: str | None = "March 3, 2026"
    paragraphs: int = 4
    offsets: dict[str, tuple[float, float]] = field(default_factory=dict)
    seed: int = 1
    note: str = ""
    # Letter "branding" and wording. Defaults give a Brightwater Utilities letter.
    company: str = COMPANY
    tagline: str = "Water | Power | Service"
    footer: str = COMPANY_FOOTER
    subject_prefix: str = "Subject: "
    salutation: str = "Dear Customer,"
    body: list[str] | None = None  # explicit paragraphs; None = generated from a seed
    closing: str = "Kind regards,"
    signer: list[str] = field(
        default_factory=lambda: ["Riley Placeholder", "Customer Care, Brightwater Utilities"]
    )


LETTERS: list[LetterSpec] = [
    LetterSpec(
        "L01-classic",
        "classic-left",
        ADDRESSES["short"],
        "Your updated tariff from April 2026",
        note="Baseline letter for classic-left.",
    ),
    LetterSpec(
        "L02-classic-jitter",
        "classic-left",
        ADDRESSES["medium"],
        "Meter reading review for account 0000-0002",
        offsets={"address": (6, 5), "subject": (0, 6), "date": (-10, 0)},
        seed=2,
        note="Classic-left with small offsets.",
    ),
    LetterSpec(
        "L03-subject-right",
        "subject-right",
        ADDRESSES["short"],
        "Planned maintenance in your area next month",
        seed=3,
        note="Baseline letter for subject-right.",
    ),
    LetterSpec(
        "L04-subject-right-3line",
        "subject-right",
        ADDRESSES["medium"],
        "Change to your payment plan and the schedule for the remaining instalments this year",
        seed=4,
        note="Subject wraps to three lines in the right-hand box.",
    ),
    LetterSpec(
        "L05-tall-address-8",
        "tall-address",
        ADDRESSES["long8"],
        "Statement of account",
        seed=5,
        note="Eight-line address: only tall-address has the capacity.",
    ),
    LetterSpec(
        "L06-tall-address-2page",
        "tall-address",
        ADDRESSES["long7"],
        "Annual summary of your service",
        paragraphs=14,
        seed=6,
        note="Seven-line address and a body that runs onto page 2.",
    ),
]


def _paragraphs(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return [" ".join(rng.sample(_SENTENCES, k=3)) for _ in range(n)]


def _baseline(page_h: float, top: float, size: float = SIZE) -> float:
    """Convert a top-left 'text top' to a reportlab baseline y."""
    return page_h - top - size * 0.8


def _draw_lines(
    c: canvas.Canvas,
    page_h: float,
    x: float,
    top: float,
    lines: list[str],
    font: str = FONT,
    size: float = SIZE,
) -> float:
    """Draw lines starting at ``top`` (top-left coords). Return the next free top."""
    c.setFont(font, size)
    for i, line in enumerate(lines):
        c.drawString(x, _baseline(page_h, top + i * LEADING, size), line)
    return top + len(lines) * LEADING


def _chrome(c: canvas.Canvas, page_h: float, spec: LetterSpec, first: bool) -> None:
    if first:
        c.setFont(FONT_BOLD, 16)
        c.drawString(54, _baseline(page_h, 38, 16), spec.company)
        c.setFont(FONT, 8)
        c.drawString(54, _baseline(page_h, 60, 8), spec.tagline)
    c.setFont(FONT, 7)
    c.drawString(54, _baseline(page_h, 760, 7), spec.footer)


def _zone(layout: LayoutProfile, role: str, spec: LetterSpec) -> Zone:
    z = layout.zone(role)
    if z is None:
        raise ValueError(f"layout {layout.id} has no {role} zone")
    dx, dy = spec.offsets.get(role, (0.0, 0.0))
    return Zone(role, z.x + dx, z.y + dy, z.width, z.height, z.max_lines)


def render_letter(spec: LetterSpec, layout: LayoutProfile, out: Path) -> Path:
    """Draw one synthetic letter PDF according to ``layout``'s zones."""
    _, page_h = LETTER
    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=LETTER, invariant=1)
    c.setTitle(f"Synthetic letter {spec.id}")
    c.setAuthor("icsel synthetic data (fictional)")
    _chrome(c, page_h, spec, first=True)

    if spec.date:
        z = _zone(layout, "date", spec)
        _draw_lines(c, page_h, z.x, z.y, [spec.date])

    z = _zone(layout, "address", spec)
    _draw_lines(c, page_h, z.x, z.y, spec.address)

    z = _zone(layout, "subject", spec)
    subj = simpleSplit(f"{spec.subject_prefix}{spec.subject}", FONT_BOLD, SIZE, z.width)
    _draw_lines(c, page_h, z.x, z.y, subj, font=FONT_BOLD)

    body = _zone(layout, "body", spec)
    top = _draw_lines(c, page_h, body.x, body.y, [spec.salutation]) + PARA_GAP
    paragraphs = spec.body if spec.body is not None else _paragraphs(spec.paragraphs, spec.seed)
    blocks = [simpleSplit(p, FONT, SIZE, body.width) for p in paragraphs]
    blocks.append([spec.closing])
    blocks.append(list(spec.signer))
    bottom = body.bottom
    for lines in blocks:
        for line in lines:
            if top + LEADING > bottom:
                c.showPage()
                _chrome(c, page_h, spec, first=False)
                top, bottom = 72.0, page_h - 72.0
            _draw_lines(c, page_h, body.x, top, [line])
            top += LEADING
        top += PARA_GAP
    c.showPage()
    c.save()
    return out


def generate(
    out_dir: Path,
    layouts: Path | None = None,
    letters: list[LetterSpec] | None = None,
) -> Path:
    """Render the synthetic letters into ``out_dir`` and write ``manifest.json``.

    Args:
        out_dir: Folder for the letter PDFs and the manifest (created if needed).
        layouts: Folder of layout profiles; defaults to the bundled examples.
        letters: Letter recipes; defaults to :data:`LETTERS`.

    Returns:
        Path of ``manifest.json``, which maps each letter file to its planted
        expected layout.
    """
    by_id = {p.id: p for p in load_profiles(layouts)}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for spec in letters or LETTERS:
        pdf = render_letter(spec, by_id[spec.layout], out_dir / f"{spec.id}.pdf")
        manifest.append({"file": pdf.name, "expected_layout": spec.layout, "note": spec.note})
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path
