# From local tool to production: open questions

icsel is a local prototype. It has no authentication, no multi-user storage and no connection to
AEM. This page lists what someone would have to find out before using anything like it inside
AEM. Every statement about AEM or Adobe services here is `UNVERIFIED` and written as a question.
Nothing here is an instruction.

## What exists

- A command line tool and a browser UI for one person on one machine. The UI listens on
  127.0.0.1 and has no login.
- PDF in; JSON, CSV, Markdown and a sample PDF out. Nothing is sent anywhere unless someone
  switches the AI step on.
- Tests on synthetic letters only. How well it works on a real set of letters is unknown.

## Where would it run? (`UNVERIFIED`)

- Could it be a separate service that AEM calls, so that Python and PDF parsing stay outside
  AEM? Which outbound calls does your environment allow?
- Or a component or asset action inside AEM? Would that need a rewrite in another language?
- Who triggers it: an author picking a template, or a batch job over an archive?

A separate service looks simpler, because the code would not change. That is an opinion, not a
finding.

## Using Interactive Communications (`UNVERIFIED`)

- Is there a supported API to list templates, read their geometry, or create a draft Interactive
  Communication in a chosen template?
- Can a real preview be produced by API? If so, it should replace the sample PDF.
- How would shared-text candidates relate to document fragments, and detected fields to a form
  data model? Who decides that a candidate becomes a fragment?
- Which of these APIs exist in your product version and deployment?

## Authentication and access (`UNVERIFIED`)

- Which identity does the service use, with which permissions? Read-only for templates?
- How are users signed in, and how is it checked that a user may see a given letter?
- The browser UI here must not be exposed as it is.

## Letters and personal data

- Where do letters live, for how long, and who can read them? icsel keeps uploads and results in
  `out/web/` until someone deletes them.
- Result JSON, `fields.csv`, `shared-text.csv` and the AI cache contain letter text.
- The AI step can send names, addresses and amounts (`fields`, `purpose`, and text of unlabeled
  blocks in `classify`). `fragments` sends wording that several letters have in common, which
  can still name a person. Which endpoint is approved for that, in which region, with which
  retention?
- What happens with a deletion request?

## Logging and audit

- icsel keeps no log beyond its output files. Who needs to know which letter was matched, with
  which settings, and who accepted the result?
- A record should hold the profile and settings versions, not letter text.

## Approvals

- Who approves a new profile, a changed weight, or a changed prompt?
- Should a person confirm every layout choice? Weak fits, near-ties and no-fits are flagged, so
  at least those can go to a person.
- What score is good enough? 0.8 is a default, not a tested threshold.

## Risks of the sample PDF

The sample is a local approximation drawn with reportlab:

- fonts, line breaks, hyphenation and spacing differ, so text that fits here may not fit in the
  real output, and the other way round;
- there are no fragments, conditions, data binding, real pagination rules or images;
- a good-looking sample can be taken for proof. It only shows that boxes and line counts agree.

Do not use it for approval, for proofing, or to send to anyone as a letter.

## Other risks

- Block labels come from rules for typical business letters. Other languages, right-to-left
  text, multi-column letters and scans are not covered.
- A layout profile goes stale when its template changes. Nothing detects that.
- Statement detection was tested on synthetic statements of one design.

## A cautious order of steps

1. Run icsel locally on letters you may use, with profiles you measured.
2. Compare its choice with what your experts would choose. Stop if it does not help.
3. Answer the questions above with your platform, security and privacy teams.
4. Only then decide where it should run, and replace the sample PDF with a real preview if one
   is available.
