# Contributing to icsel

Thanks for your interest. icsel is a small, local prototype. Contributions that
keep it simple, honest and easy to run are very welcome.

> icsel is a community project. It is **not** an official Adobe project and is
> not supported by Adobe.

## Ground rules

These rules apply to every change, including test data and docs:

1. **Synthetic data only.** Never commit real letters, real templates, real
   company names, or anything that looks like customer data. The fictional
   company is *Brightwater Utilities*. Use placeholder names (`Alex Sample`),
   the `ZZ` region and `0000-xxxx` account numbers, and `*.example` domains.
2. **Do not invent platform behavior.** Any statement about AEM as a Cloud
   Service internals, Interactive Communications APIs, template storage, or
   programmatic IC creation that you cannot back with public documentation
   must be marked `UNVERIFIED`, with a short "how to verify" note.
3. **Be honest about fidelity.** The sample PDF is a local approximation, never
   real IC output. Keep that wording in the README, the CLI output and the PDF
   footer.
4. **No secrets, no network calls by default.** Optional features that call a
   service must be off by default and read configuration from environment
   variables. Tests must pass without network access.
5. **Dependencies.** The core install depends only on `pdfplumber` and
   `reportlab`. Open an issue before proposing another core dependency.

## Development setup

Python 3.10 or newer.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

**macOS / Linux (bash or zsh)**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Checks

Run these before opening a pull request. CI runs the same commands on Windows
and Linux.

```bash
ruff check .
ruff format --check .   # or `ruff format .` to fix
pytest
```

## Code style

- Use `pathlib.Path` for paths; never assume a shell has expanded `*.pdf`
  (accept folders and patterns through `icsel.inputs`).
- Public functions get type hints and a docstring (Google style).
- Keep units explicit. Internal geometry is PDF points (1/72 inch) with a
  top-left origin.

## Pull requests

- Keep each PR focused, and describe what changed and how you tested it.
- Add or update tests for behavior changes. If you add a synthetic letter,
  plant its expected layout in `icsel.synthetic.LETTERS`.
- Update the docs when you change a command, the profile format or the scoring.
- By contributing you agree that your contribution is licensed under the
  Apache License 2.0 (see [LICENSE](LICENSE)).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
