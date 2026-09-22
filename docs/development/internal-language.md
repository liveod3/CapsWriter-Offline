# Internal language policy

Maintained project-owned internals use English. Product UI supports English and Simplified Chinese, and task-based user guides remain Chinese. This policy makes the maintained source and developer documentation consistent without translating the data the product processes.

## Scope

Use English for Agent/developer documentation, active plans, source comments and documentation strings, internal identifiers, controlled exceptions, diagnostic file records, development scripts, build messages, and configuration-template explanations.

Keep command names, configuration fields, protocol values, preset IDs, and public interfaces stable. The migration changes prose, not recognition rules or runtime settings. Local root configuration is ignored; this task translates its Chinese comments/documentation only and verifies executable AST equivalence separately.

For product output, use the [localization layer](localization.md): `Notice` preserves English diagnostics, while console formatters and `tr()` render the selected interface language. Do not translate arbitrary backend output or user text.

## Preserved material

| Material | Treatment and reason |
| --- | --- |
| `docs/user/`, root README, documentation index | Chinese task-oriented help, as requested by the user |
| `docs/archive/` | Dated evidence and original release claims; only status/navigation repairs |
| Locale catalog values and test fixture strings | Preserve translated product text and multilingual assertions; comments/docstrings are still checked |
| Chinese ITN rules, units, idioms, character ranges, engine language aliases | Semantic recognition data; translating it changes behavior |
| LLM/model prompts and triggers | Functional input, preserved exactly, including Chinese prompts |
| Toast and formatting demonstration text | Multilingual rendering/recognition fixtures |
| Existing decoder abort sentinel | Recognition-result compatibility; not a newly introduced diagnostic |
| Native/third-party diagnostics | Preserve original messages; project wrappers use controlled English records |
| `core/server/engines/*/export/`, `core/tools/zhconv/` | Copied upstream/derived export and conversion code; retain original text and attribution |
| Model notebooks, binary/model payloads, JSON linguistic resources | Historical experiments or data, outside maintained prose checks; do not execute or rewrite for this migration |
| Ignored local files and user archives | Never read by the repository checker |

Maintained inference modules under `core/server/engines/*/inference/` are included. Their imported implementation patterns are not a reason to exclude project-maintained comments. This boundary is a maintenance decision, not a complete provenance/license audit; the release-hardening TODO still owns that work.

## Run the checks

```powershell
python scripts/check_internal_language.py
python scripts/check_docs.py
```

The language checker reads Git-tracked and visible new files, excluding ignored data. It checks Python/spec tokens, other maintained text formats, and active Markdown. It detects Han characters rather than attempting to classify every natural language. Reviewers must still assess English quality, stale facts, non-Han languages, and ambiguous ownership.

Python comments, documentation strings, and identifiers remain checked even in tests/catalogs. Multilingual literals in tests and the Chinese locale catalog are permitted. Other semantic literals and quoted documentation lines require exact hashes with reasons in `scripts/internal_language_exceptions.json`; changing text invalidates the exception. This avoids copying large prompts into policy files and prevents a whole source module from becoming exempt. Review the source and reason before changing a hash. Do not suppress a new diagnostic sentence as a recognition fixture.

The documentation checker validates local Markdown links/heading anchors and one-title/heading-level structure. It skips examples inside fenced code. The dated Qwen incident has a narrow exception for its ignored local evidence paths. External URLs are not fetched, and editorial quality remains a human check.

Both commands run in quality CI and are available through pre-commit. The existing localization source guards additionally cover display/logging boundaries. The checker regressions exercise new violations, exact exceptions, fixture handling, relative/encoded links, anchors, and private-evidence boundaries.

## Review future edits

Keep user guides task-oriented and follow the [writing guide](writing-guide.md). Update the canonical page when behavior changes, then update incoming links. Preserve historical claims as dated evidence rather than silently rewriting their original observations. Add any newly justified exception with its exact scope and reason; do not broaden exclusions to make a check pass.
