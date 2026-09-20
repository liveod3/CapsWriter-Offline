# P0 stage 2a — Recording ownership and bounded capture

Status: accepted by the user after rapid start/stop recordings interleaved with delayed LLM processing remained independent. Other device, shutdown and failure checks were not separately reported; their automated coverage does not certify hardware behavior.

Delivery workflow from stage 2b onward: implement and run automated checks, hand off for manual acceptance, then commit only after the user confirms, before starting the next stage.

## Behavior and scope

Only one shortcut can own microphone capture at a time. A competing keyboard, mouse or UDP start cannot launch a second recorder against the same input. The shortcut that started recording remains responsible for stopping it; pressing another shortcut does not transfer ownership.

Every recording has its own capture bridge. Finishing closes that bridge to new audio while its recorder drains the queued tail. A new recording can start immediately using a new bridge. Old completion callbacks and canceled recordings cannot reset the newer recording's state or consume its audio.

Each bridge accepts at most 200 pending audio blocks (10 seconds at the existing 50 ms block size); finish/cancel controls have reserved capacity. Producers coalesce event-loop wakeups, preventing an unbounded backlog of scheduled audio coroutines. Buffer overflow aborts with a retry hint rather than silently dropping words. The recorder's pre-threshold cache has the same block limit. At most eight recorder futures may remain pending across shortcuts; further starts ask the user to wait. These are internal safety limits, with no local configuration migration.

A five-second microphone readiness timeout cancels capture, clears recording indicators and releases ownership. Recorder failure and rejected event-loop submission also roll back ownership. Stale stream callbacks are rejected using the stream's readiness event, and each callback retains its capture snapshot. Pause/resume is serialized with ownership changes; shortcut shutdown requests cancellation of active and draining recorders.

Audio-file handles are closed through the existing manager on every recorder exit. This does **not** establish that FFmpeg exits successfully or that file operations cannot block. Remaining A02 work, reserved for stage 2b: move blocking file I/O off the event loop, verify/terminate recording processes, deduplicate device recovery, isolate private PortAudio reinitialization and provide a complete shutdown barrier. Existing synchronous hardware resume also belongs to that lifecycle review.

## Changed files

- `core/client/audio/capture.py`: bounded recording-owned bridge.
- `core/client/state.py`: shared ownership lock, capture reference and recorder tracking.
- `core/client/shortcut/task.py`: exclusive launch, timeout/failure rollback, private finish/cancel and generation-aware completion.
- `core/client/shortcut/shortcut_manager.py`: cancel active and draining recorders on stop.
- `core/client/audio/stream.py`: capture snapshots, stale callback rejection and readiness invalidation on stop.
- `core/client/audio/recorder.py`: consume a fixed private source, bound pre-threshold buffering and close handles on failure.
- `core/client/app.py`: serialize pause/resume with capture ownership.
- `tests/unit/test_capture_ownership.py`: 20 new synthetic regressions.
- `tests/unit/test_processing_status.py`, `tests/unit/test_tray_actions.py`: adapt existing checks to the real ownership state.

Stage 1 acceptance and TODO/changelog records were updated in the same workspace. Existing user changes and local configuration values are preserved.

## Automated checks

Existing `capswriter` environment: Python 3.11.15. Default suite: **226 passed, 2 deselected**. Configured coverage gate: **73.73%**; that subset primarily measures server/protocol modules, not the new client capture code. Ruff, selected mypy paths, compileall and `git diff --check` passed.

Tests cover simultaneous competing starts, same/different shortcut relaunch while an older recorder is sending, cancellation and retry, readiness timeout, stale completion, submission failure, shutdown cancellation, bounded/coalesced buffering, FIFO tail delivery, cross-thread wakeups, overflow rollback, late stream callbacks and writer failure cleanup. All audio is synthetic; GPU, microphone, UI, real WebSocket/LLM and FFmpeg execution are mocked or absent.

## Manual acceptance

Restart the source client; the server can continue running the accepted stage 1 code. Keep the current shortcut and save settings. Check whichever shortcut modes are actually configured rather than changing the local configuration for this batch.

1. **Normal and rapid repeat:** dictate a short sentence, finish it, and immediately begin another. Repeat several times. Each result should contain only its own speech; the next recording should remain visibly active even if an older result arrives.
2. **Competing shortcuts:** start recording with one configured shortcut (for example right Ctrl), then trigger the other (for example mouse X2). The second must not start or stop a competing recording. Finish using the original shortcut, then confirm the other shortcut can start a fresh recording.
3. **Cancellation and key behavior:** if a hold shortcut is configured, check a short press below its threshold, a sustained press, release and immediate retry. Check ordinary key repeat does not create duplicate recordings. For toggle mode, check repeated start/stop clicks. Confirm no stuck indicator or audio from the canceled recording appears in the next result.
4. **Pause and idle resume:** manual tray pause must remain paused when a recording shortcut is pressed. Resume from the tray and dictate normally. If idle suspend is enabled, let it suspend and check the first shortcut recording after wake-up.
5. **Readiness failure, when practical:** if the device does not become ready, the timeout should release recording state and show a retry hint. After the device is available, retry. Full unplug/replug and driver recovery guarantees await stage 2b; report any failure without changing drivers for this check.
6. **Exit:** exit while recording, then restart the client and verify a fresh recording works. This checks the cancellation request path, not full process/driver shutdown certification.

If audio saving is already enabled, also confirm an ordinary recording still produces its expected audio file. No need to simulate a full buffer or force a file-system failure manually; those paths have synthetic regressions. Repeat the file/dictation concurrency check only if convenient; stage 2a does not optimize the previously reported ASR queue latency.

Report passed, failed or not-run items. After acceptance, continue with stage 2b; keep the parent recording-stability TODO open until the remaining lifecycle work and Windows checks are complete.
