# Plan and status

This is the working plan for icsel, in phases. Each phase ends with something you can run
and review. The ground rules in [CONTRIBUTING.md](../CONTRIBUTING.md) apply to every phase:
synthetic data only, no invented platform behavior (mark it `UNVERIFIED`), honest fidelity,
no network calls by default.

## Goal

A clean, publishable open-source repo for AEM Forms Interactive Communications users. Given
a text-based letter PDF it picks the best-fitting layout out of a small set and renders a
sample of the letter in that layout. It doubles as the reference implementation for an
Adobe Experience League AI Recipe. The sample PDF is always a local approximation, never
real Interactive Communications output.

## Status

| Phase | What | Status |
| --- | --- | --- |
| 1 | Packaging and quality | **Done** |
| 2 | Better model and scoring | **Partly done** |
| 3 | Optional AI step | **Done** |
| 4 | Portfolio mode | **Done** (this change) |
| 5 | Docs and recipe draft | Not started |

The phase order changed once: the AI step (3) now comes before portfolio mode (4), so
portfolio mode could be designed around it. It calls the AI only where it helps and
never for every letter.

## Phase 1: Packaging and quality (done)

- `pyproject.toml` with the `icsel` console script, Python 3.10+, `dev` and `llm` extras.
- Type hints and docstrings enforced by `ruff`; `pytest` configuration; tests block the network.
- Windows, macOS and Linux: `pathlib` throughout, folders and glob patterns accepted
  (`icsel/inputs.py`).
- GitHub Actions workflow on Windows and Linux (lint, tests, smoke test of the console script).
- Apache-2.0 `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.gitignore`.

## Phase 2: Better model and scoring (partly done)

Done:

- Overlay preview: the browser UI (`icsel serve`) draws the detected blocks and the layout
  zones over the source letter, beside the sample.
- Tests that every synthetic letter's best layout equals its planted layout, plus hard-check
  failures.

Still to do:

- Move the scoring weights, the position scale and the hard-check rules (currently constants
  in `match.py`) into a documented config file with defaults.
- A JSON Schema for the layout profile; validate profiles on load with clear errors.
- A deterministic, plain-language **explanation** per result (no model), built from the
  structured score. Phase 3's AI explanation then becomes an optional upgrade.
- A **dimensioned layout view** (`icsel show-layout <profile>`, and an option on `match`).
  It draws the page with every zone and annotates measurements in inches (`--units in|cm|pt`,
  default inches): distance of each zone from the top and left edges (for example
  `2.0" from top`), zone width and height, page margins, vertical and horizontal gaps between
  neighboring zones, and line capacity beside the address and subject boxes. Dimension lines
  with arrowheads and non-overlapping labels; PDF and PNG output.
  Also a side-by-side comparison of two layouts and a `--diff` mode that says where the
  letter's blocks sit relative to the chosen layout (for example `subject 0.3" lower than
  layout`). Expose the measurements as plain data so the arithmetic is unit tested.
- Expand the synthetic data to five layouts and about fifteen letters with more edge cases:
  no date, two-line vs four-line subject, a very long address, a three-page body, a letter
  with a table-like block, and a subject on the right but lower than the template expects.

The ambiguity flag and the tie-break that were planned here were built in Phase 4.

## Phase 3: Optional AI step (done)

Off by default, provider-agnostic, no network in tests.

- A one-method interface (`CompletionBackend`) and an assistant with two jobs: label blocks
  the rules left `unknown`, and explain the fit from scores alone.
- Guard rails: only unknown blocks change, only above a confidence threshold, never a second
  date, address or subject; everything the AI changed is marked `role_source: "llm"`.
- Prompts in `src/icsel/prompts/`, an example Anthropic backend behind the `llm` extra,
  configuration through environment variables (`.env.example`).
- A note on personal data: send only text you are allowed to send to your model endpoint.

## Phase 4: Portfolio mode (done)

`icsel portfolio PATH...` scans folders of letters and writes `portfolio.csv` and
`portfolio.md`. Details are in the README. Highlights:

- Best layout per letter, score, runner-up and the score gap.
- **Ambiguity flag** where fitting layouts score within a margin, with a documented
  **tie-break** by tightest fit (`icsel.match.resolve_ties`). `icsel match` is unchanged.
- Distribution of letters per layout, letters that fit **no** layout and weak fits.
- **Near-duplicate** groups by Jaccard similarity of 5-word body shingles, digits normalized.
- Streams the folder: one small record per PDF, so a folder of hundreds of letters does not
  need to fit in memory. Corrupt files and scans are listed, not fatal.
- **Economical AI use** (`--llm`): only letters with unknown blocks, or that are ambiguous,
  weak or fit no layout. An estimate is shown first and the user must confirm (`--yes` to
  skip, required when not interactive). Stops after three failures in a row.
- `icsel samples --portfolio` builds a 40-file synthetic demo folder with varied bodies and
  planted cases: four near-ties, a weak fit, a no-fit letter, one duplicate pair, two notices
  with a table, an image-only PDF and a corrupt file. `tests/test_portfolio.py` checks each.

## Phase 5: Docs and recipe draft (not started)

- `README.md`: screenshots (source letter beside sample output), quick starts, limitations.
- `docs/architecture.md`: pipeline diagram (Mermaid), modules, scoring formula.
- `docs/profile-schema.md`: the layout profile format, and how to produce one from a
  reference PDF or by hand.
- `docs/getting-real-templates-in.md`: three options (hand-written profile; one reference PDF
  per template generated from the platform with placeholder text; parsing an exported
  template), with pros and cons. Anything platform-specific is marked `UNVERIFIED`.
- `docs/production-path.md`: how this could become an in-platform component (a thin component
  or asset action, an external service for PDF parsing and scoring, results stored as asset
  metadata). A design sketch with open questions, all `UNVERIFIED`: outbound calls from the
  authoring tier, approved model endpoints, whether draft ICs can be created programmatically.
- `docs/recipe-draft.md`: an Experience League AI Recipe draft (title and category, problem,
  architecture, step-by-step, testing with synthetic data, community value).
- `docs/REVIEW.md`: what is verified, what is `UNVERIFIED`, known limitations.

## Definition of done for the repo

- `pip install -e ".[dev]"`, then `icsel match`, `icsel profile` and `icsel portfolio` work
  from a clean clone on Windows and Linux.
- `pytest` passes with no network; CI is green.
- The README quick start works when copied and pasted.

## Ideas after the recipe

Reuse the same stack for QA of generated output, as a sibling tool or extra commands:

- A **letter PDF preflight linter**: page size, minimum margins, fonts embedded, address in the
  window zone, unresolved placeholders, page limits, rules in a config file.
- A **payload and scenario generator** for edge-case test data, feeding the linter.
- A **change summary for approvers** that compares two versions of an export in plain language.
- An **export checker** for missing fragments and broken data-model references.
