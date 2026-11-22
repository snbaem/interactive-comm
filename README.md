# icsel: pick a layout for a letter PDF

[![CI](https://github.com/snbaem/interactive-comm/actions/workflows/ci.yml/badge.svg)](https://github.com/snbaem/interactive-comm/actions/workflows/ci.yml)

`icsel` is a local prototype for AEM Forms Interactive Communications (IC) users.
Given a text-based letter PDF, it picks the best-fitting layout from a small set
of layout profiles and renders a sample PDF of the letter in that layout.
Layouts differ in where boxes sit and how big they are, for example a subject
box on the right or a taller address box.

> **Not an official Adobe project.** It is not affiliated with, endorsed by or
> supported by Adobe. Product names are used only to describe compatibility.
>
> **Fidelity:** the sample PDF is a local approximation for checking fit. It is
> not AEM Forms Interactive Communications output.

All data in this repository is synthetic. The company, *Brightwater Utilities*,
is fictional, and so is every name, address and account number.

## Quick start

Requires Python 3.10 or newer. It runs entirely offline.

**Windows (PowerShell)**

```powershell
git clone https://github.com/snbaem/interactive-comm.git
cd interactive-comm
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
icsel samples
icsel match samples\letters\L04-subject-right-3line.pdf
icsel match samples\letters
icsel profile samples\letters\L01-classic.pdf
```

**macOS / Linux (bash)**

```bash
git clone https://github.com/snbaem/interactive-comm.git
cd interactive-comm
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
icsel samples
icsel match samples/letters/L04-subject-right-3line.pdf
icsel match samples/letters
icsel profile samples/letters/L01-classic.pdf
```

`icsel match` prints a ranking per letter and writes two files per letter to
`out/`: `<letter>.result.json` (detected blocks and per-layout scores) and
`<letter>.sample.pdf`.

## Browser UI

```bash
icsel serve --open
```

This starts a small local web app at <http://127.0.0.1:8765/>. If that port is
busy, it uses the next free one and prints the address. You can:

- click a synthetic sample letter, or drop your own text-based PDF;
- see the ranking with each layout's hard-check failures and per-block offsets;
- view the source letter with its detected blocks and the layout zones drawn on top,
  beside the sample rendered in that layout;
- click any layout in the ranking to render the letter in it instead.

The server uses only the Python standard library. It listens on `127.0.0.1` and
makes no outbound calls. Uploads and results go to `out/web/`. Stop it with
Ctrl+C. Do not expose it with `--host 0.0.0.0` on a shared network: it has no
authentication.

## Commands

| Command | What it does |
| --- | --- |
| `icsel match PATH...` | Rank layouts for each letter, then write a result JSON and a sample PDF. Options: `--layouts PATH` (repeatable; default is the bundled examples), `--out DIR`, `-r/--recursive`, `--no-zones`. |
| `icsel profile PATH...` | Derive a draft layout profile from a PDF. With one PDF it prints to stdout or writes to `-o FILE`. With several, it writes to the `-o DIR` folder (default `profiles/`). |
| `icsel serve` | Start the local browser UI (`--port`, `--host`, `--layouts`, `--workdir`, `--open`). |
| `icsel samples` | Generate the synthetic Brightwater Utilities letters (`--out`, default `samples/letters`). |

`PATH` can be a file, a folder or a glob pattern such as `letters/*.pdf`. icsel
expands patterns itself, so they work the same in PowerShell, cmd.exe and bash.
File extensions are matched case-insensitively. If a PDF cannot be read, icsel
reports it and moves on to the next file.

## How it works

1. `extract.py` reads words and their positions with pdfplumber, groups them
   into blocks, and labels each block with simple rules: letterhead, date,
   address, subject, salutation, body, closing, footer or unknown.
2. `match.py` scores each layout profile by how close each block sits to its
   zone and how much of it falls inside. Hard checks run first: line capacity,
   width, and required zones.
3. `render.py` draws the letter's text into the winning layout's zones with
   reportlab.

Layout profiles are JSON files. Measurements are in points, from the top-left
corner of the page. The bundled examples are in
[`src/icsel/data/layouts/`](src/icsel/data/layouts/classic-left.json).

## Development

```bash
ruff check .
ruff format --check .
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). The tests block network access.

## Limitations

- Only text-based PDFs work. Scanned or image-only PDFs are reported, not read
  with OCR.
- Block detection is rule-based and tuned for typical business letters.
- The sample PDF approximates placement only. It does not reproduce IC fonts,
  fragments, data binding or pagination.

## License

[Apache License 2.0](LICENSE).
