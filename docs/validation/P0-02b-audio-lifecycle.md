# P0 stage 2b — Audio recovery, file I/O and shutdown

Status: accepted by the user, including saved-audio testing. Commit this stage separately before beginning the next P0 item. Stage 2a acceptance and the delivery workflow were recorded in commit `7771322`.

User feedback: repeat recording, pause/resume, device recovery and exit/restart passed the conversational checklist. After the configuration and output location were explained, the user also confirmed that audio saving works. Individual hardware subcases and encoder formats were not separately enumerated; acceptance applies to the reported local checks, not every driver or environment. The root-level recording folders and restart requirement for configuration changes were raised as future improvements and added to P1 in `TODO.md`.

## Behavior and scope

Stream completion callbacks only notify the existing device monitor. That single thread coalesces recovery requests, rejects old stream identities and spaces repeated recovery attempts. Pause keeps the microphone released; shutdown publishes a permanent barrier before waiting for backend operations. A partially started stream is closed, and a failed close retains the handle so private reinitialization cannot run over an unclosed stream. Recovery cancels only the interrupted capture, allowing a fresh attempt after the device returns.

Private sounddevice device-cache refresh now lives in `portaudio_compat.py`. It accepts the 0.5 series and initialization counts zero/one, checks the private functions and preserves the count when initialization fails and is retried. Unsupported or shared initialization states skip refresh. No DLL unloading or private FFI access is used. The installed version is 0.5.5; mock coverage is not certification of other versions or Windows drivers.

Each recording serializes file creation, writes and finalization on its own worker thread. Audio manager construction and hardware startup/resume also run outside the event loop. FFmpeg uses an unbuffered pipe with partial-write handling. Normal completion closes stdin and checks the exit status before sending the final recognition marker. Cancellation or a 10-second I/O deadline requests child termination; finalization waits up to five seconds before kill and a further two-second wait. WAV remains the fallback. Existing files are preserved, and interrupted recordings may leave partial files for recovery.

Shutdown first rejects new work, stops input listeners and requests recorder cancellation. It tracks actual asyncio recorder tasks separately from their cross-thread futures, because a canceled future can report completion before cleanup finishes. Writer cleanup survives repeated caller cancellation. The event loop stays alive while recording cleanup, stream/monitor closure and connection closure complete. A slow microphone startup cannot restart listeners after shutdown has begun.

These changes do not optimize ASR scheduling, implement server task cancellation or close the separate Tk-host lifecycle TODO. Native driver calls and filesystem operations cannot be forcibly interrupted by Python timeouts; an unresponsive driver or filesystem remains a limitation requiring real-machine evidence. Child termination is covered with fake processes, not a real FFmpeg run.

## Changed files

- `core/client/audio/stream.py`, new `portaudio_compat.py`: one recovery owner, stale-event rejection, safe refresh and permanent shutdown barrier.
- `core/client/audio/file_manager.py`, new `file_writer.py`, `recorder.py`: serial background file I/O, partial writes, child completion/abort and per-recording cleanup.
- `core/client/app.py`, `manager/mic_runner.py`, `shortcut/task.py`, `state.py`: asynchronous hardware opening, pause/resume serialization and actual-task shutdown tracking.
- New `tests/unit/test_audio_recovery.py` and `test_audio_file_lifecycle.py`; updated `test_audio_stream_lifecycle.py`, `test_tray_actions.py` and `test_websocket_shutdown.py`.
- `TODO.md` and `docs/CHANGELOG.md`: record acceptance, retire A02 from the active backlog and add the two requested future improvements. The pre-existing caret-context TODO change remains separate.

Local configuration, credentials, user recordings and model files were not changed.

## Automated checks

Existing `capswriter` environment, Python 3.11.15:

- Default suite with the configured coverage gate: **252 passed, 2 deselected**; 26 added cases relative to stage 2a.
- Configured coverage: **73.73%**, above the 50% gate. This configured subset primarily covers server/protocol code and does not measure the new client lifecycle code.
- Ruff across entry points, templates, core, LLM and tests: passed.
- Mypy on the configured protocol/schema/merger/format paths: passed, seven files.
- Compileall, including existing local configuration, and `git diff --check`: passed.

Regressions cover repeated finish callbacks, pause/shutdown races, partially started streams, failed close, initialization retry, delayed hardware resume, blocked/partial encoder writes, creation finishing after cancellation, encoder exit failure, WAV output, repeated cleanup cancellation and shutdown waiting for actual recording tasks. Startup/shutdown tests use fake listeners and devices. All audio is synthetic; no real microphone, keyboard injection, model, network LLM or real encoder was used. GUI, hardware and release packaging checks were not run.

## Manual acceptance

Restart the source client to load this batch. The server can remain running. Keep existing local configuration; report unavailable checks as not run.

1. **Repeat recording:** dictate a short sentence and a longer sentence, finish each and immediately start the next. Check independent results and a responsive recording indicator. If delayed LLM processing is enabled, repeat while an older result is still processing.
2. **Pause and idle resume:** pause from the tray, verify the microphone is released and a shortcut does not override manual pause, then resume and dictate. If idle suspend is enabled, check the first recording after it suspends automatically.
3. **Default-device change:** if configured to use the system default input, switch it while not recording, then dictate again. Also check a change while paused: the app should stay paused until resumed. If a specific input device is configured, an OS default change should not override that choice.
4. **Unplug/replug:** where practical, disconnect the current microphone during recording, then reconnect it. Allow several seconds for the monitor to retry and start a fresh recording. The interrupted recording should release its indicator/ownership; subsequent speech must not be appended to the old capture. A headset whose Windows driver does not report stream completion may behave differently; record the device and observed state if recovery fails.
5. **Saved audio:** set `ClientConfig.save_audio = True` in the root local `config_client.py` (not the template), then restart the client. Record short and long samples and play the resulting files under `<project root>/<YYYY>/<MM>/assets/`, using the recording's local date. Output is MP3 when FFmpeg is available, otherwise WAV. Check that completed files are playable and a canceled recording does not prevent the next one. Restore `False` and restart afterward if audio retention is no longer wanted; existing files remain. No need to simulate a blocked disk or broken encoder manually.
6. **Exit:** exit once during recording, once soon after ending a recording and, if practical, during microphone recovery. Confirm the client exits, its microphone indicator clears and no recording FFmpeg child remains. Restart and verify a new recording. Leave unrelated Python/FFmpeg processes alone.

The user has accepted this stage at the scope recorded above. Retain this checklist for future regressions; broader Windows/driver verification remains in the release-validation backlog and is not inferred from successful mocks.
