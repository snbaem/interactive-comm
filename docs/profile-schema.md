# Layout profiles and `scoring.json`

## Coordinates and units

All lengths are PDF points (72 pt = 1 inch). The origin is the **top-left** corner of the page
and `y` grows downwards. Many PDF tools count from the bottom-left, so convert:
`y = page height - (bottom-left y + height)`. US Letter is 612 x 792 pt, A4 is 595 x 842 pt
(rounded).

## Layout profile

One JSON file per layout. The JSON Schema is
[`layout-profile.schema.json`](../src/icsel/data/layout-profile.schema.json); the loader applies
the same rules, plus a check that zones stay on the page.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `profile_version` | integer | no | Must be `1` |
| `id` | string | yes | Unique, not empty; used on the command line |
| `name` | string | no | Display name (default: the id) |
| `description` | string | no | Free text |
| `kind` | `letter` or `statement` | no | Default `letter`. A statement only fits a `statement` layout, and the other way round |
| `page.width`, `page.height` | number | yes | Page size in points, greater than 0 |
| `page.units` | string | no | Only `pt` |
| `zones` | list | yes | At least one zone |

Extra top-level fields are kept but ignored. Each zone has:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `role` | string | yes | `letterhead`, `address`, `date`, `subject`, `salutation`, `body`, `closing`, `footer`, `unknown`, `reference` or `table` |
| `x`, `y` | number | yes | Top-left corner, 0 or more |
| `width`, `height` | number | yes | Greater than 0 |
| `max_lines` | integer | no | Line capacity, at least 1. More lines fail a hard check (except in `body`) |
| `required` | boolean | no | The letter must have this block (default false) |

Unknown zone fields are errors, so a typo does not go unnoticed. Scored roles are `address`,
`subject`, `date`, `body` and `reference`; only the first zone of a role is used. The other
roles matter for drawing the sample.

```json
{
  "profile_version": 1,
  "id": "my-layout",
  "name": "My layout",
  "page": {"width": 612, "height": 792, "units": "pt"},
  "zones": [
    {"role": "date", "x": 396, "y": 108, "width": 162, "height": 16, "max_lines": 1},
    {"role": "address", "x": 72, "y": 144, "width": 252, "height": 86, "max_lines": 6, "required": true},
    {"role": "subject", "x": 72, "y": 252, "width": 468, "height": 30, "max_lines": 2},
    {"role": "body", "x": 72, "y": 300, "width": 468, "height": 420}
  ]
}
```

### Validation errors

All problems of a file are reported together, and the command exits with code 2:

```text
error: bad.json: invalid layout profile
  - zones[0].role: 'adress' is not one of letterhead, address, date, subject, salutation, body, closing, footer, unknown, reference, table
  - zones[0].width: must be greater than 0, got 0
  - zones[1].maxlines: unknown field (allowed: height, max_lines, required, role, width, x, y)
  - zones[1]: right edge 672 is beyond the page width 612
```

Two profiles with the same `id` are an error too.

### Write a profile by hand

1. Measure each box in points from the top-left corner.
2. Set `max_lines` for the address, subject and date. Take the box height divided by the line
   height and round down; the bundled profiles use 12 to 14 pt per line.
3. Mark the address `required` if every letter in this layout has one.
4. Check it as below.

### Derive a draft from a PDF

```bash
icsel profile samples/letters/L01-classic.pdf --id my-layout -o my-layout.json
```

The draft has one zone per detected role, sized to the text that was found, with `max_lines`
set to the lines that were found. Widen the zones and raise `max_lines` by hand: a three-line
address in the source PDF does not mean the box holds three lines. Add zones the PDF left empty.

### Check it

```bash
icsel show-layout my-layout.json
icsel show-layout my-layout.json --compare classic-left
icsel show-layout my-layout.json --letter samples/letters/L01-classic.pdf --diff
icsel match samples/letters/L01-classic.pdf --layouts my-layout.json
```

The view lists every distance and size, in inches by default (`--units in`, `cm` or `pt`).

## `scoring.json`

The defaults are in [`scoring.json`](../src/icsel/data/scoring.json). Pass a file with any subset
of the keys to `match`, `portfolio` or `eval` with `--config FILE`. Keys that start with `_` are
comments.

| Key | Default | Allowed | Meaning |
| --- | ---: | --- | --- |
| `role_weights` | address 3, subject 3, date 1, body 2, reference 1 | 0 or more, not all 0 | Weight of each scored role |
| `position_scale` | 72 pt | 1 to 2000 | Distance at which closeness reaches 0 |
| `closeness_share` | 0.6 | 0 to 1 | Share of closeness in a role's score; the rest is overlap |
| `width_tolerance` | 18 pt | 0 to 500 | How far a block may exceed its zone's width |
| `low_fit_threshold` | 0.8 | 0 to 1 | A best score below this is a weak fit |
| `tie_margin` | 0.05 | 0 to 1 | Fitting layouts this close to the best are a near-tie |
| `cluster_distance` | 36 pt | 0 to 2000 | Proposals: largest box distance inside a cluster |
| `cluster_min_letters` | 2 | 1 or more | Proposals: smallest cluster that becomes a draft |
| `shared_text_threshold` | 0.8 | 0.1 to 1 | Shared text: similarity that counts as the same text |
| `shared_text_min_letters` | 2 | 2 or more | Shared text: letters a text must appear in |
| `shared_text_min_words` | 8 | 1 or more | Shared text: shorter body paragraphs are ignored |

Errors name the file and the key:

```text
error: c.json: 'tie_margin' must be a score difference between 0 and 1, got 3
```

The duplicate threshold is an option of `icsel portfolio`, `--duplicate-threshold`.
