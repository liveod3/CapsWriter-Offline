# Composable correction prompts

Implemented on 2026-09-27 after acceptance and commit of the shared settings item.
Accepted by the user on 2026-09-27 with commit authorized. The user deferred live
manual option comparisons until GUI controls are available; no model-quality
claim is added by this acceptance.

## Delivered behavior

- A mandatory Chinese correction base, five independent modules and minimal,
  natural or fluent editing, assembled deterministically into one system prompt.
- Shared client settings with validated scalar types/defaults, safe reload and
  request-entry snapshots; no extra LLM calls or context capture.
- Explicit preset modes: shipped correction uses composition, while legacy full
  custom prompts and translation remain custom. Ambiguous definitions fail safely.
- Client/preset reference gates enforced before transmission and reference-based
  punctuation injection. Saved action prompts match the actual transport prompt.
- File-only `settings prompt` inspection and an async effective-state inspection
  method for future GUI use. Shared catalog imports avoid executable local config
  or client initialization in the file-only path.

See the [prompt contract](../reference/correction-prompts.md) for exact defaults,
mode migration, module/strength precedence and upstream formatting limitations.

## Automated evidence

Environment: existing `capswriter` Conda environment, Python 3.11.15 on Windows.
Final default suite: **793 passed, 8 deselected**. Syntax checks, Ruff, configured
mypy targets, internal-language/documentation checks and `git diff --check` passed.
The focused composition/request/settings suite passed 127 tests before the final
record-permission additions. No new configured coverage measurement is claimed;
the configured coverage modules were not changed by this item.

Tests cover:

- Module enable/disable instructions across all editing levels, preservation rules,
  number/English safeguards and invalid scalar options.
- Both context permissions, punctuation permission, reference omission, and saved
  records containing only the reference actually sent.
- One request with the resolved prompt, no mutation of the catalog preset, settings
  changes during catalog loading, and next-request application.
- Legacy prompt byte content after the existing whitespace normalization, unchanged
  translation selection, explicit/default routing and preset file reload.
- Shared settings validation/save and effective prompt inspection before/after
  publication; missing newer fields use defaults.
- Invalid composition falls back without dispatch; existing disabled, missing-key,
  timeout/cancel and provider-failure tests run in the default suite.
- Preview/request equality and a guarded subprocess proving settings prompt
  inspection imports neither executable local config nor audio/Tk/application code.
- Locale-independent task instructions with narrowly scoped source-check exceptions;
  product notices and CLI help remain in the locale catalogs.

Synthetic transports return fixture text; they do not demonstrate semantic model
compliance, transcription accuracy, fluency or absence of omissions. No live
provider request, microphone input, UI Automation read, application restart,
model loading or package build was performed by the agent.

## Manual acceptance

Restart the client once for the new source and composed preset format. Then verify:

1. Compare minimal/natural/fluent on the same meaningful multi-clause input; retain
   all facts, negation, conditions, list items, unfinished content and tone.
2. Toggle numbers, punctuation, fillers, English restoration and homophones
   independently. Include meaningful repetitions and ambiguous proper nouns;
   verify stronger editing does not bypass disabled modules.
3. With authorized reference enabled, check insertion boundaries without shortening
   the complete current transcript. Confirm no-reference behavior separately.
4. Account for ASR numeric normalization and client punctuation stripping before
   interpreting an LLM module's effect.
5. Confirm next-task application of saved options and preserved custom/translation
   presets. GUI controls and packaged behavior belong to subsequent work.

Root local settings, provider credentials and user records were not modified.
The tracked default preset now opts into composition; older local client files
receive missing-option defaults without forced rewriting.
