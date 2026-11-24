# Prompt templates for the optional AI step

The AI step is **off by default**. It only runs when you pass `--llm <backend>` or
set `ICSEL_LLM=<backend>`.

| File | Job | What is sent to the model |
| --- | --- | --- |
| `classify_blocks.md` | Suggest roles for blocks the rules labeled `unknown` | Page size; for every block its index, page, rule-based role, position and size in inches, line count and boldness; **text only for `unknown` blocks** (up to 600 characters each) |
| `explain_fit.md` | One-paragraph "why this fit", only for weak fits, near-ties and no-fits | Scores, offsets, hard-check failures and role counts. **No letter text.** |
| `detect_fields.md` | Mark text as fixed or variable and name the variable fields | **The text of every block except letterhead and footer**, up to 12,000 characters. Needs `--fields-send-text` |
| `tag_purpose.md` | Choose one purpose from a list | **The subject line and first body paragraph**, up to 800 characters. Needs `--purpose-send-text` |
| `propose_layout.md` | Name a proposed layout and say how it differs from the nearest one | Geometry of the draft and the nearest layout, their ids, cluster size. **No letter text.** |

Each file holds the system prompt, a line `---USER---`, then the user prompt.
`$payload`, `$roles`, `$facts`, `$blocks`, `$purposes` and `$letter` are filled in by icsel.
A changed template is a new prompt version, so cached replies are not reused. To change the wording,
copy this folder, edit the copies and set `ICSEL_PROMPTS_DIR` to the new folder.

## Personal data

Letters often contain names, addresses, account numbers and amounts. The text of
`unknown` blocks can include any of these (a table of account details, for
example). **Only enable the AI step for documents you are allowed to send to the
model endpoint you configure**, under your organization's data-handling rules and
the provider's terms. When in doubt, use synthetic or redacted letters, or keep the
AI step off. Set `ICSEL_LLM_TASKS=explain` to use only the explanation, which sends
no letter text.
