# icsel — layout selector for letter PDFs (v1 prototype)

Local prototype for AEM Forms Interactive Communications (IC) users. Given a
text-based letter PDF, `icsel` picks the best-fitting layout from a small set of
layout profiles and renders a sample PDF of the letter in that layout.

> **Not an official Adobe project.** Product names are used only to describe
> compatibility.
>
> **Fidelity:** the sample PDF is a local approximation for checking fit. It is
> not AEM Forms Interactive Communications output.

All data in this repository is synthetic. The company, Brightwater Utilities,
is fictional, as are every name, address and account number.

## Try it (v1, no packaging yet)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows; use .venv/bin/python on macOS/Linux
python scripts/make_synthetic.py                           # writes samples/letters/*.pdf
PYTHONPATH=src python -m icsel match samples/letters/L04-subject-right-3line.pdf
PYTHONPATH=src python -m icsel profile samples/letters/L01-classic.pdf
python -m pytest
```

## How it works

1. `extract.py` reads words and positions with pdfplumber, groups them into
   blocks and classifies each block (letterhead, date, address, subject,
   salutation, body, closing, footer, unknown) with simple rules.
2. `match.py` scores each layout profile in `layouts/` by how close each
   letter block sits to its zone and how much of it falls inside, after hard
   checks (line capacity, width, required zones).
3. `render.py` draws the letter's text into the winning layout's zones with
   reportlab.

Layout profiles are JSON files in points with a top-left origin; see
`layouts/classic-left.json`.
