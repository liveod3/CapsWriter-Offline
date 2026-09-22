# Internal English and documentation reorganization

Status: accepted by the user on 2026-09-22, with commit authorized. Scope: the complete P1 **Standardize project-owned internals on English** outcome, plus the requested Microsoft-style documentation organization.

## Delivered changes

- Reorganize Chinese task-based user help under `docs/user/`, English references under `docs/reference/`, and developer material under `docs/development/`, with one documentation index.
- Rewrite Agent/navigation/template guidance in English. Replace obsolete build instructions, correct stale storage/reload/timeout claims, and remove unverified performance rankings.
- Archive old release notes without translating historical claims. Retain audits and the unresolved Qwen incident as dated evidence. Remove duplicate retired-role/hotword redirects and update incoming links.
- Translate maintained source comments/docstrings, internal development output, template explanations, and the standalone Ollama experiment's internal prompt identifier. Preserve public IDs, recognition rules, runtime prompts, localized UI, and fixtures.
- Translate only Chinese prose in the two ignored root configurations; preserve executable statements and values. Both default templates remain tracked; local copies remain ignored.
- Add scoped language and Markdown link/structure checks, documented exceptions, regression coverage, CI steps, and local hook definitions.

## Automated evidence

The selected project environment is `capswriter`, Python 3.11.15.

| Check | Result |
| --- | --- |
| Default pytest suite with configured coverage | **541 passed, 8 deselected**; **85.22%** coverage against the configured 50% threshold |
| Compileall | Passed for entry points, templates, `core`, `LLM`, tests, scripts, and both existing local configurations |
| Ruff | Passed for entry points, templates, `core`, `LLM`, tests, and scripts |
| Configured mypy targets | Passed; 7 source files |
| Internal language guard | 300 files after archiving Claude navigation; no violations |
| Documentation links, anchors, and heading structure | 44 pages; no violations |
| Git whitespace check | Passed |
| Configuration tracking | Both templates tracked; both root configurations ignored |

Before/after static comparisons confirm unchanged executable ASTs for 180 changed Python/spec files, after removing documentation-only string expressions. This includes both local configurations and both templates. Six other files contain reviewed changes to development output, the standalone Ollama demo's internal identifier, or verbose-regex comments. The demo's assignments, prompts, and fixtures are preserved after normalizing that identifier; compiled regex instructions are identical. Both public LLM TOML files parse to exactly the same data as before the migration.

The first test attempt encountered access-denied errors in the existing pytest temporary/cache directories. The successful run used a new, verified task-owned temporary directory and disabled pytest's cache provider. No existing temporary directory was removed. The pre-existing caret-context TODO entry remains unchanged.

## User acceptance

The user approved the completed work on 2026-09-22 and specifically confirmed that retaining Chinese user guides made the documentation easier to scan. The final follow-up archives the retired Claude navigation page and updates its incoming links; AGENTS.md remains the active rule source. Documentation and language checks were rerun after this change. This acceptance does not claim additional microphone, model, or package validation.

## Manual review checklist used for handoff

1. Open the [documentation index](../README.md). Check the task grouping, Chinese wording, heading hierarchy, tables, and navigation in your Markdown viewer.
2. Review [setup](../user/setup.md), [file transcription](../user/transcription.md), [text actions](../user/text-actions.md), and [configuration/storage](../user/configuration.md) against your usual workflow.
3. Review [Agent rules](../../AGENTS.md), [architecture](../development/architecture.md), [build guidance](../development/build.md), and the [language exception policy](../development/internal-language.md).
4. Inspect the two local configuration files. Confirm comments are readable and your shortcut/device/model/storage/LLM values are unchanged. Do not share credentials or full private configuration.
5. Optionally perform ordinary dictation and a file transcription using your existing setup. Check interface language still follows your preference and saved user text retains its language. This documentation/prose change does not require switching models or contacting a new provider.
6. Report corrections or accept the whole item. After acceptance, mark the TODO complete and create the commit.

## Limits

No real microphone, global key simulation, model download, GPU management command, network LLM request, or package build is part of automated self-review. External download/model URLs are retained as source pointers, not a claim that release artifacts were downloaded or tested. Historical/private evidence remains outside version control. Broader hardware, model quality, and clean-machine release checks stay in their existing TODO items.
