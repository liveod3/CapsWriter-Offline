# P1 settings GUI and preset editor

Date: 2026-09-27; acceptance updated 2026-09-28. Status: the user accepted the functional source GUI baseline and authorized committing it. Physical desktop and complete packaged-release checks remain separate follow-ups. The reported startup recording symptom still needs clarification between an actual Recording/red-dot state and Windows microphone-use indication. The preceding composable-prompt work was accepted and committed as `58727c6`; this record concerns the GUI, client desktop entry and integrated Tk shutdown work.

## Status dashboard and diagnostic workspace

Accepted by the user on 2026-09-28, followed by a request for paused startup. Status now shows today's
saved dictations and recorded token usage, monthly LLM costs, and the latest ten
saved results with a Today filter and explicit complete-result copy. Cost sources,
currencies, missing usage and partial reads remain distinct. Diagnostics groups
logging options, runtime actions and readable metadata reports with explicit copy;
accounting preferences move to Records.

Validation used the existing Python 3.11.15 environment: **1035 passed, 11
deselected** in the default suite. Synthetic cases cover actual versus estimated
usage, adjacent-month timezone boundaries, unknown/currency distinctions, malformed
and bounded reads, empty/unavailable ledgers, latest-ten/today filters, changed or
truncated copy targets, visible-only refresh, busy retries, stale callbacks,
keyboard copy, stable rows, metadata-only reports and close during active reads.
Syntax, Ruff, configured mypy, internal-language, documentation and diff checks
passed. English and Simplified Chinese Status/Diagnostics were rendered at
800x600, 1120x860 and 1440x900 without horizontal overflow. Preview row height was
corrected after inspecting the first render; subsequent captures show readable
timestamps and wrapped result text. Fixtures used synthetic transcripts, usage and
diagnostics only; no real audio, provider requests, user records or clipboard
contents were read. Physical mixed-DPI interaction remains manual validation.

The preceding Dictation changes were accepted and committed as `d224e65` before
this implementation began.

## Dictation organization and final output formatting

Accepted by the user on 2026-09-28 with commit authorized. General contains interface
preferences; Dictation separates capture, formatting and insertion; Records owns
save preferences. The LLM page uses the label LLM processing. Page-ID navigation
keeps home shortcuts and device watch/discovery on the correct pages.

Traditional conversion and punctuation removal now run after optional LLM
processing. Synthetic pipeline checks cover successful cleanup/translation,
disabled/failure fallback, final-text length thresholds and disabled formatting.
Insertion, UDP, cached output and final archives agree; raw ASR and action stages
remain intact. Existing cancellation/focus tests passed. Final default suite:
**1024 passed, 11 deselected**, using the existing Python 3.11.15 environment.
Syntax, Ruff, configured mypy, internal-language/docs and diff checks passed.
General, Dictation, LLM processing and Records were rendered in English/Chinese
at 800x600, 1120x860 and 1440x900; no horizontal overflow remained. A smaller
recognition-choice minimum width accommodates the narrow scrolled Dictation page.
Synthetic device visibility/home-link/group-ownership checks passed. Real audio,
LLM requests and actual user configuration were excluded; physical mixed-DPI
interaction remains manual validation.

## Provider selection autosave

Accepted by the user on 2026-09-28 with commit authorized, covering the final
Text processing, configured-provider details, master-switch gating and autosave
implementation. Remove the separate save
button. User activation of the provider dropdown immediately submits a serialized,
revision-checked save. Programmatic loading and metadata refresh do not write.
The controls are briefly disabled during the save, avoiding overlapping selections.
Failure is shown inline and retains the draft; selecting the same choice retries.
Queued/active saves finish before close or exit; failure cancels pending exit.
Next requests read the saved provider, while in-flight snapshots stay unchanged.
Validation: **1018 passed, 11 deselected** in the default suite using the existing
Python 3.11.15 environment. New synthetic tests cover keyboard activation, no
writes on refresh/unchanged choice, inline failure and same-choice retry, queued
polling, external conflicts, and close/exit success/failure. Syntax, Ruff,
configured mypy, language/docs and diff checks passed. English/Chinese off/on
renders at 800x600, 1120x860 and 1440x900 passed spacing/overflow checks; the button
is absent. No real provider calls or user configuration writes were performed.
Physical mixed-DPI desktop interaction remains manual validation.

## Provider-only selection and read-only details

Historical implementation stage dated 2026-09-28; superseded by the accepted
provider-autosave implementation above. This supersedes the model
editor and per-preset model override described in the historical stages below.
The GUI only chooses a configured provider; the model remains fixed in that
provider configuration. Below the selector, a read-only panel shows the model,
API type and configured rates with currency, per-million token units and update
date. Missing, invalid and expired rates are distinguished. No price lookup or
provider request occurs. Rate and credential refresh preserves selection drafts.
Provider help and selection have an explicit 20-pixel minimum layout gap. Master
switch gating and preservation of advanced presets remain in place.

Verification: **1011 passed, 11 deselected** in the default suite on the existing
`capswriter` environment, Python 3.11.15. Syntax, Ruff, configured mypy, internal
language, documentation and diff checks passed. Synthetic boundary checks cover
exact endpoint/model rate matching, zero versus unknown, invalid/expired rates,
metadata refresh, provider-owned request models and untouched connection/translation
configuration. English and Simplified Chinese off/on layouts at 800x600, 1120x860
and 1440x900 passed horizontal-overflow and help-spacing checks; representative
renders were visually inspected. No actual user configuration, live provider
requests, microphone input or UI Automation capture was used. Physical mixed-DPI
interaction and live service connectivity remain outside this verification.

## Configured providers and the LLM master switch

Historical implementation stage dated 2026-09-28; superseded by the accepted
provider-autosave implementation above. Remove the expandable provider
editor from the GUI. Keep selection from the existing catalog, with disabled
missing-credential entries, preserved saved selections and empty-list guidance.
Credential readiness follows explicit environment-variable precedence in the
request process; it is not an endpoint health check. No provider configuration is
created or changed merely by opening or selecting an entry.

The LLM master switch disables complete rows/cards for model selection, cleanup,
caret context, LLM record permissions, context diagnostic copies and accounting.
The switch remains usable. Disabled labels, inputs, help and toggles are muted;
values and model drafts survive off/on transitions, reloads and foreground-operation
completion. Other audio/ASR/transcript settings remain independent. Regression
checks cover these transitions and prove provider selection preserves connection
files and other presets. The capture entry point now checks the LLM master switch
before dispatching any UI Automation work; disabling it suppresses the shared
ASR/LLM context snapshot without resetting the saved context permission. Final
verification: **1005 passed, 11 deselected** in the final default suite. It includes off/on layouts in English and Simplified Chinese at
800x600, 1120x860 and 1440x900 with no horizontal overflow. Syntax, Ruff, configured
mypy, language/docs and diff checks passed. The capture/privacy tests passed
121 cases after updating the existing capture fixture to explicitly enable LLM.
No real user configuration, provider request, audio or UI Automation capture was
used. Physical mixed-DPI interaction and real service connectivity remain manual
scope.

## Text processing and model selection

Historical implementation stage dated 2026-09-28; superseded by the accepted
provider-autosave implementation above. The common page now exposes
Text cleanup with three strengths, a distinct five-module group, independent
caret controls and compact bilingual help/dividers. General and Dictation were
accepted and committed first as `a468e9e`.

The fixed cleanup model selection and expandable connection editor preserve
separate saves, revisions, other drafts and existing secrets. Tests cover new
connections without model/key duplication, per-action model overrides and legacy
fallback, malformed/unresolved models, translation isolation, custom prompts and
context permissions, external changes, missing cleanup entries and effective
preset-file paths opened by the parent GUI. Runtime mocks check that diagnostics,
accounting and transport use the same resolved model. Advanced defaults and
capability switches remain unchanged and receive contextual guidance.

Synthetic English and Simplified Chinese layouts at 800x600, 1120x860 and 1440x900
were inspected with provider management collapsed and expanded. The offscreen
renderer explicitly registered system fonts for accurate glyph metrics; production
font policy is unchanged. No real client/server, microphone, transcript history,
caret reads, provider requests or writes to actual user configuration were used.
Physical mixed-DPI/focus/accessibility, live model compliance and packaging remain
manual release scope.

Verification: **997 passed, 11 deselected** in the final default suite; the focused
GUI/text-action/composition suite passed **185 tests**. Syntax, Ruff, configured
mypy, internal-language, documentation and diff checks passed. The initial baseline
run hit inaccessible shared pytest temporary directories; both the successful
985-test baseline and final run used fresh ignored workspace test directories.
The existing native audio tests and complete packaging were not repeated because
this increment does not change audio ownership, native callbacks or packaging.

## General and Dictation acceptance

On 2026-09-28 the user accepted the General and Dictation settings refinements and authorized committing the accumulated settings/autosave/help/device work before the Text page redesign. Physical hotplug, mixed-DPI and packaged-release checks remain separate release scope.

## Settings autosave and exit confirmation

Follow-up dated 2026-09-28, pending user acceptance. Explicit desktop exit now asks for confirmation with Cancel as the default and Escape action; recording or processing adds an interruption warning. Accepted exit flushes valid pending client settings before stopping the owned client. Save failures cancel shutdown and preserve drafts; invalid drafts and catalog edits require separate discard confirmation. Closing into the tray keeps the previous behavior.

Client settings autosave after a 600 ms edit debounce, with numeric editing committed on completion. Local field feedback and shared full-candidate validation reject invalid input; revision checks and atomic replacement remain authoritative. No save operation disables the client controls. In-flight acknowledgements advance only the submitted draft, so newer edits receive another save. Failures remain inline with explicit retry and no automatic retry loop. External revisions keep the existing conflict protections. Provider/preset mutations retain explicit entry saves.

Every common field has bilingual guidance and accessible descriptions; the later compact-help refinement below replaces permanent explanations with adjacent help buttons. Short numbers/codes/choices use bounded controls. Advanced file editing moves to its own settings page. GUI language is explicitly documented as requiring full client exit and reopening, independent of task-boundary locale updates in the hidden client.

Validation: **951 passed, 10 deselected** in the final default suite. Coverage includes delayed writes, rapid edits, invalid and oversized input, cross-field recovery, save failure/retry, external conflicts, unchanged runtime indicators, safe dialog defaults and exit flush/failure. Syntax, Ruff, configured mypy, internal-language, documentation and diff checks passed. Synthetic English/Chinese layouts at 1120x860 and 800x600 were inspected using only public configuration templates. No real client/server, microphone, user records, provider requests or user configuration writes were involved. Physical multi-monitor/DPI and user interaction review remain pending; packaging and native visibility tests were not repeated because entry points and native window ownership are unchanged.

## Compact context help

Follow-up dated 2026-09-28, pending user review. Replace permanent field explanations with circular question-mark buttons beside client and catalog labels. Native rich-text tooltips use escaped public help strings, a bounded reading width and click/F1 access in addition to hover. Keep validation errors and compact changed-state badges visible. Reduce form spacing, simplify the General introduction and autosave footer, and put explicit catalog-save guidance inside the editors. Groups appear in two columns when at least 920 logical pixels are available, preserving single-column reading order at narrower sizes and retaining existing widget identities through reflow.

Verification: **94 GUI tests passed**, including native tooltip dispatch, click/keyboard access, no configuration writes and repeated wide/narrow reflow without horizontal overflow. The reflow test caught a minimum-width lock after shrinking two columns; using viewport width for layout decisions and bounded caption widths resolved it. Syntax, Ruff, internal-language, documentation and diff checks passed. English/Chinese synthetic renders were inspected at 1440x860, 1120x860 and 800x600, including the help popup. No real applications, audio, private records or local user configuration were used. The default suite was not repeated for this presentation-only follow-up; the preceding autosave/exit revision passed 951 tests. Physical mixed-DPI/monitor placement and user visual acceptance remain pending.

## Language and microphone choices

Follow-up dated 2026-09-28. The user accepted the simpler compact-help presentation and requested preset recognition-language choices and automatic input-device discovery. Implemented localized choices backed by canonical server language keys, with explicit model-support limits. Microphone discovery runs automatically on the first General-page visit and on Refresh, using an isolated, bounded, metadata-only helper. Device refresh keeps controls enabled and preserves drafts, saved defaults, legacy indices and unavailable/custom selectors. Duplicate host-qualified names are disabled for new selection. Device changes autosave with the existing restart requirement.

Verification: **975 passed, 10 deselected** in the default suite. Coverage includes canonical language mapping, input-only filtering, duplicate interfaces, output limits, hidden same-installation source/frozen command construction, timeout/failure sanitization, early startup import isolation, typed legacy settings, autosave, refresh during edits and close during discovery. The first full run exposed a test setup that mutated an already-selected default item's data without an edit signal; the regression now loads the legacy value through the settings service, matching real startup. The final focused GUI/device suite passed **117 tests**, including the subsequent case-insensitive ambiguous-name refinement. Syntax, Ruff, configured mypy, language/docs and diff checks passed.

English/Chinese synthetic layouts were inspected at 1440x860, 1120x860 and 800x600. The actual metadata helper successfully found 14 local input entries and the system default without opening a stream; interface aliases can produce multiple entries per physical device. Only counts/status were exposed in verification output. No actual client/server was started or restarted, and no recording, provider request, transcript read or user configuration write was performed. Real device switching after restart, physical hotplug, mixed-DPI and complete frozen-product validation remain manual acceptance scope. The frozen command path is covered by mocks, not a new packaged build.

## Automatic device updates

Follow-up dated 2026-09-28, pending user acceptance. The previous input-channel filter still exposed every PortAudio host interface, including WDM-KS kernel pins. The normal dropdown now uses a single preferred endpoint backend (WASAPI first), shows friendly names and retains nonpreferred saved selectors only for compatibility. Native Core Audio notifications publish a bounded marker, then the Qt owner debounces and refreshes through the existing worker. Hidden pages retain pending changes; registration failure enables a 15-second visible-page fallback. In-flight changes coalesce into one follow-up. Open menus defer updates, identical results retain their model, and populated forms avoid transient loading-row movement. Closing unregisters callbacks before release and ignores late events; a failed unregister retains references for a cleanup retry.

Verification: **982 passed, 11 deselected** in the default suite. A separate native Windows test successfully registered and unregistered notifications and exercised the actual callback vtable using synthetic added/removed/state/default/property events, including the by-value property key. This did not change Windows settings. The final focused GUI/device/native suite passed **125 tests**, including the subsequent failed-unregistration cleanup refinement. Syntax, Ruff, configured mypy, internal-language, documentation and diff checks passed. Pure/Qt cases cover filtering/fallback, hidden-page deferral, notification bursts, fallback timing, late events, changes during discovery, open-menu stability, legacy selections and default-label updates.

Read-only local metadata discovery found 14 raw input interfaces and **2 visible microphones**, plus the system-default choice. English/Chinese synthetic dropdowns and settings layouts were inspected. No real audio stream, client/server, user configuration edit or system-default change was used. This verifies actual notification subscription and callback ABI, but physical headset plug/unplug delivery and real stream switching remain user acceptance checks; existing audio monitoring/recovery was intentionally preserved. Complete frozen packaging and physical mixed-DPI checks remain outside this increment.

## Stable microphone layout

Follow-up dated 2026-09-28, pending user acceptance. Initial discovery previously inserted a muted loading label below the microphone row, then removed it, moving Release microphone when idle into its former position. Discovery now stays silent, including first use. Error/unavailable/partial results use a compact inline status icon with retained space while hidden; details support hover, click and keyboard through the shared help widget. Retries retain any existing error until a new result arrives. No query result adds or removes a layout row. Both General groups have subtle one-pixel separators between individual settings.

Verification includes deterministic narrow/wide geometry comparisons across a held initial query, success, timeout, retry and recovery, plus access to error details. English/Chinese synthetic normal and compact renders were inspected. Query ownership, native callbacks and audio behavior are unchanged; no real client, microphone or local user settings were used. The final focused GUI suite passed **113 tests**; syntax, Ruff, internal-language, documentation and diff checks passed. Reflow validation caught compact-width overflow from the new icon slot; a bounded microphone minimum width resolved it. Physical DPI/user review remains pending, and the default/native suites are not repeated for this presentation-only change.

## Desktop worker crash investigation

The user reported repeated `python.exe` memory-access error dialogs during desktop lifecycle testing. The previous 829-test run only required worker termination, not a successful exit code, so its passing result did not establish safe shutdown.

A synthetic worker reproduction captured exit code `3221225477` (`0xC0000005`) and a fatal `_enter_buffered_busy` error for buffered stdin during interpreter finalization. The daemon control thread continued reading after acknowledging shutdown. Reproduction disabled Windows error dialogs only in the test process and inherited test children, and captured stderr; it did not change system settings or start real audio, services, or model requests. Read-only process inspection identified only the user's existing client/server process tree after the original tests, and no such process was terminated. No matching Windows Application Error event or local Python crash dump was found.

The control thread now acknowledges shutdown and leaves its read loop, uses non-daemon ownership, and coordinates output closure with finalization. Output EOF makes the parent close worker stdin, covering an application that returns independently. The stop thread is signaled and joined, and stdout is restored before closing its replacement. Regression assertions require exit code zero, not merely a reaped process. Isolated checks cover normal shutdown, constructor failure and independent application return: all three workers exited zero with empty captured stderr. Broader desktop acceptance remains pending; this evidence does not prove that every historical dialog had the same cause.

Post-fix verification: **830 passed, 8 deselected** in the default suite, run with a process-local Windows error mode to prevent test crash dialogs. This does not alter application error handling. Syntax, focused Ruff, internal-language, documentation and diff checks passed. A first temporary test launcher lacked a multiprocessing main guard and caused recursive test execution and timing failures; the guarded launcher produced the result above. The launcher and synthetic stderr captures remain ignored local artifacts.

## Implemented scope

### Native visibility and separate entry correction

The user clarified that the console client must retain its original entry and default behavior, with the GUI launched separately. `start_client.py` again defaults to console microphone mode; `start_capswriter.ps1` defaults to two terminals. GUI source launch uses dedicated `start_desktop.ps1` / `start_desktop.pyw`, and EXE script filtering separates `CapsWriter.exe` from `start_client.exe`.

The user then reported a running tray whose Open Main Window action did nothing. Process inspection confirmed the GUI and hidden client were running. The GUI window had a valid on-screen rectangle but native visibility was false. A synthetic native Windows probe reproduced `Qt.isVisible() == True` alongside `IsWindowVisible() == False` after launch with `STARTF_USESHOWWINDOW/SW_HIDE`; the old showNormal/raise/activate sequence left it hidden. This matches [Windows startup show-state semantics](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow). Resetting hide/show restored native visibility.

GUI launch now uses normal window startup; windowed Python avoids a console without hiding the GUI. The shared reveal path verifies native visibility and resets inconsistent Qt/native state when needed. Two isolated Windows regressions exercise the actual tray QAction, initial visible/hidden preferences, repeated reopening, minimized restoration and maximized preservation. They assert native window visibility, unlike earlier offscreen-only checks. The existing user GUI window was also restored after checking its exact process, class and title; neither client nor server was restarted. Full physical multi-monitor/accessibility and complete packaged behavior remain pending.

Post-correction checks: **839 passed, 10 deselected** in the default suite; **2 native visibility tests passed** separately. Syntax (including both build specs), Ruff, internal-language, documentation and diff checks passed. Both source launchers were exercised with `-WhatIf`: the original helper selects server/client terminals, and the dedicated desktop helper selects only the windowed GUI. No real audio or provider request was initiated for verification.

The main overview displays dictation readiness, recording/processing/pause state, ASR connection, text processing preferences and configured shortcuts. A styled sidebar leads to five settings pages for common dictation settings, correction modules/strength and presets, providers and client ASR connection, independent output/record controls, and runtime status/local diagnostics/monthly costs. Entry points share settings validation, revision conflicts and safe publication. Provider/preset editing preserves unrelated TOML and credentials. Final-prompt inspection includes unsaved edits without model calls or caret reads.

The desktop owns one hidden client with bounded JSON pipes and Windows job ownership. It automatically connects through the existing client, without starting or managing the ASR server. Closing hides to the available tray; explicit Quit settles the client, and no-tray closure follows Quit. A saved preference starts directly in the tray, while failure reveals the window once. Reopening the same desktop entry activates its existing window. Legacy console mode retains an owned settings-only child and standalone editing remains available. Tk shutdown includes stop, owner-thread overlay/reference cleanup, quit/destroy and bounded join, including pre-readiness and late-submission cases.

## Unified source launcher follow-up

At the user's subsequent request, `start.ps1` replaces both PowerShell helpers described in the earlier correction above. It requires `-Server` and/or `-Client Gui|Console`; no target or `-Help` displays help before Conda discovery. The Python console and GUI entries remain distinct. `-WhatIf` validates and previews selected launches. All selected entries are checked before any process starts; GUI launch retains the normal native show state. Static English/Simplified Chinese launcher resources follow system UI culture without executing local configuration.

Verification: **21 isolated Windows PowerShell launcher tests passed** on Python 3.11.15. A mocked process boundary covers five target combinations, no-target/help behavior without an environment in both locales, invalid selections, previews, spaces/apostrophes in paths, preflight failure before server launch and parent PATH restoration after launch failure. The actual checkout also passed no-argument help and combined server/GUI `-WhatIf`. Ruff, Python syntax, documentation, internal-language and diff checks passed. This follow-up did not launch an application or repeat audio/GUI lifecycle tests; those implementations were unchanged. User acceptance remains pending.

## Interaction refinement, 2026-09-28

The user preferred the original microphone/status icon, reported undersized settings text, accidental numeric edits while scrolling, an unnecessary reload button and continuously flashing footer controls. The flashing came from unconditional disable/re-enable around every background read.

The desktop now reuses the console microphone asset, with recording badges on the overview, window and tray. Lightweight serialized runtime reads run at 200 ms intervals when idle, without reading settings or catalog files; settings/catalog checks remain every two seconds. The base font is explicitly 16 logical pixels throughout child controls (changing only the window font did not propagate through the stylesheet); small state labels are 14. Numeric and closed choice controls ignore wheel events even when focused, leaving page scrolling and keyboard/direct editing intact.

The permanent reload control is removed. External revisions automatically refresh clean forms; dirty forms retain their content and old revisions, rejecting silent overwrites. A contextual file-version action appears only on conflict and confirms draft discard. Background reads preserve enabled states, and one pending foreground action takes priority over subsequent polling. Standalone closure waits for an already queued save as well as an active save. Existing desktop exit ownership is unchanged.

Validation: **873 passed, 10 deselected** in the default suite; **2 native Windows visibility tests passed** separately. New synthetic checks cover wheel propagation for integer, decimal and choice controls with/without focus, keyboard editing, footer enabled-state events, saving during a blocked background read, closing with a queued save, clean-versus-dirty external client/catalog revisions, selector preservation, recording-badge transitions/disconnection, font propagation, content-free status reads and status-failure ownership. Syntax, Ruff, configured mypy, internal-language, documentation and diff checks passed. English and Chinese renders at 1120x860 and 800x600 were inspected. No microphone, real client/server, provider request or user data was used. User acceptance and physical mixed-DPI/focus checks remain pending.

## Chinese font feedback, 2026-09-28

After accepting the preceding interaction fixes, the user reported softer Chinese text and allowed reverting the font size if necessary. A synthetic Windows Qt probe at the current 150% display scale found that the earlier stylesheet change affected both size and family: the original controls used Segoe UI with SimSun/MS UI Gothic glyph fallbacks, while the enlarged stylesheet forced Microsoft YaHei UI for all characters. QFontInfo and QTextLayout glyph runs confirmed these selections; this is evidence of a rendering change, not proof of the user's perceptual cause. Qt's [font matching documentation](https://doc.qt.io/qt-6/qfont.html#details) describes system matching and missing-glyph fallback.

Removed the forced family and selected the system general UI font, retaining 16-pixel body controls and the existing heading/state sizes. This restores the earlier font-selection behavior without undoing the readability increase or changing Windows font/DPI settings. Validation: 55 focused settings tests passed; Ruff, syntax, documentation, internal-language and diff checks passed. Synthetic Chinese Windows-backend layouts were rendered without displaying windows or starting a client, server or microphone and inspected at the current scale. Subjective clarity and physical monitor comparisons remain for user review; the previous 873-test run predates this font-only adjustment and was not repeated.

## Compact-size restoration, 2026-09-28

The user's subsequent screenshot review explicitly rejected retaining the enlarged sizes and deferred visual refinement in favor of application logic and user expectations. Restored the pre-enlargement scale: body controls 12 logical pixels, small labels 11 and card titles 15. System font selection and all accepted recording-icon, scroll, save, polling and conflict-handling fixes remain intact. Validation: 55 focused GUI tests passed; syntax, focused Ruff, documentation, internal-language and diff checks passed. No new visual variants, real application launches or user configuration changes were made. The earlier font experiments above are historical evidence, not the current size policy.

## Recognition history and button feedback, 2026-09-28

At the user's request, added an independent recognition-history workspace over the existing effective transcript directory. First navigation queries the current month; subsequent explicit queries filter month/keyword and page newest-first results. Selection reads a bounded, digest-checked record into a plain-text widget. Copy writes only on explicit activation, and opening the day file uses Notepad owned by the GUI, with the effective archive path resolved by the attached worker. No new collection, format migration, background history polling or permission changes are introduced. Large/inaccessible archives and stale details have explicit feedback; Markdown timestamp-heading ambiguity and nontransactional pagination are documented in the design.

The missing button feedback came from ID-specific subtle/sidebar base styles overriding generic hover styling. Explicit hover, pressed and focus rules now cover those controls, and GUI buttons use a pointing-hand cursor. Chinese fonts remain deferred as requested.

Validation: **902 passed, 10 deselected** in the default suite; **2 native Windows visibility tests passed**. New synthetic checks cover month/date validation, leap days, Unicode/case-insensitive search, newest-first pagination, BOM/CRLF, old unstructured notes, bounded reads/details, external changes/deletion, directory containment, saving-off access, custom/effective directories, literal markup, explicit clipboard/file actions and hover-image changes with keyboard focus. Final list-preview/layout and shutdown-navigation refinements received focused history/GUI checks. Syntax, Ruff, configured mypy, documentation, internal-language and diff checks passed. English/Chinese Windows-backend renders at 1120x860 and 800x600 were inspected using synthetic archives without visible application windows. No real user history, live client/server/microphone, provider request or clipboard read was used; copy/file-opening tests intercepted those actions. User acceptance and physical mixed-DPI interaction remain pending.

### Search and detail revision after user review

Reproduced the reported Next stall with 45 synthetic records: after selecting one record, clearing the list emitted a selection change, dispatched `history_read`, and silently dropped `history_query`. The new regression initially failed with only `history_read` dispatched and the page unchanged. Rebuilds now block selection signals, the request method returns acceptance, and history uses its own loading text. Next, Previous, search after selection, queued search during quiet polling, rejected requests and invalid-range recovery are covered.

Replace month text entry with calendar range controls and all-dates/today/last-seven-days/current-month/custom choices. Keyword and date filters work independently or together, Clear filters searches all dates, and summaries include the active query and page totals. Details show saved recognition/input/output/final stages, actual saved prompt/reference and a separate raw view. Saved request IDs join bounded, read-only local cost records with explicit source, amount, currency, tokens, model and duration; missing or corrupt cost data leaves transcript access intact. Exit client now matches sidebar alignment and has an outlined action style; remove the local-settings footer sentence.

Validation: **921 passed, 10 deselected** in the default suite and **2 native Windows visibility tests passed**. After the final calendar styling and detail-scroll reset, focused history/GUI checks passed again: **103 passed**. The project interpreter was Python **3.11.15** in the existing `capswriter` environment. Synthetic checks cover combined/cross-month/keyword-only ranges, real calendar popup activation, clear/reset, bounds/containment, structured writer output, missing fields, deduplicated output, explicit final-text copying, neighboring-month cost lookup, read-only database bytes, provenance, absent/corrupt/oversized costs and no private metadata in detail payloads. Syntax, Ruff, configured mypy, documentation, internal-language and diff checks passed. Windows-backend synthetic renders cover normal and compact English/Chinese layouts plus request, cost and calendar views. No real archives, client/server/microphone or provider traffic were used. User acceptance and physical mixed-DPI/focus/accessibility checks remain pending; no commit has been made for this revision.

### Automatic filters and stable history controls

Further user feedback identified whole-page enabled/disabled transitions and clear-before-load behavior as visible flicker. History requests now leave controls enabled, retain displayed content while reading, preserve unchanged selection on refresh and replace content only when a current response arrives. A reserved progress row avoids transient layout movement. The empty-detail instruction is centered with muted styling.

Date presets apply immediately; date/keyword edits debounce for 300 ms, with Enter/Refresh for immediate execution. Add a bounded page input with Enter/Go. A single pending latest intent and generation checks prevent rapid selection/filter changes from applying obsolete data or errors. Pending history work stops on actual close/exit. Existing settings-write guards and serialized backend ownership remain.

Validation: **928 passed, 10 deselected** in the default suite; **2 native Windows visibility tests passed**. Added regressions exercise automatic today/week/custom/keyword filters, page entry and bounds, query shrink, unchanged enabled/visible control states and retained detail during delayed refresh, superseded request errors, rapid detail selection and shutdown dropping deferred reads. Syntax, Ruff, configured mypy, documentation, internal-language and diff checks passed under the existing Python **3.11.15** environment. Synthetic English/Chinese empty and populated history views were inspected at normal and compact sizes. No real history, clipboard contents, microphone, client/server or provider calls were used. Physical mixed-DPI/focus/accessibility and user acceptance remain pending; no commit was made.

### Source acceptance and startup-state investigation

The user accepted the GUI's functionality and authorized committing the baseline rather than waiting for every visual refinement. The same message reported recording enabled at startup. Source inspection shows `ClientState.recording=False`, microphone preparation followed by shortcut listener startup, and an audio callback that discards idle samples before they reach a capture session. This behavior is shared by GUI and console startup; opening the stream can still produce Windows microphone-use indication.

Added mock startup regressions for both entry modes, strengthened idle audio-discard coverage, and checked GUI waiting/recording/processing transitions and badge state. Clarified the ready headline to Waiting for shortcut and gave recording/processing their own instructions. These tests do not reproduce or resolve an unexplained real Recording/red-dot state at startup. Requested clarification of the observed indication; microphone lifecycle and live user processes remain unchanged pending that evidence.

Commit-time validation: **931 passed, 10 deselected** in the default suite; targeted startup/audio/GUI checks passed **100 tests**. Syntax, Ruff, documentation and internal-language checks passed. The immediately preceding native Windows check passed **2 tests**; no native-window ownership code changed in this final wording/test revision. Complete release packaging, physical mixed-DPI checks and the unclarified startup indication remain outside this acceptance claim.

## Verification

- Desktop suite before the native-visibility correction: **839 passed, 8 deselected**, configured-module coverage **85.22%**. Syntax, Ruff, configured mypy, internal-language, documentation and diff checks passed. The dropdown appearance adjustment was followed by the focused GUI suite. Coverage uses the absolute `pyproject.toml` path and explicit branch mode so synthetic subprocess working directories cannot change the measurement configuration; an earlier attempt without this failed coverage combination and is not counted as successful verification.
- Environment: existing `capswriter`, Python 3.11.15; PySide6-Essentials 6.8.3 and TOML Kit 0.13.3.
- Earlier settings-only default suite with coverage: **818 passed, 8 deselected**, configured-module coverage **85.22%**. Syntax, Ruff, configured mypy, internal-language, documentation and diff checks passed at that stage. This coverage percentage describes the existing configured critical modules, not GUI coverage or subsequent desktop changes.
- Synthetic tests cover first provider creation, comments/unknown fields/credential preservation, clear-key intent, invalid/referenced entries, external edits, atomic replacement failure, draft preview, bounded diagnostics, malformed/oversized/EOF pipes, standalone dispatch, attached saved/pending/restart state, process reuse/reaping, keyboard navigation, draft retention and accessibility names.
- Desktop regressions cover automatic startup, unavailable-client fallback, retry, startup visibility with and without a tray, repeat activation, hide versus Quit, one-time failure reveal and retry after shutdown failure. Five isolated worker paths (normal shutdown, startup failure, independent return, parent pipe EOF and shutdown during initialization) exited zero with empty captured stderr. An end-to-end offscreen `pythonw` probe used the real desktop/backend/worker with a synthetic client and confirmed both processes exited zero. No audio or service process was started by these tests.
- Modern Chinese and English layouts were inspected at 1120×860 and 800×600 logical pixels; settings scroll while save/reload remain visible. Initial real-window dimensions are constrained by the available screen area, subject to the 800×600 minimum.
- Offscreen renders at scale factors 1, 1.5 and 2 used synthetic public-template fixtures. Screenshots were inspected; system fonts were explicitly loaded for the offscreen platform. This is a rendering check, not a physical monitor test.
- A minimal PyInstaller package importing the complete settings window/backend built and ran successfully against synthetic files. Its import guard rejected root executable config, Tk, audio and client/server startup. Initial unactivated Conda packaging missed native DLLs; rebuilding with the existing environment's `Library/bin` on PATH fixed collection. No full product packaging, model download or release upload was performed.
- No live microphone, global shortcut simulation, provider request, caret/clipboard read or user-content export was performed. Ignored local settings and credentials were not edited.

## Remaining acceptance

1. Start the source desktop after exiting the old console client; confirm automatic connection, visible offline guidance, startup-in-tray, single-instance activation and correct saved/effective status during a real task. Closing with a tray must leave dictation running; explicit Quit must release it without stopping the independent server.
2. Compare correction module/strength choices on user-selected dictation, separately from these configuration tests. Verify expected LLM fallback messages on real provider errors when desired.
3. Check primary/secondary monitors, mixed DPI movement, constrained screen height, keyboard Tab/Shift+Tab traversal, focus return after tray activation, and screen-reader behavior. Synthetic keyboard/scale checks are narrower evidence.
4. Exit while recording/status hints are present and while settings are open; check no orphan owned child or late Tk access. Test packaged application behavior on a clean Windows machine before release.

Complex shortcut maps, server model selection, app-specific output rules and rate-table editing retain advanced-file access in this increment. Complete product packaging, physical desktop checks and renaming remain separate work. The focused frozen settings smoke predates the new desktop supervisor and does not verify the complete packaged desktop. See the [ownership design](../development/settings-gui.md) and [active backlog](../../TODO.md).
