# Shared settings and application operations

Implemented and accepted by the user on 2026-09-27, with commit authorized. This is item 2
of the desktop workflow sequence, not implementation of the settings GUI.

## Delivered scope

- Shared static whole-candidate validation and revision-checked read/preview/save
  for the main client/server settings sections.
- Detached saved/effective values, pending/restart fields and controlled failures;
  invalid files preserve the last effective application state.
- Atomic source-preserving saves, cooperative file locks, stale-editor rejection,
  and revocation of old/in-flight reload candidates without file I/O under the
  publication lock.
- Client async operations for settings, language/LLM toggles, pause and reconnect;
  tray adapters retain UI feedback and delegate publication to existing boundaries.
- File-only `settings show/check/set` CLI with credential redaction, stdin JSON
  support and no imports of local executable configuration, hardware or UI.

The authoritative API and limits are in the
[configuration reference](../reference/configuration.md#shared-settings-interface).
Provider/preset TOML structured editing belongs to the following work; its loader,
request snapshots and files are preserved. No protocol or worker ownership change
is included. Source startup of custom executable configuration is preserved;
structured editing requires declarative configuration, as live reload does.

## Automated evidence

Environment: existing `capswriter` Conda environment, Python 3.11.15 on Windows.
Only synthetic settings, mock owners and providers were used. No microphone input,
global key simulation, network LLM requests, model loading or packaging was run.

Coverage includes:

- Complete candidate validation, legacy defaults, bad values in unchanged fields,
  unknown fields, duplicate classes, invalid/executable files and safe errors.
- Comment/BOM/CRLF and credential-expression preservation, guarded compound-value
  edits that would lose internal comments, failed atomic saves,
  external edits before/during save, and two simultaneous editors sharing a revision.
- Preview without write, detached snapshots, live versus restart classification,
  active client task deferral and unchanged server task snapshots.
- Pending/in-flight candidate revocation, shutdown rejection, rapid toggle and
  language changes using saved state, and existing resource-owner delegation.
- Actual pystray callback adaptation with mock application owners, controlled
  action failures and effective menu checkmarks.
- CLI client/server read/check/save/conflict and malformed JSON behavior, credential
  redaction, stdin input, and subprocess import guards for local configuration,
  audio, Tk and application startup.

An initial default run passed 756 tests (8 deselected). A later coverage run with
four additional boundary tests passed 759 and failed the existing
`test_service_terminal_failures_and_fallbacks[cancel]`: its 30 ms timeout fired
before cancellation and recorded `request_timeout` instead of `cancelled`.
The test passed in isolation under coverage afterward. This timing-dependent test
is recorded separately in TODO; its implementation is unchanged here. Configured
module coverage in the full run was 85.22% (50% required).

Final verification: 761 default tests passed (8 deselected), including the added
compound-comment regression. Before that final regression, the complete coverage
rerun passed all 760 tests with 85.22% configured module coverage. Syntax checks,
Ruff, configured mypy targets, internal-language/documentation checks and
`git diff --check` passed. The earlier cancellation-test failure remains recorded
above rather than being treated as resolved by a passing rerun.

## Manual scope and acceptance

Still to verify through user interaction after restarting the updated client:

1. Change language and LLM switches from the tray while idle and during a real
   dictation task; confirm saved feedback, task-boundary application and checkmarks.
2. Exercise real microphone reconnect and pause/resume; the automated checks use
   mock resource owners and do not certify device/focus/DPI behavior.
3. When the GUI is implemented, verify editor conflict presentation and keyboard,
   focus, mixed-DPI and shutdown behavior. No GUI framework was selected here.
4. Validate the settings CLI in frozen packages during the packaging workstream.

No local configuration, credentials or existing user data was edited. The user
accepted this implementation and authorized committing it before starting item 3.
Acceptance does not replace the remaining manual checks above.
