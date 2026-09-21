# P0 stage 3c — Microphone upload and final-result deadlines

Status: accepted by the user after manual testing; no issue was reported.
Commit this stage separately before the next implementation stage.
Stage 3b was accepted and committed as `eaee651`.

## Resulting behavior

Each microphone audio send now has a deadline. A disconnected client, rejected
send, send exception or timeout fails that recording rather than silently
dropping audio and later accepting a partial result. Failed uploads close their
captured WebSocket within five seconds, aborting its transport if close fails or
is canceled. Cleanup cannot clear or abort a newer connection published later.
Other unfinished recordings on the failed connection also fail when the receiver
observes the disconnect; they cannot safely continue uploading across reconnect.

The final ASR wait begins immediately before sending the final audio message.
Only a matching final submitted task can claim a successful result. Deadline
expiry clears its pending state, context, audio association and progress, cancels
its remaining recorder work and displays a retry hint. Late, duplicate and foreign
successes cannot reach the LLM, text output or success archive. A final result
received before upload returns waits for successful upload; upload failure or
cancellation invalidates that result. Partial progress does not extend the final
ASR deadline. Ongoing recording itself has no new duration limit.

The client previously stopped receiving ASR while it ran the previous result's
LLM/output pipeline. It now owns separate receiver, serial result processor and
deadline watcher tasks. ASR replies and errors can be accepted promptly while
an earlier LLM request runs; accepted results are no longer on an ASR deadline.
Final text still processes one result at a time in receive order. A connection
loss fails unfinished recognition but preserves results already accepted for
processing. All three tasks and the exit waiter are canceled and joined on exit.
The watcher polls every 250 ms; receipt also checks the deadline directly.

At most 64 dictations may be uploading, awaiting ASR or awaiting/completing text
processing. Admission rejects further recordings until capacity is released,
instead of evicting an older task's context. The existing 32-recorder draining
limit remains in place. Task-owned audio associations are removed on terminal
cleanup; no new deletion of existing audio files is introduced.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `mic_io_timeout` | 60 seconds | Deadline for each microphone WebSocket send. |
| `mic_result_timeout` | 600 seconds | Absolute wait from final-message submission to final ASR receipt; excludes queued/running LLM and output work. |

The tracked client template defines these fields. The existing ignored local
client configuration received only missing fields; all existing values and file
encoding/newlines were preserved. Old configurations fall back to the same
defaults. Nonpositive, nonfinite, boolean and invalid timeout values also use the
defaults. Increase the result timeout for slow hardware or heavily queued ASR.
Configuration still requires client restart; live configuration reload is a
separate TODO. There is no protocol change in this stage.

## Scope and limitations

This is client recovery, not server cancellation or process supervision. A timed
out result is ignored locally; the server may finish inference later. Explicit
cancel messages, ASR/aligner startup/death/hang supervision, general queue/send
failure policy and server shutdown ownership remain under A03. The existing
LLM service owns its separate request deadline. This stage does not bound all OS
file operations or fix output/archive failure independence (A06).

The self-review found that canceling a connection close could otherwise forget
the socket before cleanup completed; the helper now aborts that captured
transport before propagating cancellation. No new audio callback work, global
key simulation, direct Tk access or inference on the event loop was introduced.

## Changed files

- `core/client/dictation_lifecycle.py` (new): compatible timeout values, capacity and bounded connection cleanup.
- `core/client/audio/recorder.py`: upload deadlines, final submission ownership, successful-upload completion and cleanup.
- `core/client/output/result_processor.py`: owned receiver/processor/watcher, final ownership, deadline expiry and disconnect handling.
- `core/client/state.py`, `core/client/shortcut/task.py`: bounded pending state and recording admission/failure feedback.
- `config_templates/config_client_template.py`: public timeout defaults; the ignored local client configuration has only the missing fields added.
- `tests/unit/test_mic_task_lifecycle.py` (new): 26 synthetic regressions.
- `tests/unit/test_dictation_pipeline.py`, `test_processing_status.py`, `test_websocket_shutdown.py`: fixtures now model pending upload/result ownership.
- `tests/manual/task_error_server.py`: optional `--mode stall`; the existing default error mode remains available.
- `TODO.md`, `docs/CHANGELOG.md`, this record: scope and acceptance status. The pre-existing caret-context TODO entry remains untouched.

## Automated validation

Existing `capswriter` environment, Python **3.11.15**. Default suite and coverage
gate: **346 passed, 2 deselected**. Configured coverage remains **81.85%**; this
server/protocol coverage subset does not measure the new microphone lifecycle.
Ruff, the seven selected mypy files, compileall (including existing local
configurations), CLI/stub help and `git diff --check` pass. Tests use a new unique
directory under ignored `.cache/` and disable pytest's shared cache provider to
avoid the existing temporary-directory permission issue.

Regressions cover invalid/legacy defaults, intermediate/final send failures and
timeouts, disconnected upload, final-result timeout, reply-before-upload races,
stale/foreign/duplicate replies, receiving and expiring tasks during slow text
processing, peer preservation, connection cleanup timeout/cancellation, capacity
without context eviction and owned shutdown. Inputs, sockets and processing are
synthetic/mocked. No real microphone, global keys, cloud LLM, model/GPU inference
or release build was exercised automatically.

## Manual acceptance

Restart the source microphone client. The accepted stage 3b server can stay in
use for normal checks; this stage changes no server code or wire fields.

1. **Normal and sequential results:** check short/long dictation, several quick
   successive recordings and the next recording after completion. If LLM actions
   are normally enabled, record again while an earlier request is processing.
   Confirm the results remain separate and text processing remains serial. A
   previous LLM request should not prevent receipt of the next ASR result.
2. **Disconnect and recover:** stop the server during a recording or while waiting
   for ASR. Expect failure feedback and cleared recording/progress state. Restart
   the server, wait for reconnection and verify a fresh recording succeeds.
3. **Optional three-second stall test:** stop the regular server and the regular
   microphone client first. For an existing plaintext-loopback setup, run the
   following in two terminals at the repository root, with `capswriter` activated.
   Use the client's existing port if it differs from 6016. Do not change a
   LAN/TLS setup to accommodate this stub.

   ```powershell
   # Terminal 1: accepts audio but deliberately sends no results.
   python tests/manual/task_error_server.py --port 6016 --mode stall
   ```

   ```powershell
   # Terminal 2: override only this process; no configuration file is edited.
   python -c "from config_client import ClientConfig as C; C.mic_result_timeout=3.0; from start_client import main; raise SystemExit(main(['mic']))"
   ```

   Record a short phrase and stop recording. Roughly three seconds after final
   submission, expect a recognition-timeout hint and cleared processing status,
   without inserted text or a successful transcript record. Repeat to confirm a
   fresh task can start. The stub saves no audio; normal client saving settings
   still apply. The automated suite covers late-result rejection; no ten-minute
   wait or broken model is needed for this manual check.
4. **Restore and exit:** stop the stub and temporary client, restart the normal
   server/client and verify dictation works with normal timeout defaults. Exit
   while a task is awaiting ASR or queued behind a result and check that the
   application closes cleanly.

The user reported that manual testing found no issues. Acceptance covers those local checks; individual checklist results and hardware/model combinations were not enumerated.
