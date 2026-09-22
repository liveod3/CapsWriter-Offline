# P0 stage 3d — Bounded server result delivery

Historical implementation snapshot. The remaining-work lists and checks below
describe the delivery slice before whole-item completion. Current behavior,
verification and the single acceptance gate are in the
[whole-item record](P0-03-terminal-outcomes.md). This slice remains uncommitted as
part of that whole item and does not require separate acceptance.
Stage 3c was accepted and committed as `3e6ee6e`.

## Behavior

Ordinary recognition results previously had no WebSocket send deadline. A stuck
client could hold up the shared sender, while permanent queue errors could be
logged and retried indefinitely. Worker output backpressure also had no total
deadline. This stage applies two distinct policies:

- **Client-local failure:** a timed-out/failed result send revokes that connection's
  input cache, activity and shared worker identity, then closes it. Later queued
  results and audio for that connection are discarded. Other connections remain
  eligible for delivery. The sender remains serial, so a peer can wait for this
  bounded send/close interval; this is not a concurrent sender redesign.
- **Shared-channel failure:** input/output queue exceptions, an invalid internal
  result, a result read that never returns, a worker failure signal or an observed
  ASR process exit ends the service. The listener closes and clients disconnect,
  producing observable task failures through their existing connection handling.
  The main application's final cleanup also stops workers and the tray. There is
  no automatic replay or restart over a potentially damaged queue; restart the
  server and retry failed work with new tasks.

A caught inference exception still uses stage 3b's isolated task error outcome.
Task-error sends and legacy-client close fallback retain their five-second
deadline. Closing a failed connection also has a five-second deadline; close
failure or cancellation aborts the transport. A retired connection cannot submit
tail audio while its close handshake is pending.

The sender owns one asynchronous IPC read at a time and checks worker health
while waiting. Normal empty queue polls do not constitute a stalled channel.
A pipe read that ignores its queue timeout cannot create a sequence of retry
threads: the service stops after the read deadline. Python cannot terminate that
blocked thread directly; the existing daemon executor lets it end with the process.
Its Future is now marked running correctly so canceling an async waiter cannot
make the returning reader raise `InvalidStateError` while setting its result.

The worker signals a shared multiprocessing Event before potentially blocking
model/GPU cleanup. Queue errors and unexpected task-loop errors stop the loop
after clearing its sessions and buffers. The parent notices the signal or a dead
worker while waiting for results. Model startup also observes a reported failure
signal; this does not add a deadline for a live, unresponsive startup. Shutdown
queue errors no longer skip process termination, and forced ASR termination now
has a follow-up join. Startup RuntimeErrors are no longer silently suppressed
unless an explicit stop already interrupted the event loop.

## Configuration and compatibility

| Server setting | Default | Meaning |
| --- | --- | --- |
| `result_send_timeout` | 10 seconds | Per ordinary-result WebSocket send. A failure retires that client connection. |
| `result_queue_timeout` | 60 seconds | Maximum worker wait for output capacity, or maximum wait for one IPC read that fails to return. A failure stops the service. |

The server template defines both settings. Only missing fields were added to the
ignored local server configuration, preserving existing values and encoding.
Absent or invalid/nonpositive/nonfinite/boolean values fall back to the defaults.
Restart the server to load configuration changes. Error-message and close
deadlines remain internal five-second limits. No wire fields or client settings
changed in this stage; local/LAN authentication, TLS and input-size limits remain
in force. The shared Event is passed through the Windows spawn argument chain,
not attached to any task/result sent through a queue.

## Scope remaining

This slice does not complete A03. Bounded handling of live-but-hung ASR/model
loading and wedged aligners, task cancellation and server shutdown on owning
threads remain in scope. Stopping the affected service and requiring a manual
restart is sufficient; automatic restart/backoff and task replay are not required.

The local Python 3.11 standard-library source also confirms that the background
`multiprocessing.Queue._feed` thread can lose an item after a serialization/write
error, release its capacity and print the error without making `put()` fail.
This slice does not detect those silent delivery losses. The whole-item acceptance
must verify that deadlines still terminate affected tasks and bound cleanup; it
does not require a new acknowledgement or feeder-supervision architecture. The
read deadline covers a read that actually remains blocked, not a queue that
continues returning empty after an item was lost.

The rewritten sender no longer logs full recognition text or arbitrary send-error
messages. Other recognition-content diagnostics remain under A04; this stage is
not a repository-wide logging/privacy audit. Real OS refusal to terminate a
process is not made recoverable by Python deadlines.

## Files and validation

- `core/server/delivery.py` (new), `connection/ws_send.py`, `connection/ws_recv.py`: timeout parsing, shared failure categories, one owned result read, send/close policy and revoked input ownership.
- `core/server/connection/server_manager.py`, `core/server/app.py`: stop the result service on shared failure and run application cleanup after it returns.
- `core/server/state.py`, `worker/__init__.py`, `worker/worker.py`, `worker/task_handler.py`, `worker/process_manager.py`: shared failure notification, bounded output capacity waits and worker cleanup.
- `core/tools/daemon_executor.py`: correct Future running state for canceled async read waiters.
- `config_templates/config_server_template.py`: public defaults; the ignored local server configuration only received missing fields.
- `tests/unit/test_server_delivery.py` (new): 32 synthetic regressions. Existing task-error and isolation assertions now reflect explicit connection/worker cleanup.
- `tests/manual/result_delivery_server.py` (new): optional loopback fault fixture with no models.
- `TODO.md`, `docs/CHANGELOG.md`, this record: scope and pending acceptance. The unrelated pre-existing caret-context TODO entry is preserved.

Existing `capswriter` environment, Python **3.11.15**. Default suite and coverage
gate: **378 passed, 2 deselected**. Configured coverage is **83.67%**, not whole-app
coverage. Ruff, the seven selected mypy files, compileall including existing local
configurations, fixture help and `git diff --check` pass. Tests use unique ignored
`.cache/` temporary directories and disable pytest's shared cache provider.

Tests cover send exception/timeout and peer delivery, broken/invalid/stalled
result queues, input submission failure, bounded worker backpressure, temporary
backpressure recovery, runtime worker death and failure notification, input tails
after retirement, listener/client cleanup, normal shutdown versus worker death,
startup failure propagation, spawn argument wiring and cleanup after broken
shutdown queues. All workers, sockets, audio and faults are synthetic/mocked; no
real microphone, key injection, model/GPU or cloud LLM was exercised. No release
build or real forced process termination was performed.

## Checks to include in whole-item acceptance

1. **Normal regression:** restart the source server and use the accepted stage 3c
   client. Check short/long dictation and a file transcription. If practical, use
   two file clients or a file client plus dictation to check independent results.
2. **Disconnect during work:** close one file client during transcription and
   confirm another client can still complete. Start a fresh transcription and
   verify recovery. There is no need to force an actual slow TCP receiver; send
   timeouts and peer delivery are covered by injected automated tests.
3. **Optional shared-failure fixture:** stop the normal server first. For an
   existing plaintext-loopback client, activate `capswriter` and run at the repo
   root (substitute the client's existing port if needed):

   ```powershell
   python tests/manual/result_delivery_server.py --port 6016 --mode queue
   ```

   Wait for client reconnection, then record and finish a short phrase, or submit
   a valid test media file. The first submitted audio task deliberately breaks
   the fixture's result channel. Expect the fixture to report the failure and
   exit, the client to clear its pending work/report failure, and no success
   output for that task. Repeat with `--mode worker` to exercise the shared worker
   failure signal. The fixture runs the real SocketManager/receiver/sender, but
   uses synthetic queues and no recognition process or models. It does not save
   audio; normal client recording/log settings still apply. Do not change a
   LAN/TLS installation to fit this optional loopback fixture.
4. **Restore and exit:** stop any remaining fixture, restart the normal server,
   wait for the client to reconnect and confirm fresh dictation/file work succeeds.
   Exit the server using its normal control and check that its tray and model
   processes do not remain. Leave unrelated Python processes alone.

Whole-item acceptance is pending. Do not request separate acceptance of this slice.
