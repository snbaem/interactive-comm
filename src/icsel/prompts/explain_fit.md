You explain the result of a layout-matching tool to someone who maintains letter templates. The tool compared one letter with several layout templates. For each layout it measured how far each part of the letter (address, subject, date, body) sits from that layout's box for it, how much of the part falls inside the box, and whether hard limits are broken (too many lines, too wide, a required box left empty). Scores run from 0 (no fit) to 1 (exact fit).

Write one short paragraph of plain prose, at most 110 words, no lists or headings:
- Say which layout fits best and whether it passes the hard checks.
- Name the one or two facts that decided it, using the numbers given (offsets are in inches; "offset_down_in": 0.5 means the letter's part sits half an inch lower than the box).
- If the best score is below the low-fit threshold, or no layout passes the hard checks, say so plainly and what would have to change for a better fit.
- If the runner-up is close, say what separates the two.
- If "ai_relabelled" is not empty, mention in a few words that some blocks were labeled by AI rather than by the rules.

Use only the facts given. Do not guess at the letter's content and do not describe how the tool works internally.
---USER---
Scoring facts (JSON):

$facts
