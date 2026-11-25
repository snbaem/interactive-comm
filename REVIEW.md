# Reviewer checklist

## Install

Python 3.10 or newer, from a clean clone:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Run three commands

```bash
icsel samples
icsel match samples/letters
icsel eval samples/letters
```

| Command | Expect |
| --- | --- |
| `icsel samples` | `Wrote 17 synthetic letters and ...manifest.json` |
| `icsel match samples/letters` | Exit code 0, 17 rankings. The best layout is the one in `manifest.json`; `L14-no-layout-fits.pdf` fits none. One `.result.json` and one `.sample.pdf` per letter in `out/` |
| `icsel eval samples/letters` | Layout agreement 1.000, boilerplate recall 1.000, "No AI task was run." |

Then the checks: `ruff check .`, `ruff format --check .` and `python -m pytest` (no network).

## Look at the PDFs

- `out/L04-subject-right-3line.sample.pdf`: the three-line subject sits in the right-hand box,
  and the page says it is a local approximation.
- `out/L12-table-block.sample.pdf`: the table the rules could not label flows into the body with
  an amber background, columns side by side.
- `out/L11-three-page-body.sample.pdf`: the body continues on later pages.
- `out/S01-statement.sample.pdf`: title, date and account details in the statement layout.
- `out/L14-no-layout-fits.sample.pdf`: drawn in the closest layout although none fits. Its source
  has overprinted text on purpose.

## Check the claims

- [ ] No document says the sample is Interactive Communications output.
- [ ] No document says icsel reads templates or is ready for production.
- [ ] AI is off by default, and the README table matches `src/icsel/prompts/README.md`.
- [ ] Everything in letters, images and docs is synthetic.

## Known rough edges

- No real AI model has been run. The benchmark says nothing about a model until it has been.
- The benchmark letters and layouts were designed together, so rule scores are close to perfect.
- `classify` has little to improve on the sample letters.
- Boilerplate precision on the sample letters is 0.93: random paragraphs that share two of three
  sentences are grouped.
- Shared text does not join a paragraph that a page break splits. Two letters to the same person
  can share text that names them; only address lines and digits are masked before `fragments`
  sends it.
- Statement detection was tested on one synthetic design.
- In dimensioned views, labels can touch zone edges when zones are close together.
- `docs/images/browser-ui.png` and `portfolio-report.png` need an installed Chromium-based
  browser to regenerate.
- `icsel portfolio samples/portfolio` exits with 1 by design: one scan and one corrupt file.

## Every `UNVERIFIED` item

A test checks that each file with the label is listed here.

| File | Item |
| --- | --- |
| `README.md` | Mapping of detected fields to data binding, form data model and fragments; mapping of shared text to fragments; the platform options under next steps |
| `docs/getting-real-templates-in.md` | The whole automatic route: template export, `.xdp` geometry, roles of subforms, boxes that grow |
| `docs/production-path.md` | Where it would run, template and Interactive Communications APIs, authentication |
| `docs/recipe-draft.md` | Library category names; mapping to Master Pages, Subforms, Document Fragments and Form Data Models; APIs |
| `docs/PLAN.md` | Ground rule and the phase 5 note, no claim |
| `CONTRIBUTING.md` | The ground rule itself, no claim |
| `src/icsel/sharedtext.py` | Mapping of a shared-text candidate to a document fragment (also printed in `portfolio.md`) |
