# Prompt templates for the optional AI step

The AI step is **off by default**. It only runs when you pass `--llm <backend>` or
set `ICSEL_LLM=<backend>`.

| File | Job | What is sent to the model |
| --- | --- | --- |
| `classify_blocks.md` | Suggest roles for blocks the rules labeled `unknown` | Page size; for every block its index, page, rule-based role, position and size in inches, line count and boldness; **text only for `unknown` blocks** (up to 600 characters each) |
| `explain_fit.md` | One-paragraph "why this fit" | Scores, offsets, hard-check failures and role counts. **No letter text.** |

Each file holds the system prompt, a line `---USER---`, then the user prompt.
`$payload`, `$roles` and `$facts` are filled in by icsel. To change the wording,
copy this folder, edit the copies and set `ICSEL_PROMPTS_DIR` to the new folder.

## Personal data

Letters often contain names, addresses, account numbers and amounts. The text of
`unknown` blocks can include any of these (a table of account details, for
example). **Only enable the AI step for documents you are allowed to send to the
model endpoint you configure**, under your organization's data-handling rules and
the provider's terms. When in doubt, use synthetic or redacted letters, or keep the
AI step off. Set `ICSEL_LLM_TASKS=explain` to use only the explanation, which sends
no letter text.
