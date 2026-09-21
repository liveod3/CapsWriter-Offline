# P0 stage 3b — Recognition task error outcomes

Status: accepted by the user after manual testing; no specific outstanding issue
was reported. Commit this stage separately before the next implementation stage.
Stage 3a was accepted and committed as `1460e30`. This stage resumes the uncommitted
task-error work found in the workspace on 2026-09-21.

## Behavior and scope

A caught inference/pipeline exception previously left the client waiting for a
result that would never arrive. The worker now emits one final `recognition_failed`
result with empty text, tokens and timestamps. It removes the owning session and
buffered fragments, and discards later input for that `(socket_id, task_id)`.
Other tasks, including another task on the same connection and the same task ID
on another connection, retain their own state and can continue.

The main process removes the failed task's receive cache and suppresses duplicate
errors and late results. Each process remembers at most 64 failed task IDs per
connection. Reaching that limit blocks that connection's tasks and closes the
connection after the last error; disconnect releases the history. This limit is
a failure budget, not a limit on successful tasks or an expiring retry window.
Retrying a failed task requires a new task ID, as normal clients already generate.

New audio messages advertise `supports_task_errors=true`; missing capability
defaults to false. Success messages still default to `error_code=''`. An error
requires a valid task ID, `is_final=true`, finite nonnegative timing fields and
empty recognition content. A legacy client is closed with code 1011 instead of
receiving an empty success. Updated clients still accept ordinary legacy-server
successes; an old server cannot provide these new error outcomes.

Task-error sends have a five-second deadline. A failed or timed-out send falls
back to closing the connection; close also has a five-second deadline and aborts
the transport on failure. This self-review correction prevents an undeliverable
error from indefinitely blocking this sender. It does not bound ordinary success
sends, inference itself or a permanently full/broken multiprocessing queue.

The microphone client cancels only the matching active/draining recorder, clears
its context/audio association and progress, and presents a retry hint. Duplicate
and unrelated errors produce no extra failure notification. A newer recording's
ownership and indicator are preserved. Failed tasks do not invoke the LLM, inject
text, replace the last successful output or write a successful transcript record.
The file client reports a readable failure immediately, cancels and joins upload,
reaps its decoder, closes its connection and allows the batch to continue. It
does not save a partial transcription as a completed output file.

Explicit cancel messages, microphone deadlines, worker startup/death/hangs,
ASR/aligner supervision, general queue/send failure policy and shutdown ownership
remain under A03. Existing routine recognition-content logging remains under A04;
only the newly handled pipeline-error diagnostic is made content-free here.
No configuration fields or local configuration values were changed.

## Files

- `core/protocol.py`, `core/server/schema.py`: compatible capability/error fields and error validation.
- `core/server/task_failures.py` (new), `core/server/state.py`: bounded failure history and main-process audio-cache ownership.
- `core/server/worker/task_handler.py`, `pipeline.py`, `core/server/connection/ws_recv.py`, `ws_send.py`: error creation, task cleanup, tail suppression, routing and transport fallback.
- `core/client/state.py`, `audio/recorder.py`, `shortcut/task.py`, `output/result_processor.py`: recorder lookup by task ID, capability advertisement and owned error cleanup.
- `core/client/transcribe/file_transcriber.py`, `feedback.py`: file failure handling and presentation.
- `tests/unit/test_task_error_outcomes.py` (new), `test_dictation_pipeline.py`, `test_file_task_lifecycle.py`: protocol, worker, routing and client regressions.
- `tests/manual/task_error_server.py` (new): optional loopback-only failure stub for reviewing actual client feedback without changing models or configuration.
- `TODO.md`, `docs/CHANGELOG.md`, this record: scope and pending acceptance.

The pre-existing caret-context TODO entry is preserved. The existing invalid-media
test expectation was already changed in the interrupted work to match its current
action hint; that correction is retained.

## Automated validation

Existing `capswriter` environment, Python **3.11.15**. Default suite with the
configured coverage gate: **320 passed, 2 deselected** (37 more than stage 3a).
Coverage is **81.85%** for the configured subset, not the whole application.
Ruff, the seven selected mypy files, compileall including existing local
configurations, and `git diff --check` pass.

Coverage includes intermediate/final pipeline failures, pickle/wire round trips,
legacy defaults, malformed/content-bearing errors, same-connection peer tasks,
cross-connection ID collisions, repeated/late errors, cache cleanup, bounded
failure history, send/close exceptions and timeouts, matching recorder cancellation
and file-upload cancellation with decoder cleanup. All inputs are synthetic.

The first default test run encountered filesystem permission errors in pytest's
existing temporary directory (244 passed, 68 setup errors). Re-running with a
new unique directory under ignored `.cache/` and `-p no:cacheprovider` passed.
No existing temporary directory was removed or permission settings changed.
No real microphone, key injection, GPU/model inference, cloud LLM or packaging
was exercised. The manual stub is not run as part of the default suite.

## Manual acceptance

Restart both the source server and source client to load this stage. Use the
existing `capswriter` environment; activating it and launching `python` directly
avoids the outer `conda run` batch-shell Ctrl+C prompt.

1. **Normal regression:** perform a short and a longer dictation, then two quick
   successive dictations. Transcribe a known-good file. Check text, enabled output
   formats, indicators and the next recording after completion.
2. **Controlled error presentation (optional stub):** stop the normal server from
   its own exit control. For a client already configured for plaintext loopback,
   run the following in a separate terminal at the repository root. Use the
   client's existing port if it differs from 6016; do not change configuration to
   make a LAN/TLS setup fit this stub.

   ```powershell
   conda activate capswriter
   python tests/manual/task_error_server.py --port 6016
   ```

   Wait for the microphone client to reconnect. Start dictation and speak beyond
   its minimum recording threshold. The stub deliberately fails on the first
   audio message. Expect one recognition-failure notification, stopped recording
   and progress, no inserted text and an unchanged last successful output. Try
   another recording and verify that it can start and fail independently.

   With the stub still running, submit two valid test media files in one batch.
   Both should report recognition failure and the batch should finish with two
   failures, without new TXT/SRT/JSON output or lingering decoder processes.
   The stub does not save received audio; ordinary client recording/log settings
   still apply. Its output contains only source type and a short task ID.
3. **Recovery:** stop the stub with Ctrl+C, restart the normal server, wait for the
   client to reconnect, and confirm dictation and a fresh file run succeed again.

The stub checks real client presentation and cleanup, not real inference errors.
Automated tests cover the actual server handler's injected pipeline failures and
isolation. There is no need to corrupt models or force real GPU failures. Record
the user's observed results here before committing.

The user reported that testing was complete and there were basically no issues.
Acceptance covers the reported local checks; individual checklist results and
hardware/model combinations were not enumerated.
