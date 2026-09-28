# Composable correction prompts

Correction presets can compose a mandatory base, an editing level and independent
modules into one system prompt. The service still selects at most one preset and
makes one LLM request. Composition is a set of model instructions, not a
deterministic guarantee of language quality or content preservation.

## Preset modes and compatibility

`LLM/presets.toml` supports two `prompt_mode` values:

| Mode | Source of system prompt |
| --- | --- |
| `custom` (default when omitted) | Existing required `system_prompt`, with the same surrounding-whitespace stripping as before |
| `correction` | Built-in composition from shared client settings; `system_prompt` must be absent |

The shipped `correct_asr` preset now explicitly selects `correction`. Its natural
level and enabled number/punctuation/filler/homophone modules retain the intended
scope of the previous default. English-term restoration defaults off. The existing
Chinese task instructions and insertion examples remain Chinese independently of
UI locale; their source is [correction_prompts.py](../../core/correction_prompts.py).
This is not a claim of identical model output after restructuring the prompt.

Older or custom presets that define only `system_prompt` continue as full custom
prompts. No modules or mandatory base are injected into them. The translation
preset is unchanged and remains custom. A file defining both correction mode and
`system_prompt` is rejected to avoid silently ignoring user instructions. To
opt into composition, preserve the old prompt separately, remove `system_prompt`
and set `prompt_mode = "correction"`; to revert, select `custom` and restore the
full prompt. Provider configuration and legacy Python role files are not rewritten
or executed for migration.

## Model selection and the settings page

The GUI calls the base feature **Text cleanup** and keeps its three editing
strengths and five optional modules separate from advanced preset editing.
Translation and full custom prompts remain file-configured actions; the GUI does
not stack cleanup before them or rewrite existing routing.

Each provider defines its required `model` alongside protocol, URL, timeout and
credentials. Presets select a provider ID; they do not override its model. The GUI
selects only a configured provider for cleanup and displays its model and locally
configured rates read-only. Saving that selection leaves other presets untouched.
The provider model is shared by the request, diagnostics and cost accounting.

## Shared client settings

These preferences belong to `ClientConfig`, not to credentials or caret capture.
They affect any preset explicitly selecting `correction` mode. Per-preset module
overrides and editing the built-in base text through a GUI are not introduced here;
complete custom prompts remain the escape hatch.

| Field | Default | Contract |
| --- | --- | --- |
| `llm_correction_level` | `natural` | One of `minimal`, `natural`, `fluent` |
| `llm_correction_numbers` | `True` | Normalize definite numeric Chinese expressions to Arabic digits; preserve value, precision, units, leading zeroes, uncertain quantities and date wording |
| `llm_correction_punctuation` | `True` | Adjust punctuation and segmentation; only this module permits reference-based boundary punctuation |
| `llm_correction_fillers` | `True` | Remove meaningless hesitations and stutter duplication; retain meaningful emphasis, references, answers and tone |
| `llm_correction_english` | `False` | Restore misrecognized English terms only when supported by actual transcript/reference evidence; do not translate ordinary Chinese or guess proper nouns |
| `llm_correction_homophones` | `True` | Correct well-supported homophone/near-homophone or spelling recognition errors; English restoration remains independent |

`minimal` permits module-local edits without sentence rewriting. `natural` permits
small local grammatical repairs while preserving order and tone. `fluent` permits
sentence restructuring but must retain every meaningful fact, clause, list item,
unfinished idea, condition, negation and tone. Combining meaningless repetition
still requires the filler switch. Summarization is not an editing level.

The mandatory base requires the complete current transcript as output, treats
transcript and reference as data rather than instructions, prohibits guessing or
answering questions, and gives disabled-module restrictions priority over editing
strength. Enabled modules add their instructions in fixed order; disabled modules
instead add explicit preservation restrictions. With all modules off and minimal
editing, the prompt requests the original complete transcript when no authorized
change is needed. This still invokes the selected LLM; turn off correction or LLM
to guarantee zero requests.

All six fields use the [shared settings interface](configuration.md#shared-settings-interface),
including whole-candidate validation, version conflicts and safe task-boundary
publication. Older root configurations can omit them and receive defaults through
the template and request snapshot getters. No ignored local configuration needs
rewriting to activate the defaults. Presets continue to load once per request.

## Request and privacy boundaries

The service captures scalar options and the client context permission before its
first await, then resolves the selected immutable preset once. Changes during
catalog loading or HTTP work affect later requests. The actual resolved prompt
is used by transport, opt-in diagnostic text copies, saved action records and
the existing configuration-revision observation. No additional provider call,
clipboard read, history read or context collection is introduced.

Caret capture now also requires `ClientConfig.llm_enabled`; disabling the master
switch suppresses capture of the snapshot used by both ASR and LLM. Existing
preferences are retained for re-enabling. Reference transmission requires both `ClientConfig.caret_context_enabled` and
the selected preset's `use_caret_context`. Composition preferences do not enable
either permission. The service also enforces both gates for callers supplying a
reference directly. Insertion-point punctuation rules are injected only when both
permissions and the punctuation module are enabled. Missing reference data still
uses the no-reference instruction; the base never authorizes copying surrounding
text into the result.

Upstream transformations are independent: server `format_num` may already have
normalized numbers, and the client's `TextOutput.strip_punc` runs before LLM
processing. Disabling a prompt module preserves what the LLM receives; it cannot
recover an earlier number spelling or removed punctuation. Existing Traditional
Chinese conversion and translation routing also remain independent.

Invalid composition preserves the original action input without sending a request.
Existing timeout, cancellation, provider errors and output fallback behavior
remain in the service. Synthetic tests prove configuration/request behavior, not
the model's compliance with complete-content or linguistic instructions.

## Inspection interfaces

`python start_client.py settings prompt --preset correct_asr` prints a JSON preview
using saved client settings and the current static catalog. It includes mode,
resolved options, context permission and the exact system prompt; it supplies no
transcript/reference and sends no request. Like other file-only commands, it cannot
claim to show effective state in an independently running client. Explicit prompt
inspection may display private custom prompt text, but never provider credentials.

`ClientOperations.inspect_prompt(preset_id)` uses the running client's detached
effective settings and the same resolver; it does not apply pending changes.
Candidate editors may pass validated detached values to `inspect_prompt` for a
preview before saving. `PromptPreview` omits the prompt from its representation;
frontends must deliberately display its `system_prompt` field and not log it.

The pure catalog implementation lives in `core/llm_config.py` so inspection avoids
the client package's startup/configuration side effects. Existing
`core.client.llm.config` imports remain compatible through re-exports.
