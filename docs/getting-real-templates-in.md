# Bringing your own layouts in

**This repo does not read Interactive Communications templates.** It reads layout profiles, small
JSON files with boxes and measurements ([format](profile-schema.md)). Getting from a real
template to a profile is manual work today.

Keep your profiles and letters outside this repository. It accepts synthetic data only (see
[CONTRIBUTING.md](../CONTRIBUTING.md)). Profiles can reveal how your letters look, so handle
them like the letters.

## The manual route

1. **Pick the layouts.** Start with the handful of templates most letters use.
2. **Get a reference PDF for each.** Use placeholder text, and fill every box as far as it goes:
   the longest address, the longest subject. Use only text you may process on your machine.
3. **Measure the zones.** Either measure in a PDF viewer or an image editor, or let icsel draft
   the profile and correct it:

   ```bash
   icsel profile reference.pdf --id plain-letter -o layouts/plain-letter.json
   ```

   The draft is sized to the text it found. Set the true box sizes, `max_lines`, and
   `required` by hand.
4. **Validate and view it.**

   ```bash
   icsel show-layout layouts/plain-letter.json
   icsel show-layout layouts/plain-letter.json --letter reference.pdf --diff
   ```

   An invalid file is rejected with every problem listed. In the view, compare the numbers with
   the template's own measurements.
5. **Test with letters.** Take letters whose template you already know.

   ```bash
   icsel match letters --layouts layouts
   icsel portfolio letters --layouts layouts --out out/check
   ```

   A wrong best layout usually means a zone in the wrong place, a `max_lines` that is too low,
   or a block with the wrong label. The browser UI shows the labels on the page:

   ```bash
   icsel serve --layouts layouts
   ```
6. **Tune only if needed.** Change weights or thresholds with `--config FILE`, and keep the file
   with the profiles.

### Checklist

- [ ] Page size matches the template.
- [ ] Coordinates are in points from the **top-left** corner.
- [ ] Address, subject and date have `max_lines` from the box, not from one example.
- [ ] `required` is set only on boxes every letter fills.
- [ ] `kind` is `statement` for statement layouts.
- [ ] Ids are unique and the folder loads without errors.
- [ ] The view agrees with the template's measurements.
- [ ] At least five letters with a known template match it, including the longest address.
- [ ] Two layouts that look alike are told apart, or you accept that portfolio mode flags them
      as ambiguous.
- [ ] No profile, letter or name from your organization is committed here.

## What an automatic route might look like

Everything in this section is `UNVERIFIED`. None of it exists in this repo, and none of it has
been checked against Adobe documentation. It is a list of ideas to look into.

- `UNVERIFIED`: a print-channel template may be exportable in a form that contains the
  positions of its content areas. *To verify:* export one template in your own environment and
  look for coordinates and units.
- `UNVERIFIED`: an `.xdp` file is XML, and its master pages and subforms may carry `x`, `y`, `w`
  and `h` values that could be turned into zones. *To verify:* open an `.xdp` you own; check
  whether positions are absolute, and how flowed subforms are described.
- `UNVERIFIED`: which subform is the address, subject or body is probably not marked in the
  file, so someone would still assign the roles. *To verify:* check whether names or bindings
  follow a convention in your templates.
- `UNVERIFIED`: boxes that grow or move with their content have no single rectangle, so a
  profile could only describe one state of them. *To verify:* render the same template with
  short and long content and compare.

A practical middle way needs no parsing: produce one reference PDF per template with placeholder
text, run `icsel profile` on it, and correct the drafts.
