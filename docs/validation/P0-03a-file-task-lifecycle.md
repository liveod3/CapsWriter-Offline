# P0 stage 3a — File-task deadlines and cleanup

Status: accepted by the user, including the terminal failure presentation follow-up. Commit this batch separately before the next implementation stage. Stage 2b was accepted and committed as `0706830`.

User feedback confirmed that a batch completed its two valid media files around an invalid renamed input. The failure display interleaved raw diagnostics with progress, including an empty error for the receiver canceled after sender failure. The follow-up replaces this with a reason, an action hint and a next-file status, removes the failed progress line and leaves diagnostics in enabled file sinks. Expected child cancellation is no longer logged as an error. Unhandled warnings still reach the console.

## Problem and resulting behavior

File transcription previously had unbounded waits for FFmpeg output, send-window capacity and recognition results. Canceling the parent while it awaited the sender/receiver pair did not consistently cancel and join both children. Several decoder failure paths terminated FFmpeg without waiting for it, while ffprobe cancellation could abandon a child process.

Each file task now owns both async child tasks through a shielded cleanup operation. Failure cancels the remaining work; parent cancellation waits for both tasks and connection cleanup. Application shutdown joins the runner before resetting shared state. A failed file is counted as failed and the batch can move on only after cleanup; cancellation stops the batch.

Task IDs are allocated before either child starts. Only matching recognition results can release the send window or complete the file. Advancing processed duration refreshes the result deadline; foreign or duplicate progress does not keep a stalled task alive. A final result is saved only after the sender confirms successful upload, preventing output from a final message received while sending fails.

FFmpeg and ffprobe creation retain ownership if cancellation arrives while a child is being created. Cleanup terminates a running child and drains its output pipes, including after process exit when buffered output can otherwise block `wait()`. After two seconds it escalates to kill, allows three more seconds to drain, and bounds the final wait to three seconds. Repeated caller cancellation cannot skip this cleanup. A real synthetic Python-child test covers buffered stdout; actual FFmpeg and model behavior remain for manual acceptance.

## Configuration

The tracked client template and the existing local client configuration gained only these fields; other user values were preserved. Old configurations use the same defaults through `getattr`, and invalid/nonpositive/nonfinite values fall back safely.

| Setting | Default | Meaning |
| --- | --- | --- |
| `file_io_timeout` | 60 seconds | Per-operation deadline for connection, reading one decoded chunk, sending a message and waiting for decoder completion. |
| `file_result_timeout` | 600 seconds | Maximum interval without advancing matching recognition progress; also bounds waiting for send-window capacity. Increase for slow hardware or heavily concurrent workloads. |

The result deadline is not a limit on total file duration. ffprobe duration lookup has an internal 15-second limit; a normal probe failure/timeout falls back to unknown duration after cleanup. File connection close has a five-second deadline and aborts the captured transport if graceful close fails. Settings load at client startup; configuration hot reload remains a separate P1 TODO.

OS-level process creation or an OS refusal to terminate a child cannot be made interruptible by these Python deadlines. Output persistence retains its existing behavior and is not made transactional by this stage. There is no new wire protocol: cancellation closes this file client's connection, leaving the existing server disconnect cleanup in charge of its queued work. Already-running inference may finish later. Server process hangs, explicit task error/cancel responses, microphone deadlines and permanent server queue/send failures remain under A03.

## Changed files

- `core/client/transcribe/lifecycle.py` (new): compatible timeout parsing and owned subprocess cleanup.
- `core/client/transcribe/file_transcriber.py`, `media_tool.py`: deadlines, result ownership, upload completion and FFmpeg/ffprobe cleanup.
- `core/client/transcribe/feedback.py` (new), `core/logger.py`: stable failure IDs and readable labels; suppress only diagnostics already covered by task feedback from the console handler, preserving file sinks.
- `core/client/manager/file_runner.py`, `core/client/app.py`: join child tasks and runner cleanup before moving on/resetting state.
- `config_templates/config_client_template.py`: public timeout defaults. The ignored local `config_client.py` received the same missing fields and will not be committed.
- `tests/unit/test_file_task_lifecycle.py` (new), `test_file_runner.py`, `test_websocket_shutdown.py`: new lifecycle regressions and adapted preflight cleanup fixture.
- `TODO.md`, `docs/CHANGELOG.md`: pending acceptance. The pre-existing caret-context TODO change remains separate and untouched.

## Automated validation

Existing `capswriter` environment, Python 3.11.15. Default suite and configured coverage gate: **283 passed, 2 deselected**, including 31 additional regressions. Coverage is **73.73%** for the configured server/protocol subset; it does not measure this client's new lifecycle code. Ruff, the seven selected mypy files, compileall (including existing local configurations) and `git diff --check` passed. Feedback tests also verify one readable invalid-media failure, preserved technical diagnostics, visible unhandled warnings and removal of failed progress while retaining successful progress.

Tests cover successful completion, sender failure, decoder failure, decode/send/window/result deadlines, disconnect, repeated parent cancellation, batch continuation after cleanup, late process creation, stubborn child termination, ffprobe cancellation/timeout, transport cleanup and stale results. The synthetic Python subprocess writes constant bytes; no microphone, private media, keyboard injection, network LLM, GPU or real model was used. Real FFmpeg/media interaction and packaging were not tested automatically.

## Manual acceptance

Use a newly started source file-transcription client so it loads this batch. The server can stay on the accepted stage 2b code, since the protocol is unchanged. Keep ordinary microphone dictation in its separate client if desired.

1. **Normal and batch success:** transcribe a short and a longer known-good media file, including both in one batch. Verify the expected output files and summary, then confirm ordinary dictation still works. Long-running transcription with steady progress should not time out just because its total elapsed time exceeds ten minutes.
2. **Invalid media followed by valid media:** create a disposable text file containing a short synthetic string and rename its extension to `.wav`, without modifying a real recording. Submit that file before a known-good media file in one batch. The invalid file should be counted as failed; the next file should still complete without a stuck progress display or a leftover decoder. The existing CLI supports `python start_client.py transcribe --format txt "invalid.wav" "valid.wav"` using the project interpreter and actual test paths.
3. **Connection loss:** during a longer file transcription, stop the server from its normal exit control. The file client should report failure instead of waiting indefinitely. Restart the server, start a fresh file-transcription run and verify it completes. Other connected clients may reconnect as usual.
4. **Cancel during transcription:** in the file client's own console, press Ctrl+C twice within one second, following the existing exit-confirmation behavior. Verify the file client exits and its FFmpeg/ffprobe children do not remain. Start another file run and confirm it works. Leave unrelated Python/FFmpeg processes alone.

The existing interactive batch summary may ask for Enter to close its window; that prompt is not a pending transcription. There is no need to force a ten-minute stall or change timeout values for acceptance; synthetic tests cover the deadline paths. The user confirmed the presentation follow-up has no remaining issues. Keep this checklist for future regressions; acceptance covers the reported local tests, not every media format or failure mode. Commit this stage separately before implementing the next A03 substage.

### Conda and Ctrl+C

The installed Conda `cli/main_run.py` calls `utils.wrap_subprocess_call`; its Windows branch writes a temporary `.bat` and invokes `cmd.exe /d /c`. The `Terminate batch job (Y/N)?` prompt belongs to that command-shell layer. The app's own signal handler prints its Chinese exit confirmation and calls `app.stop()` after two Ctrl+C presses within one second. Choosing N in the outer shell cannot resume an app that has already accepted cancellation. Leave the wrapper behavior unchanged in this stage.

To avoid the `conda run` wrapper during manual tests, activate the environment in PowerShell first, then launch Python directly:

```powershell
conda activate capswriter
python -u start_client.py transcribe --format srt "media-directory"
```
