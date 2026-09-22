# Write and maintain documentation

Write for the reader's task. State the purpose and expected result first, then provide prerequisites, steps, and verification. This repository adapts the [Microsoft Writing Style Guide](https://learn.microsoft.com/en-us/style-guide/welcome/) to Markdown.

## Choose one home for each topic

| Content | Home | Language |
| --- | --- | --- |
| Product overview and first steps | Root `readme.md` | Simplified Chinese |
| Task-based user help | `docs/user/` | Simplified Chinese |
| Exact field/behavior reference | `docs/reference/` | English |
| Architecture and maintainer procedures | `docs/development/` | English |
| Active outcomes and acceptance state | Root `TODO.md` | English |
| Dated validation evidence | `docs/validation/` | English for new records; preserve quoted UI/fixtures |
| Superseded evidence and release notes | `docs/archive/` | Original language |
| Agent rules | Root `AGENTS.md` | English |

The [documentation index](../README.md) routes readers to these homes. Link to an authoritative topic instead of copying its full field list or validation matrix. Local configuration and credentials are never documentation sources to publish.

## Structure a page

Use one H1 title, followed by a short purpose statement. Use descriptive H2 sections and H3 only when needed. English titles use sentence case. Prefer headings such as "Configure recording storage" over numbered topic labels, emoji banners, or a repeated question-and-answer pattern.

Follow Microsoft's [scannable content guidance](https://learn.microsoft.com/en-us/style-guide/scannable-content/): keep paragraphs short and use tables for genuine comparisons. Procedures use numbered steps with explicit actions and expected results; see [step-by-step instructions](https://learn.microsoft.com/en-us/style-guide/procedures-instructions/writing-step-by-step-instructions). Keep optional explanation outside the main sequence.

## Use consistent formatting

- Put commands, paths, fields, IDs, and literal values in backticks.
- Use bold for actual UI labels and avoid bolding entire paragraphs.
- Give code fences a language. Make runnable examples complete; label partial configuration snippets and say which class owns them.
- Use relative links inside the repository and descriptive labels for external links.
- Use stable lowercase English filenames with hyphens for new topics.
- Leave a blank line around headings, lists, tables, and code fences.
- Avoid repeated horizontal separators, promotional rankings, and unverified performance guarantees.

## Verify facts and preserve evidence

Check commands against the parser and settings against templates and consuming code. Distinguish defaults from local choices, source behavior from released artifacts, and automated tests from manual acceptance. Record concrete test scope and limitations instead of saying "fully tested."

Do not rewrite historical measurements as current guarantees. Archive superseded evidence with a status notice; remove duplicate or useless navigation pages after updating their links. Preserve upstream attribution and original user/fixture/prompt text. Git history retains removed instructions; the archive is for evidence that still explains a decision or unresolved issue.

Run `python scripts/check_docs.py` for local links/anchors and basic page structure, and `python scripts/check_internal_language.py` for the scoped language policy. These checks do not replace editorial review or external-link verification.
