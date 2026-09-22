# Terminal task outcomes and bounded recovery

Status: accepted by the user on 2026-09-22 after reporting no material issues.
The user did not enumerate individual manual checks; no broader hardware or
release certification is implied. Commit the accepted remaining changes and mark
the single TODO entry `[x]`. There are no additional implementation-stage acceptance
gates. This record supersedes the remaining-work lists in the historical 3a–3d
records; they remain evidence of earlier checks.

## Scope and outcome

An accepted dictation/file task must reach one observable success, failure or
cancellation outcome in the current client, with bounded waiting and cleanup.
Late/duplicate results cannot trigger another output. Recovery may stop the
affected service and require a manual restart. Automatic worker restart/replay
and a new IPC acknowledgement architecture are outside this item's scope.

| Failure or action | Final behavior |
| --- | --- |
| File upload, decoder or result wait stalls | Existing file deadlines stop the task, cancel/join its sender and receiver, reap media subprocesses and close its dedicated connection. Failed work does not produce success output. |
| Caught recognition exception | Return a content-free terminal task error. Discard that task's state and tails while keeping peer tasks. Older clients without task-error support are disconnected. |
| Microphone upload/final wait stalls | Bound sends and final waits; clear pending state and progress once. A final-wait timeout sends a scoped cancellation. Failed upload/unsafe interrupted send closes the captured connection. |
| Recording is canceled after audio was sent | Send `{"type":"cancel","task_id":"..."}` on the owning connection. Revoke the server input cache, suppress late results, acknowledge with terminal `cancelled`, and discard queued worker fragments/session for that connection/task pair. Cancellation before any upload needs no remote request. |
| Cancellation delivery fails | Close/abort the captured connection. File cancellation already uses its dedicated connection. Older servers reject the new message and close; peers on that old microphone connection may also fail cleanly. |
| One client's result send stalls | Bound send and close, retire that connection once, and continue delivering to peers. The sender remains serial. |
| Shared queue error, stalled read, or worker death | Stop the service, disconnect clients, clear their pending work, and reap server children. Retry requires a fresh task after restarting the server. |
| Model startup or live ASR operation hangs | Startup deadline or task-loop progress deadline stops the service; bounded process joins escalate to termination. Idle ASR polls update progress; no independent heartbeat thread can hide hung inference. |
| Aligner request/model loading hangs or aligner crashes | Signal shared failure and stop/reap the service. Only normal idle aligner exits are replaced automatically. A caught per-request alignment error retains the existing timestamp fallback. |
| Queue feeder silently loses a message | Existing client deadlines settle missing results. Inactive server input caches and worker sessions also expire, so a lost final/cancel cannot leave permanent state. This bounds failure, without claiming reliable IPC delivery. |
| Tray/signal exit races with startup or processing | Callbacks request shutdown. Network operations run on the event-loop owner; handlers drain before the owner reaps processes, closes queues/tray and closes the loop. Startup is not cleaned up reentrantly by a signal callback. |

Cancellation does not interrupt a native inference call in place. Its results are
suppressed; the worker processes cancellation when it returns to the queue, or
the progress deadline terminates the service if it never returns. Shared-channel
failure affects all clients; it is not presented as an isolated task failure.
The existing bounded failure history also bounds canceled identities; reaching
its per-connection budget requests reconnection.

The terminal guarantee concerns client task ownership and user-visible output,
not exactly-once packet delivery. For example, a result already being sent can
race a local timeout/cancel; the client rejects it after relinquishing that task.

## Defaults and compatibility

| Setting | Default | Meaning |
| --- | --- | --- |
| `model_startup_timeout` | 300 seconds | Total wait for recognition-model readiness, including a startup queue read that never returns. |
| `worker_stall_timeout` | 600 seconds | Maximum task-loop stall or task inactivity. Also expires inactive partial input and worker sessions. |
| `aligner_request_timeout` | 60 seconds | Existing aligner request deadline; now stops the service on timeout instead of leaving a possibly wedged aligner alive. |
| `result_send_timeout` | 10 seconds | One ordinary result send before retiring its client. |
| `result_queue_timeout` | 60 seconds | One stuck result read or worker wait for output capacity. |

Missing/invalid new limits use compatible positive defaults. Slow model startup,
CPU inference or heavily queued workloads may need higher limits. These are
stall/inactivity limits rather than a total-duration limit on a progressing file.
The worker watchdog also bounds an aligner pipe read that ignores its timeout.

Only missing server fields and the changed aligner-timeout comment were merged
into the ignored local configuration; user values remain intact. Restart client
and server for acceptance. Public defaults are in the server template. Old audio
messages still work; the new cancellation error is only sent in response to a
new cancellation request. No credentials, models, logs or recordings are staged.

## Implementation and verification

Changes since the last accepted commit are grouped below:

- `core/protocol.py`, `core/client/audio/recorder.py`, `dictation_lifecycle.py`,
  `output/result_processor.py`: cancellation message/result, captured connection
  ownership, timeout cancellation and owned cleanup through repeated cancellation.
- `core/server/connection/ws_recv.py`, `ws_send.py`, `server_manager.py`,
  `delivery.py`: bounded delivery/cleanup, scoped cancellation, input expiry and
  shared failure policy.
- `core/server/worker/process_manager.py`, `supervision.py`, `task_handler.py`,
  `worker.py`, `__init__.py`, `model_loader.py`, `check_model.py`,
  `core/server/engines/manager.py`, `state.py`, `app.py`: spawn-safe progress/failure
  state, startup/runtime supervision, session expiry and shutdown ownership.
- `core/tools/daemon_executor.py`: correct Future running state for a canceled
  async reader. At most one stalled daemon read is abandoned on service exit;
  Python cannot forcibly terminate that reader thread separately.
- `config_templates/config_server_template.py`: public limits and changed aligner
  timeout policy. `TODO.md` and `docs/CHANGELOG.md`: whole-item status/history.
- `tests/unit/test_terminal_recovery.py`, `test_server_delivery.py`, and adjusted
  existing isolation/error/microphone tests: synthetic regression coverage.
  `tests/manual/result_delivery_server.py`: optional model-free fault fixture.

Validation uses the prepared `capswriter` environment, Python 3.11.15:
**412 passed, 2 deselected**, configured coverage **84.70%**. Ruff, the seven
selected mypy files, compileall including existing local configurations, fixture
help and `git diff --check` pass. Coverage is for the configured modules, not the
entire application. Tests use unique ignored temporary directories and no shared
pytest cache.

Regressions cover invalid cancellation identities, duplicate cancellation, peer
isolation, late results, cancel-send failure, repeated cancellation, lost finals,
stalled/broken startup reads, runtime ASR death/hang, abnormal versus idle aligner
exit, aligner timeout/queue failure, a dead progress lock, and startup/exit races
on both the owner and a foreign thread. A real **synthetic** Windows-spawned child
also verifies progress sharing, hang detection, termination and reaping. It loads
no model and invokes no microphone, GPU, key injection or cloud service.

Real models, microphone/desktop interactions and release packaging have not been
run by the agent. They are not established by the synthetic checks. Broader model
quality, timestamp provenance, diagnostics privacy and release validation retain
their own existing TODO entries; they are not additional stages of this item.

## One manual acceptance pass

1. Restart both source client and server. Confirm short/long dictation and a file
   transcription succeed once, with no lingering progress indicator. If practical,
   run a file task alongside dictation and check both results.
2. Exercise the existing short-press cancel behavior in hold mode, if configured,
   and normal stop/exit behavior with the user's existing shortcuts. Do not change
   shortcut mode just for this check. Cancellation after upload and its races are
   covered by automated tests; there is no new user-facing cancel control.
3. Disconnect/exit one file client during work and confirm another client still
   works. Exit the server during a pending task; the client should report failure
   and clear pending work without late text output. Restart the server and confirm
   a fresh task succeeds. Check the app's own model processes/tray are gone on exit.
4. Optionally, after stopping the regular server, use the existing local plaintext
   port with `python tests/manual/result_delivery_server.py --port 6016 --mode queue`
   (or `--mode worker`). Submit a short dictation/file task; the model-free fixture
   should stop, and the client should fail its pending work. Restart the regular
   server afterward. Do not change a LAN/TLS setup to fit this optional fixture.

No real model corruption, forced GPU hang, or manual killing of unrelated processes
is needed. The user accepted the whole item on 2026-09-22; its TODO is marked `[x]`.
