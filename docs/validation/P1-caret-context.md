# Caret-context compatibility

Status: accepted by the user on 2026-09-23 at the implemented and validated scope, with commit authorized.

## Acceptance scope

The user requested stopping the current work and treating the item as complete after reviewing the successful Obsidian and Codex-composer checks. Acceptance covers the guarded fallback, focused-control bounds, content-free capture/LLM diagnostics, regression coverage and recorded desktop evidence. It does not certify every text field or declare the earlier Codex mismatch fixed.

Known limitations remain: Sublime Text exposes no usable editor text provider in the tested installation; a separate opt-in adapter would be additional work. The browser composer and broader interaction matrix have not been validated. The earlier Codex-composer mismatch did not recur in the controlled manual test; reason diagnostics will identify the failed boundary if it recurs. These limits do not block the user's acceptance of this change.

## Retrospective evidence

Five recent completed dictations were correlated with their text-action archives and read-only cost records. Reconstructing each request from the archived action input and the current correction system prompt, without surrounding text, reproduced its stored `input_token_estimate` exactly in all five cases. The preset file modification time preceded those requests.

This is strong indirect evidence that these requests omitted surrounding text, assuming the current prompt and request construction match the running client. The estimator is deterministic over UTF-8 byte lengths; this comparison is not a comparison with provider token usage. There is no historical prompt fingerprint or explicit context-presence field, so it is not a direct capture trace. No transcript, context, credentials, or request identifiers are included here.

Diagnostics before this change recorded transcription lengths and LLM outcomes, but neither capture outcomes nor context inclusion. Text-action archives retain the action input, not the surrounding context. Historical failure reasons and application-wide success rates therefore cannot be recovered.

## Confirmed implementation limitation

Before this change, [the worker](../../core/client/caret_worker.py) required `TextPattern2` before querying `TextPattern.GetSelection`. A synthetic editable, non-password control exposing only `TextPattern` returned `{}` with exit code 0; `GetSelection` was never called. This reproduced the earlier finding without interacting with user applications.

The [Microsoft TextPattern contract](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-implementingtextandtextrange) allows `GetSelection` to return a degenerate range at the insertion point when no text is selected. A fallback can use that range after validating a single collapsed selection, editable attributes, and stable focus. It must not read selected text or bypass a password/read-only rejection.

The current worker also rejects disabled controls, unsupported control types, inactive carets, read-only or unknown editable attributes, selections, and changed focus. The parent discards timed-out helpers and changed foreground windows. Empty neighboring text is a valid empty result, not necessarily a compatibility failure.

The snapshot is captured at dictation start and reused for that task. It covers nearby text in the focused editor, not previous messages, another document, or conversation history. An empty chat composer can therefore correctly produce no context even when messages are visible elsewhere in the window.

## Implemented behavior

The worker validates a single collapsed selection first. It prefers an active `TextPattern2` caret and falls back to the independently validated selection for a missing pattern, explicit unsupported-interface/method HRESULTs, or an inactive Pattern2 caret in an otherwise focused editable control. An inactive range itself is never used. Access-denied errors, null active-caret output, and mismatched active-caret/selection positions do not enable fallback. Both paths check editable attributes, keyboard focus, containment in the focused control's `DocumentRange`, and stable focus/selection after capture. Expanded ranges are clipped to that control's bounds, preventing reads into adjacent page content. Character limits and the parent's 1.5-second helper timeout remain in place.

The helper returns controlled status/method identifiers alongside bounded text. The parent validates those identifiers and logs task ID, status, method, character counts and elapsed time. It also reports disabled capture, missing foreground window, busy/closed capture, zero limits, timeout, failed helper and malformed response. Provider exception messages and text do not enter diagnostics.

The existing LLM request-start record now includes `context_chars` from the actual prepared payload and `context_allowed` from the selected preset, associated with the request ID. The capture record uses the recording task ID, also present in final-transcription diagnostics. Counts include the insertion-point marker in LLM payloads but not in the capture's left/right counts. Preset routing and requests remain unchanged.

No template/local setting changes, clipboard fallback, selection-text reading, history capture, network retry, or editor-specific integration was added. Restart the source client before using the changed parent/service modules; configuration reload does not reload Python implementation.

## Target-application evidence and limits

| Target | Current evidence |
| --- | --- |
| Codex extension composer in VS Code | The user identified this as the source of the live `caret_mismatch` around 15:52:39. After restarting the client, their controlled manual test succeeded at 16:01:30: `text_pattern`, 15 characters before and 6 after. The associated LLM request at 16:01:33 included 40 context characters and completed. The earlier failure did not recur in this test and remains unexplained; new `reason` metadata distinguishes its possible branches. No extension UI automation was performed. |
| ChatGPT browser composer | User-reported target; synthetic input and empty-composer checks pending. |
| Obsidian editor | Successful capture after the user positioned the caret in a synthetic note. The focused edit control reported both text patterns available, keyboard focus, a collapsed selection and a writable text attribute, but Pattern2's caret was inactive. The independent selection fallback returned 43 characters before and 6 after in about 500 ms; boolean comparisons confirmed the `LEFT`/`RIGHT` boundaries. Application version: 1.14.2. This is one successful desktop capture, not the complete interaction matrix. |
| Sublime Text editor | User prepared a synthetic buffer. The actual helper returned `unsupported_control` in about 390 ms. The accessibility observation exposed the window, scrollbars and menus, but no editor text control. This installation remains unsupported by the generic UIA path; its build version was not obtained. |

The complete parent/helper path also passed against the prepared Obsidian note: a 68-character reference included the insertion marker and matched both `LEFT`/`RIGHT` boundaries. This exercised the actual subprocess timeout, JSON validation and snapshot assembly without a live LLM request.

The initial desktop observation had an unusable-window error. Automatic approval review rejected broad Obsidian accessibility-tree inspection because it could expose other private notes, and later rejected screenshot refresh because the first image showed an unrelated overlapping window. Those operations were not bypassed; metadata-only activation and the guarded product helper provided the successful check. Probes were scoped to the user's explicitly prepared synthetic documents, returned only controlled metadata and boolean fixture comparisons, and made no LLM request. The sandbox could not observe the real foreground window; successful desktop probes used the existing helper outside the sandbox against the exact observed target window, preserving focus checks. Broad tree inspection was not needed for the successful capture.

Before repositioning, the Obsidian probe returned `selection`. After repositioning, the first implementation returned `inactive_caret` despite focused/writable/collapsed-selection metadata. This real provider behavior justified extending the independent selection fallback beyond absent interfaces. Regression tests reject unfocused, read-only, noncollapsed and out-of-control selections on that path. The entire fixture phrase was not an exact match; the accepted desktop comparison checks the known `LEFT` and `RIGHT` boundaries allowing surrounding whitespace, without printing the note text.

For Sublime Text, a possible separate opt-in integration is an editor plugin using the official [View API](https://www.sublimetext.com/docs/api_reference.html) (`sel()` and bounded `substr()`), with explicit local request/response ownership. That is a proposed direction, not an implemented or installed bridge. Relaxing the UIA control/read-only checks would not create a missing editor text provider.

Use a temporary document or unsent composer containing `Caret test LEFT RIGHT`, with the caret immediately after `LEFT`, and check `captured`, nonzero counts, and the exact split without publishing content. Also check an empty editor, noncollapsed selection, read-only/password controls, and focus changes. Use the existing 800/200 defaults unless testing bounds. For a full dictation acceptance, verify the same task's capture and an LLM request with nonzero `context_chars` when its preset allows context. A capture-only probe requires no microphone or LLM request. Record browser/editor mode and versions during testing.

Consider application-specific accessibility support or adapters only after identifying remaining failure modes. Do not infer broad coverage from one working application; mock forwarding does not establish capture success.

## Checks

### Subsequent live dictation evidence

After the updated client started recording the new diagnostics, five consecutive real dictations on 2026-09-23 had three successful captures followed by completed LLM requests containing nonzero `context_chars` (45, 69 and 45). The other two requests completed without context: one capture reported `caret_mismatch`, the other `unsupported_control`. The user subsequently attributed the mismatch around 15:52:39 to the Codex extension composer in VS Code; the other requests' application attribution remains unknown. This sample establishes live capture-to-request forwarding, not a general compatibility rate or proof that the model used the reference correctly.

Text-action archives intentionally contain only the action input and result, not surrounding text. Their lack of a context section does not demonstrate a failed capture. Diagnose inclusion from the capture record and the LLM request's `context_chars`; that count includes the insertion-point marker. The unresolved Codex-composer mismatch needs controlled reproduction before changing its boundary checks.

The capture record now appends a validated `reason` for `caret_mismatch`: `range_not_collapsed`, `caret_selection_disagree`, `caret_before_control`, `caret_after_control`, or `selection_changed`. Other outcomes use `none`. Only allowlisted identifiers enter diagnostics; malformed or inconsistent helper reasons are rejected. This diagnostic refinement does not alter capture acceptance or read additional user content. It requires restarting the client to update the parent logger/parser. Older helper responses without a reason remain supported.

The subsequent user-driven Codex-composer test succeeded through capture and LLM forwarding. Its 21 captured characters plus the 19-character insertion marker produced `context_chars=40`. This confirms that the composer can work; it does not explain the earlier mismatch or establish reliability across empty, long, multiline and changing inputs. The prior log lacks a reason, so restart alone must not be claimed as its root-cause fix.

### Automated and controlled checks

- Project interpreter: Python 3.11.15 in the existing `capswriter` environment.
- Investigation baseline: 41 existing caret-context/text-action tests passed; TextPattern-only synthetic reproduction returned empty JSON before the fix.
- First implementation checks: 156 focused tests and 658 default tests passed (8 deselected). Additional regressions cover unsupported caret methods, null carets, bounds and privacy across diagnostic sinks.
- One later default run had 662 passes and an unrelated cancellation-accounting test failure: its 30 ms request timeout won the cancellation race. All four terminal-accounting cases passed on focused rerun; no accounting/runtime cancellation changes were made.
- Core fallback default suite: 670 passed, 8 deselected, including inactive-Pattern2 fallback and focused-control range containment regressions. After mismatch-reason diagnostics: 119 focused tests and 679 default tests passed (8 deselected). Syntax, Ruff, internal-language, documentation and diff checks passed; configured mypy targets passed during the core implementation and were unchanged by this refinement. A zero foreground-window ID is rejected before initializing COM, including when the OS reports no foreground window.
- Default pytest temporary/cache directories were inaccessible; runs used fresh workspace temporary directories and disabled pytest cache. Temporary fixtures were subsequently placed under ignored logs to avoid including intentionally invalid configuration fixtures in repository language scans.
- Automated tests and agent-run probes initiated no real microphone input, provider requests, or application restart. Later live dictations and the restart were user-driven. Only prepared editor windows were activated for helper probes; local settings and user documents were not modified by the agent.
- Obsidian has a successful desktop fixture capture; the Codex composer has a successful user-driven capture-to-LLM check after an earlier unexplained mismatch. Sublime remains unsupported by the generic path; browser composer checks, the remaining interaction matrix and population-wide compatibility remain unverified. The user accepted the current scope with these limits.
