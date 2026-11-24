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
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

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
    "long9": [
        "Taylor Placeholder",
        "c/o Sample Estates Management Ltd",
        "Attn: Accounts Payable Department",
        "Building D, Floor 3, Suite 310",
        "2400 Imaginary Boulevard East",
        "Northside Industrial District",
        "Riverton ZZ 00004",
        "Account 0000-0009",
        "Reference 0000-0009-A",
    ],
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
    subject_bold: bool = True
    salutation: str = "Dear Customer,"
    body: list[str] | None = None  # explicit paragraphs; None = generated from a seed
    #: Table-like block (header row first) drawn under the salutation; the rules cannot label it.
    table: list[list[str]] | None = None
    #: True for a letter that is meant to fit no bundled layout (manifest has no expected layout).
    fits_none: bool = False
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
    LetterSpec(
        "L07-no-date",
        "classic-left",
        ADDRESSES["medium"],
        "Confirmation of your direct debit",
        date=None,
        seed=7,
        note="Edge case: no date block at all.",
    ),
    LetterSpec(
        "L08-two-line-subject",
        "classic-left",
        ADDRESSES["short"],
        "Notice of a change to the billing period and payment dates for account 0000-0008",
        seed=8,
        note="Edge case: the subject wraps to two lines.",
    ),
    LetterSpec(
        "L09-four-line-subject",
        "subject-right",
        ADDRESSES["medium"],
        "Important information about the scheduled replacement of the shared service "
        "pipe serving the Fictional Road and Imaginary Boulevard area",
        seed=9,
        note="Edge case: a four-line subject in the right-hand box.",
    ),
    LetterSpec(
        "L10-very-long-address",
        "tall-address",
        ADDRESSES["long9"],
        "Statement for your business account",
        seed=10,
        note="Edge case: nine address lines, the most any bundled layout holds.",
    ),
    LetterSpec(
        "L11-three-page-body",
        "classic-left",
        ADDRESSES["short"],
        "Your complete service terms",
        paragraphs=34,
        seed=11,
        note="Edge case: the body runs onto a third page.",
    ),
    LetterSpec(
        "L12-table-block",
        "classic-left",
        ADDRESSES["short"],
        "Summary of charges this quarter",
        table=[
            ["Service", "Period", "Amount"],
            ["Water", "Jan - Mar", "$41.20"],
            ["Power", "Jan - Mar", "$88.75"],
            ["Standing charge", "Jan - Mar", "$12.00"],
        ],
        seed=12,
        note="Edge case: a table-like block in the body that the rules cannot label.",
    ),
    LetterSpec(
        "L13-subject-right-lower",
        "subject-right",
        ADDRESSES["short"],
        "Planned maintenance in your area next month",
        offsets={"subject": (0.0, 40.0)},
        seed=13,
        note="Edge case: subject on the right like subject-right, but 40 pt lower than its box.",
    ),
    LetterSpec(
        "L14-no-layout-fits",
        "classic-left",
        ADDRESSES["long9"],
        "Statement for your business account (corrected)",
        offsets={"address": (140.0, 90.0), "subject": (0.0, 70.0), "date": (-300.0, 120.0)},
        seed=14,
        note="Edge case: address, subject and date are placed where no bundled layout has boxes.",
        fits_none=True,
    ),
    LetterSpec(
        "L15-address-right",
        "address-right",
        ADDRESSES["medium"],
        "Your water meter has been replaced",
        seed=15,
        note="Baseline letter for address-right.",
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
    subj_font = FONT_BOLD if spec.subject_bold else FONT
    subj = simpleSplit(f"{spec.subject_prefix}{spec.subject}", subj_font, SIZE, z.width)
    _draw_lines(c, page_h, z.x, z.y, subj, font=subj_font)

    body = _zone(layout, "body", spec)
    top = _draw_lines(c, page_h, body.x, body.y, [spec.salutation]) + PARA_GAP
    if spec.table:
        c.setFont(FONT_BOLD, 9)
        for i, text in enumerate(spec.table[0]):
            c.drawString(body.x + i * 130, _baseline(page_h, top, 9), text)
        c.setFont(FONT, 9)
        for r, row in enumerate(spec.table[1:], start=1):
            for i, text in enumerate(row):
                c.drawString(body.x + i * 130, _baseline(page_h, top + r * 13, 9), text)
        top += len(spec.table) * 13 + PARA_GAP
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


@dataclass
class StatementSpec:
    """Recipe for one synthetic account statement."""

    id: str
    rows: int
    row_step: float
    note: str


STATEMENTS = [
    StatementSpec("S01-statement", 22, 18.0, "Account statement with a one-page table."),
    StatementSpec("S02-statement-2page", 60, 12.0, "Statement with tight rows over two pages."),
]
_STATEMENT_COLUMNS = (("Date", 54), ("Description", 130), ("Units", 380), ("Amount", 450),
                      ("Balance", 520))  # fmt: skip


_STATEMENT_DETAILS = ["Statement date: March 3, 2026", "Account: 0000-0001",
                      "Period: Feb 1 - Feb 28, 2026"]  # fmt: skip
_STATEMENT_SUMMARY = [("Opening balance", "120.00"), ("Payments", "-120.00"),
                      ("Charges", "141.95"), ("Amount due", "141.95")]  # fmt: skip
_STATEMENT_NOTE = "Payment is due by March 25, 2026. Pay online or at any service counter."


def _statement_row(r: int) -> list[str]:
    return [f"Feb {r % 28 + 1:02d}", f"Water usage reading {r + 1}", str(10 + r),
            f"{3 + r * 0.4:.2f}", f"{30 + r * 5:.2f}"]  # fmt: skip


def render_statement(spec: StatementSpec, out: Path) -> Path:
    """Draw one synthetic account statement: title, details, summary boxes and a table."""
    _, page_h = LETTER
    c = canvas.Canvas(str(out), pagesize=LETTER, invariant=1)
    c.setTitle(f"Synthetic statement {spec.id}")
    c.setAuthor("icsel synthetic data (fictional)")

    def text(x: float, top: float, value: str, font: str = FONT, size: float = 9) -> None:
        c.setFont(font, size)
        c.drawString(x, _baseline(page_h, top, size), value)

    def footer() -> None:
        text(54, 760, COMPANY_FOOTER, size=7)

    def header(top: float) -> None:
        for name, x in _STATEMENT_COLUMNS:
            text(x, top, name, FONT_BOLD)

    text(54, 38, COMPANY, FONT_BOLD, 16)
    text(400, 40, "ACCOUNT STATEMENT", FONT_BOLD, 14)
    for i, line in enumerate(_STATEMENT_DETAILS):
        text(400, 110 + i * 13, line)
    for i, line in enumerate(ADDRESSES["short"]):
        text(54, 110 + i * 12, line)
    for i, (label, value) in enumerate(_STATEMENT_SUMMARY):
        text(54 + i * 130, 194, label, FONT_BOLD)
        text(54 + i * 130, 208, value)
    top = 244.0
    header(top)
    for r in range(spec.rows):
        top += spec.row_step
        if top > 700:
            footer()
            c.showPage()
            top = 72.0
            header(top)
            top += spec.row_step
        for (_, x), value in zip(_STATEMENT_COLUMNS, _statement_row(r), strict=True):
            text(x, top, value)
    text(54, top + 30, _STATEMENT_NOTE)
    footer()
    c.showPage()
    c.save()
    return out


# ---------------------------------------------------------------- ground truth
#: True purpose of every synthetic subject line (see ``data/purposes.json`` for the list).
_PURPOSES = {
    "Your updated tariff from April 2026": "policy-update",
    "Meter reading review for account 0000-0002": "billing-notice",
    "Meter review": "billing-notice",
    "Change to your payment plan and the schedule for the remaining instalments this year": (
        "billing-notice"
    ),
    "Change to your payment plan": "billing-notice",
    "Notice of a change to the billing period and payment dates for account 0000-0008": (
        "billing-notice"
    ),
    "Budget plan review": "billing-notice",
    "Statement of account": "statement",
    "Statement for your business account": "statement",
    "Statement for your business account (corrected)": "statement",
    "Annual summary of your service": "statement",
    "Summary of charges this quarter": "statement",
    "Your usage summary for the last quarter": "statement",
    "Confirmation of your direct debit": "confirmation",
    "Confirmation of your new billing date": "confirmation",
    "Confirmation of your new service address": "confirmation",
    "Confirmation of your service address": "confirmation",
    "Direct debit change confirmed": "confirmation",
    "Your water meter has been replaced": "confirmation",
    "Your complete service terms": "policy-update",
}

_ACCOUNT_RE = re.compile(r"\b0000-\d{4}(?:-[A-Z])?\b")
_MONTH_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November"
    r"|December) \d{4}\b"
)


def _true_fields(
    date: str | None, address: list[str], subject: str, table: list[list[str]] | None
) -> list[dict[str, str]]:
    """The variable text of a synthetic letter, each text once, with unique names."""
    pairs: list[tuple[str, str]] = []
    if date:
        pairs.append(("letterDate", date))
    for i, line in enumerate(address):
        pairs.append(("recipientName" if i == 0 else f"addressLine{i}", line))
    for text in (*address, subject):
        for number in _ACCOUNT_RE.findall(text):
            pairs.append(("accountNumber", number))
    for month in _MONTH_RE.findall(subject):
        pairs.append(("effectiveMonth", month))
    if table:
        # The first column holds fixed row labels; the rest is data.
        for row in table[1:]:
            pairs.extend((h.lower(), cell) for h, cell in list(zip(table[0], row, strict=True))[1:])
    out: list[dict[str, str]] = []
    used: set[str] = set()
    texts: set[str] = set()
    for name, text in pairs:
        if text in texts:
            continue
        texts.add(text)
        unique, n = name, 2
        while unique in used:
            unique, n = f"{name}{n}", n + 1
        used.add(unique)
        out.append({"name": unique, "text": text})
    return out


def letter_truth(spec: LetterSpec, layout: LayoutProfile) -> dict[str, Any]:
    """Ground truth of one synthetic letter: block roles, fields, purpose and layout.

    The recorded layout is ``None`` for a letter that is meant to fit no layout.
    """
    subject_zone, body = _zone(layout, "subject", spec), _zone(layout, "body", spec)
    font = FONT_BOLD if spec.subject_bold else FONT
    subject = simpleSplit(f"{spec.subject_prefix}{spec.subject}", font, SIZE, subject_zone.width)
    blocks: list[dict[str, Any]] = [
        {"role": "letterhead", "lines": [spec.company, spec.tagline]},
        {"role": "address", "lines": list(spec.address)},
        {"role": "subject", "lines": subject},
        {"role": "salutation", "lines": [spec.salutation]},
    ]
    if spec.date:
        blocks.append({"role": "date", "lines": [spec.date]})
    if spec.table:
        blocks.append({"role": "body", "lines": [cell for row in spec.table for cell in row]})
    paragraphs = spec.body if spec.body is not None else _paragraphs(spec.paragraphs, spec.seed)
    for text in paragraphs:
        blocks.append({"role": "body", "lines": simpleSplit(text, FONT, SIZE, body.width)})
    blocks.append({"role": "closing", "lines": [spec.closing, *spec.signer]})
    blocks.append({"role": "footer", "lines": [spec.footer]})
    return {
        "file": f"{spec.id}.pdf",
        "layout": None if spec.fits_none else spec.layout,
        "purpose": _PURPOSES.get(spec.subject, "other"),
        "blocks": blocks,
        "fields": _true_fields(spec.date, spec.address, spec.subject, spec.table),
    }


def statement_truth(spec: StatementSpec) -> dict[str, Any]:
    """Ground truth of a synthetic statement. Fields are left out: every cell is data."""
    cells = [name for name, _ in _STATEMENT_COLUMNS]
    cells += [text for pair in _STATEMENT_SUMMARY for text in pair]
    for r in range(spec.rows):
        cells += _statement_row(r)
    return {
        "file": f"{spec.id}.pdf",
        "layout": "statement",
        "purpose": "statement",
        "blocks": [
            {"role": "letterhead", "lines": [COMPANY]},
            {"role": "subject", "lines": ["ACCOUNT STATEMENT"]},
            {"role": "date", "lines": _STATEMENT_DETAILS[:1]},
            {"role": "reference", "lines": _STATEMENT_DETAILS[1:]},
            {"role": "address", "lines": list(ADDRESSES["short"])},
            {"role": "table", "lines": cells},
            {"role": "body", "lines": [_STATEMENT_NOTE]},
            {"role": "footer", "lines": [COMPANY_FOOTER]},
        ],
    }


def _write_truth(out_dir: Path, letters: list[dict[str, Any]]) -> Path:
    path = out_dir / "truth.json"
    doc = {"version": 1, "letters": letters}
    path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return path


def generate(
    out_dir: Path,
    layouts: Path | None = None,
    letters: list[LetterSpec] | None = None,
) -> Path:
    """Render the synthetic letters into ``out_dir``; write ``manifest.json`` and ``truth.json``.

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
    truth = []
    for spec in letters or LETTERS:
        truth.append(letter_truth(spec, by_id[spec.layout]))
        pdf = render_letter(spec, by_id[spec.layout], out_dir / f"{spec.id}.pdf")
        manifest.append(
            {
                "file": pdf.name,
                "expected_layout": None if spec.fits_none else spec.layout,
                "note": spec.note,
            }
        )
    if letters is None and "statement" in by_id:
        for st in STATEMENTS:
            pdf = render_statement(st, out_dir / f"{st.id}.pdf")
            manifest.append({"file": pdf.name, "expected_layout": "statement", "note": st.note})
            truth.append(statement_truth(st))
    _write_truth(out_dir, truth)
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- portfolio demo set
_TOPICS = [
    "meter reading",
    "payment plan",
    "service address",
    "billing date",
    "tariff change",
    "online account",
    "direct debit",
    "usage history",
    "repair visit",
    "budget plan",
]
_ACTIONS = [
    "was reviewed by our team last week",
    "will be updated before the end of the quarter",
    "can be changed at any time in the customer portal",
    "appears on the statement we send you next month",
    "needs a short confirmation from you",
    "has been recorded exactly as you gave it to us",
]
_SUBJECTS = [
    "Your updated tariff from April 2026",
    "Meter reading review for account 0000-0002",
    "Planned maintenance in your area next month",
    "Change to your payment plan",
    "Confirmation of your new billing date",
    "Your usage summary for the last quarter",
    "Welcome to online account access",
    "Direct debit change confirmed",
    "Repair visit scheduled for your address",
    "Budget plan review",
    "Annual summary of your service",
    "Notice of a planned service interruption and what it means for your account this winter",
]
#: Fixed, distinct paragraphs for the planted duplicate pair.
_DUPLICATE_BODY = [
    "We are writing to confirm that your service address was updated following your request "
    "last month. Nothing further is needed from you to complete the change.",
    "From the next billing cycle your statements will show the new address. Any earlier "
    "statements stay as they were issued and are available in the customer portal.",
    "If the address shown above is wrong, please tell us within fourteen days so that we can "
    "correct it before the next statement is produced.",
]


def _unique_body(seed: int, paragraphs: int = 4) -> list[str]:
    """Paragraphs sampled from a pool of 60 distinct sentences; two letters share few."""
    rng = random.Random(seed)
    pool = [f"Your {t} {a}." for t in _TOPICS for a in _ACTIONS]
    picks = rng.sample(pool, k=paragraphs * 3)
    return [" ".join(picks[i * 3 : i * 3 + 3]) for i in range(paragraphs)]


def _jitter(rng: random.Random) -> dict[str, tuple[float, float]]:
    return {
        role: (round(rng.uniform(-5, 5), 1), round(rng.uniform(-5, 5), 1))
        for role in ("date", "address", "subject")
    }


def _portfolio_specs() -> list[tuple[LetterSpec, dict[str, str]]]:
    """Recipes for the demo letters, each with the facts the manifest records about it."""
    rng = random.Random(2026)
    out: list[tuple[LetterSpec, dict[str, str]]] = []
    n = 0
    plan = [("classic-left", 10, ("short", "medium")), ("subject-right", 10, ("short", "medium"))]
    plan.append(("tall-address", 8, ("long7", "long8")))
    for layout, count, addr_keys in plan:
        for k in range(count):
            n += 1
            spec = LetterSpec(
                f"P{n:02d}-{layout}",
                layout,
                ADDRESSES[addr_keys[k % len(addr_keys)]],
                _SUBJECTS[(n + k) % len(_SUBJECTS)],
                date=f"March {3 + (n % 20)}, 2026",
                body=_unique_body(1000 + n, paragraphs=3 + (n % 3)),
                offsets=_jitter(rng),
                note=f"Clean letter planted for {layout}.",
            )
            out.append((spec, {"kind": "letter", "expected_layout": layout}))

    # Near-ties: placed between classic-left and tall-address, so two layouts fit equally well.
    for i, (ady, sdy) in enumerate([(-12, 12), (-9, 12), (-6, 16), (-3, 16)], start=1):
        spec = LetterSpec(
            f"T{i:02d}-between-classic-and-tall",
            "classic-left",
            ADDRESSES["medium"],
            _SUBJECTS[i],
            body=_unique_body(2000 + i),
            offsets={"address": (0.0, float(ady)), "subject": (0.0, float(sdy))},
            note="Address and subject sit between classic-left and tall-address.",
        )
        out.append((spec, {"kind": "near_tie", "expected_layout": "classic-left"}))

    # Weak fit: every hard check passes but the subject is far from where the layout expects it.
    weak = LetterSpec(
        "W01-subject-too-high",
        "classic-left",
        ADDRESSES["medium"],
        "Meter review",
        body=_unique_body(3001),
        offsets={"subject": (0.0, -30.0)},
        note="Fits classic-left, but the subject sits 30 pt too high.",
    )
    out.append((weak, {"kind": "weak", "expected_layout": "classic-left"}))

    # No fit: an eleven-line address is more than any layout holds (the largest allows nine).
    nofit = LetterSpec(
        "N01-eleven-line-address",
        "tall-address",
        [f"Fictional address line {i}" for i in range(1, 12)],
        "Statement of account",
        body=_unique_body(4001),
        note="Eleven address lines; no layout has the capacity.",
    )
    out.append((nofit, {"kind": "no_fit"}))

    # The planted duplicate pair: same body, one word changed, different recipient and subject.
    first = LetterSpec(
        "D01-address-confirmation",
        "subject-right",
        ADDRESSES["short"],
        "Confirmation of your new service address",
        body=list(_DUPLICATE_BODY),
        note="Original of a planted near-duplicate pair.",
    )
    changed = [_DUPLICATE_BODY[0].replace("last month", "last week"), *_DUPLICATE_BODY[1:]]
    second = replace(
        first,
        id="D02-address-confirmation-copy",
        address=ADDRESSES["medium"],
        subject="Confirmation of your service address",
        body=changed,
        note="Near-duplicate of D01: one word changed.",
    )
    out.append((first, {"kind": "duplicate", "expected_layout": "subject-right"}))
    out.append(
        (
            second,
            {
                "kind": "duplicate",
                "expected_layout": "subject-right",
                "duplicate_of": "D01-address-confirmation.pdf",
            },
        )
    )
    return out


def _notice_truth(
    name: str, heading: str, amount: str, sentences: tuple[str, str]
) -> dict[str, Any]:
    """Ground truth of a notice. It was built from no layout, so ``layout`` is left out."""
    table = [["Account", "Due date", "Amount"], ["0000-0001", "October 15, 2026", amount]]
    return {
        "file": f"{name}.pdf",
        "purpose": "reminder",
        "blocks": [
            {"role": "subject", "lines": [heading]},
            {"role": "date", "lines": ["[Date]"]},
            {
                "role": "address",
                "lines": ["Alex Sample", "42 Placeholder Lane", "Riverton ZZ 00001"],
            },
            {"role": "body", "lines": [cell for row in table for cell in row]},
            {"role": "salutation", "lines": ["Dear Alex Sample,"]},
            {"role": "body", "lines": list(sentences)},
        ],
        "fields": [
            {"name": "letterDate", "text": "[Date]"},
            {"name": "recipientName", "text": "Alex Sample"},
            {"name": "addressLine1", "text": "42 Placeholder Lane"},
            {"name": "addressLine2", "text": "Riverton ZZ 00001"},
            {"name": "accountNumber", "text": "0000-0001"},
            {"name": "dueDate", "text": "October 15, 2026"},
            {"name": "amount", "text": amount},
        ],
    }


def _render_notice(path: Path, heading: str, amount: str, sentences: tuple[str, str]) -> None:
    """A collection-notice shaped letter: a table the rules cannot label, no layout planted."""
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    c.setTitle(f"Synthetic notice {path.stem}")
    c.setAuthor("icsel synthetic data (fictional)")
    y = 792.0 - 120.0

    def line(text: str, font: str = FONT, step: float = 14.0) -> None:
        nonlocal y
        c.setFont(font, 10)
        c.drawString(72, y, text)
        y -= step

    line(heading, FONT_BOLD, step=34)
    line("[Date]", step=24)
    line("Alex Sample", FONT_BOLD, step=12)
    line("42 Placeholder Lane", step=12)
    line("Riverton ZZ 00001", step=40)
    for x, text in [(72, "Account"), (220, "Due date"), (360, "Amount")]:
        c.setFont(FONT_BOLD, 9)
        c.drawString(x, y, text)
    y -= 14
    for x, text in [(72, "0000-0001"), (220, "October 15, 2026"), (360, amount)]:
        c.setFont(FONT, 9)
        c.drawString(x, y, text)
    y -= 36
    line("Dear Alex Sample,", step=20)
    for sentence in sentences:
        line(sentence)
    c.showPage()
    c.save()


def _render_scan(path: Path) -> None:
    """A PDF page that holds only a picture: no text layer, like a scan."""
    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader

    img = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(img)
    for row in range(8):
        draw.rectangle([60, 80 + row * 40, 540 - (row % 3) * 60, 96 + row * 40], fill="black")
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    c.drawImage(ImageReader(img), 6, 0, width=600, height=792)
    c.showPage()
    c.save()


def generate_portfolio(out_dir: Path, layouts: Path | None = None) -> Path:
    """Write the demo folder for ``icsel portfolio`` and its ``manifest.json``.

    About thirty letters with varied bodies across the three bundled layouts, plus
    deliberate cases: four near-ties between two layouts, a weak fit, a letter that fits
    no layout, a planted near-duplicate pair, two notices with a table (blocks the rules
    cannot label), an image-only PDF and a file that is not a PDF. All content is fictional.

    Returns:
        Path of ``manifest.json``: for each file, its ``kind`` and what the matcher should
        report (``expected_layout`` where one applies).
    """
    by_id = {p.id: p for p in load_profiles(layouts)}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, str]] = []
    truth: list[dict[str, Any]] = []
    for spec, facts in _portfolio_specs():
        entry = letter_truth(spec, by_id[spec.layout])
        truth.append({**entry, "layout": facts.get("expected_layout")})
        pdf = render_letter(spec, by_id[spec.layout], out_dir / f"{spec.id}.pdf")
        manifest.append({"file": pdf.name, **facts, "note": spec.note})
    notices = [
        (
            "X01-notice-with-table",
            "NOTICE OF PAST-DUE ACCOUNT",
            "12.34",
            (
                "Our records show a balance on your account that is now past due.",
                "Please contact the customer care team if you have questions about it.",
            ),
        ),
        (
            "X02-notice-with-table",
            "REMINDER OF AN UNPAID BALANCE",
            "56.78",
            (
                "A payment for the period shown below has not reached us yet.",
                "You can settle it online, by phone or at any of our service counters.",
            ),
        ),
    ]
    for name, heading, amount, sentences in notices:
        _render_notice(out_dir / f"{name}.pdf", heading, amount, sentences)
        truth.append(_notice_truth(name, heading, amount, sentences))
        manifest.append(
            {"file": f"{name}.pdf", "kind": "notice_table", "note": "Table cells stay unknown."}
        )
    _render_scan(out_dir / "Z01-scanned-image-only.pdf")
    manifest.append(
        {"file": "Z01-scanned-image-only.pdf", "kind": "scanned", "note": "No text layer."}
    )
    (out_dir / "Z02-corrupt.pdf").write_bytes(b"%PDF-1.4\nthis is not a real pdf\n")
    manifest.append({"file": "Z02-corrupt.pdf", "kind": "corrupt", "note": "Not a readable PDF."})
    _write_truth(out_dir, truth)
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path
