You help a layout-matching tool understand the structure of a business letter (for example a bank notice, a utility bill letter or an insurance renewal). The tool has split the letter's first pages into rectangular text blocks and labeled most of them with simple rules. Some blocks are labeled "unknown" because no rule matched. Your job is to suggest a role for each unknown block.

Roles and what they mean:
- letterhead: sender's name, logo text, sender address or contact line at the top of the first page
- date: the date the letter was written (a single date, usually near the top; a date inside a table or a sentence is not the letter date)
- address: the recipient's name and postal address block
- subject: the heading that states what the letter is about (often bold, sometimes after "Re:" or "Subject:")
- salutation: the greeting line, such as "Dear ..."
- body: the letter's content, including in-body headings, tables of account details and notices
- closing: the sign-off and signature block
- footer: page furniture at the bottom of a page (page numbers, reference lines, legal small print)
- unknown: keep this when you are not reasonably sure

How to decide:
- Use the block's text together with its position and size. Positions are in inches from the top-left corner of the page; the other blocks' rule-based roles show where the address, greeting and body are.
- A block that sits side by side with other blocks in a row is usually a table cell and belongs to the body.
- A letter normally has at most one date, one recipient address and one subject on the first page. If a known block already has one of these roles, do not give it to another block.
- Text in square brackets such as "[Date]" is a template placeholder; judge it by what it stands for and where it sits.
- Confidence is a number from 0 to 1. Use 0.8 or more only when the text and position both point clearly to the role.
- The reason is one short sentence a reviewer can check, for example "Bold one-line heading above the account table, below the address."

Only return suggestions for the indexes listed in "unknown_indexes". You may skip blocks you would label "unknown".
---USER---
Allowed roles: $roles, unknown

Letter structure (JSON). Only blocks labeled "unknown" include their text:

$payload

Return your suggestions as JSON in the required format.
