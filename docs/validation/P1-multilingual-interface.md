# P1 multilingual interface — validation and acceptance

Status: accepted by the user on 2026-09-22 after the localization and local
server language synchronization follow-ups. The user authorized committing the whole item.

## Scope

English and Simplified Chinese resources, a persisted local UI preference,
client tray selection, safe configuration reload, dynamic menu labels/tooltips,
recording and processing status, shared dialog buttons, dictation failures,
file progress/results/errors, subtitle rebuild, controlled LLM errors, CLI help
and parser errors, configuration/protocol validation, maintained engine notices,
standalone utility labels, and localized console diagnostics with English file
archives. Live server language changes reach ASR and aligner workers without a
restart. ASR language, shortcuts, LLM translation targets and
user content retain their existing semantics.

## Automated validation

Environment: existing Conda `capswriter`, Python 3.11.15.

- Default suite: **533 passed, 8 deselected**; configured coverage **85.22%**
  (required threshold: 50%). This is coverage of the configured core modules,
  not the whole repository or UI.
- Windows native menu tests: **8 passed**, including both locales at 20/30/40-pixel
  icon sizes. No microphone, visible menu interaction or user text capture.
- Ruff, configured mypy targets (7 source files), compileall including existing
  local configurations, CLI help and `git diff --check`: passed.
- Catalog keys/placeholders and literal IDs, language persistence and atomic-save
  failure, active-task reload boundaries, actual pystray callback adaptation,
  shared exclusion for language/LLM settings writes, long-hint wrapping, controlled
  LLM errors and original-content preservation: covered by regressions.

The implementation preserves the pre-existing TODO edit. Existing local
configurations receive only the new `ui_language = 'auto'` field when absent;
their original values and credentials are not replaced or committed.

Pytest uses a fresh `.cache/i18n-*` base directory and disables its cache provider
because the existing system temporary/cache directories reject access in the
agent shell. No permission changes or deletion of existing directories is needed.

## Manual feedback follow-up

On 2026-09-22, the user reported untranslated recording and server-ready terminal
labels while English was selected. The first extraction missed Rich `Status` and
`console.rule` entry points. The follow-up also found receiving-audio and
module-loading status labels.

- `core/tools/my_status.py` now resolves an optional message ID on each start.
  The shortcut recording status and module-level server receiving status reuse
  this mechanism, including after a language reload.
- `core/server/worker/process_manager.py`, `model_loader.py`, `core/server/app.py`
  and `core/server/connection/ws_recv.py` use localized product status resources.
  Spawned workers use the server's effective shared locale for their status,
  without loading credentials or changing ASR language.
- `tests/unit/test_terminal_localization.py` adds seven regressions covering
  reused status rendering, model-loader configuration, server-ready output,
  configuration transition history and a scoped scan of direct product console
  labels. The expanded hardcoded-text request adds
  `tests/unit/test_localization_coverage.py`: its guards also cover diagnostic
  calls, standalone tools and maintained inference wrappers, in both languages.
  It verifies English file records, localized console copies, nested reasons,
  parser errors, catalog duplicate IDs and actual spawned-worker language changes.
  The latest full validation results are recorded above.

The reported configuration-notice sequence matches switching English to Chinese
and back. Pending notices use the effective old language; applied notices use the
new language. Previously printed console lines remain in their original language.
The user accepted the overall result and authorized commit on 2026-09-22.
This acceptance does not establish that every detailed manual case below was run;
only the automated checks and observations explicitly recorded here are verified.

The server-following follow-up confirmed that the earlier independent preferences
prevented client menu changes from reaching the server. The server now reads the
local saved client preference before creating workers and uses a second validated
configuration watcher to update only that detached setting. Twelve regressions
cover menu-save propagation, startup/auto/legacy fallback, invalid candidates and
recovery, isolation of unrelated settings, and watcher shutdown. Full validation:
533 default tests passed, configured coverage 85.22%; Ruff, mypy, compileall and
diff checks passed. Native-menu code is unchanged from its eight passing checks.

## Manual acceptance

Run the updated source once. Repeat the main flows in both **English** and
**简体中文**. No credentials need to be shared with the agent.

1. Select **Settings > Interface language** in the client tray. Wait for idle,
   reopen the menu, and check every submenu, state label, checkmark and tooltip.
   Restart the client and confirm the choice persists. Check `auto` once too.
2. Record a short dictation using the existing shortcuts. Check recording,
   transcribing and LLM stages, then pause/resume and copy the latest/original
   result. The text, shortcut behavior and ASR language must be unchanged.
3. Change the UI language while a recording or LLM task is active. The active
   task keeps its language until its work settles; the next task uses the new
   language. Check that the previous task cannot clear a newer task's status.
4. Run file transcription on a small known media file and one invalid media
   file. Check progress, success summary, readable failure/action advice and
   output files. Try `start_client.py transcribe --help` and subtitle rebuild
   with an existing test TXT/JSON pair. Commands and format options stay the same.
5. With the user's normal LLM setup, check correction/translation and cancellation.
   A failure must show a localized controlled reason and retain the original
   transcription. Do not alter credentials just to manufacture an error; automated
   tests already cover missing keys, quotas, timeouts and sanitized failures.
6. Switch the client language menu in each direction and wait one to two seconds.
   The server in the same installation must report that it follows the saved
   preference, and subsequent console/tray/worker notices must use that locale.
   Restart the server and verify its initial banner follows the saved client
   preference. Recognition settings and `config_server.py` must remain unchanged.
   Native backend/OS details remain verbatim; existing terminal history is not
   rewritten. Remote clients in separate installations do not change this locale.
7. On the primary and secondary display, including different DPI scales, check
   menus, tooltips and long status hints for clipping, overlap and unexpected
   focus changes. Check console hide/show, restart and quit as normal.

Optional persistence failure check: use a disposable configuration in a separate
test copy. A failed save or invalid live `ui_language` must retain the last valid
runtime language and leave other settings untouched.

For the hardcoded-text follow-up, also inspect missing CLI argument values,
unknown options, conflicting options, configuration rejection reasons, file
receive completion, Ctrl+C hints and microphone/device warnings in both locales.
Do not induce hardware failures solely for testing. Automated checks cover these
controlled messages, both logging-handler orders, nested validation notices,
English file diagnostics, catalog completeness and live spawned-worker language.

## Limits

Automated native menu checks exercise Windows labels, Unicode, bitmap sizes and
tooltip mappings; they do not certify interactive mixed-DPI behavior. No real
microphone, model inference, provider request, GPU monitoring command or complete
PyInstaller build is needed for the automated validation. Desktop acceptance and
release packaging remain separate checks.
