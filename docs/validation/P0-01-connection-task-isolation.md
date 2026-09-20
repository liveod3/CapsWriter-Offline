# P0 stage 1 — Connection-scoped task identity

Status: accepted by the user on 2026-09-20; removed from the active TODO. Implementation and automated checks completed on the same date.

User-reported checks: short and long dictation both work and allow subsequent recording; file transcription and dictation produce independent completed outputs; two concurrent file transcriptions work, with each roughly half as fast as a single file. Dictation during dual-file work is very slow. A disconnect/reconnect exercise and forced same-ID live-model test were not separately reported; their coverage remains synthetic.

Code review confirms one ASR worker selects a task fragment and runs recognition plus any alignment before selecting the next fragment. This explains why concurrent work shares throughput and dictation can wait for file work. The actual latency split and any GPU/driver contribution were not measured. The report is recorded under the audio/result/engine contract TODO for later investigation; stage 1 does not change scheduling priority or inference parallelism.

## Scope

Worker sessions and scheduling buffers now use `(socket_id, task_id)` keys. Pipeline lookup and final cleanup use the same identity. Disconnected sessions are cleaned during idle polling and before selecting buffered work. A connection's completion or disconnect must leave another connection's same-ID state and pending fragments intact.

The JSON protocol and configuration fields are unchanged. Receive caches already belong to individual connections; the sender already routes results by `socket_id`. Tests exercise these boundaries with the corrected worker state. Alignment requests use their own unique `request_id`, so their correlation mechanism does not need a change here.

Changed application files:

- `core/server/schema.py`: internal `TaskKey` alias and `Task.key` property.
- `core/server/state.py`: connection-scoped session lookup and cleanup.
- `core/server/worker/task_handler.py`: connection-scoped FIFO/round-robin scheduling and final/disconnect cleanup.
- `core/server/worker/pipeline.py`: connection-scoped session membership check.

Regression coverage: `tests/unit/test_connection_task_isolation.py`. The existing scheduling tests remain unchanged.

## Automated evidence

Interpreter: existing Conda environment `capswriter`, Python 3.11.15.

| Check | Result |
| --- | --- |
| New isolation tests plus existing scheduler tests | 19 passed (15 new, 4 existing) |
| Default pytest suite with coverage gate | 206 passed, 2 deselected; 73.73% coverage of the configured module subset |
| Ruff, configured roots | Passed |
| mypy, configured protocol/schema/merger/format paths | Passed (7 files) |
| compileall, entry points/templates/core/LLM/tests and existing root configs | Passed |
| `git diff --check` | Passed |

Tests use 16 kHz float32 synthetic sine waves, a fake recognizer, in-memory queues/transports and pickle round trips. They cover both completion orders for mic/mic, file/file and mic/file collisions, plus disconnect before inference, during inference and before sending an already queued result. They verify that audio, text, tokens, timestamps, task source and destination remain separate, and each connection receives one final response in these normal-completion scenarios.

These tests do not start an application, load a model, capture audio, issue GPU commands or connect to an LLM. No real-model, microphone, interactive Windows or release-package checks were run. General worker-error/cancel outcomes belong to a later P0 stage.

## Manual acceptance

Use the updated source checkout and the existing `capswriter` environment. Restart the server so its worker loads the new code; a running old worker or previously built EXE will not use these changes. Use disposable copies of non-sensitive media in separate test directories for file checks.

1. **Ordinary dictation:** start the server and one microphone client using the usual launcher. Dictate several short sentences, then one longer recording that spans a normal chunk boundary. Confirm complete output, normal completion and successful subsequent dictation.
2. **Concurrent file and microphone work:** while a sufficiently long file is being transcribed, dictate a clearly different short sentence. Confirm the sentence appears only in the microphone output, the file outputs contain only file content, and both tasks finish. Example file command:

   ```powershell
   conda run -n capswriter python start_client.py transcribe --format srt,txt,json --no-recursive "<test-media-path>"
   ```

3. **Independent completion:** in separate terminals, transcribe two different files, one short and one long, with output paths in different test directories. Let the short one finish first. Confirm the long one continues and finishes with complete text. Repeat with their launch order reversed if convenient.
4. **Disconnect and reconnect:** start two sufficiently long file transcriptions. Close one transcription client's terminal while it is active, leaving the server and other client running. Confirm the surviving client completes, then start a new transcription and confirm it works. This checks server isolation, not the canceled client's terminal-outcome semantics.
5. **Deterministic same-ID check:** normal clients generate unique IDs, so the preceding checks alone do not reproduce the original collision. Re-run the synthetic collision suite if desired:

   ```powershell
   conda run -n capswriter python -m pytest -v tests/unit/test_connection_task_isolation.py tests/test_aud04_task_buffer.py -p no:cacheprovider
   ```

   Expected: 19 passed. This deliberately reuses IDs across fake connections without modifying client code or local settings; it is not a live-model collision test.

Report each manual item as pass/fail/not run. For failures, note which client was doing what, whether the other client continued, and any content-free error category. There is no need to share private audio, transcripts or complete logs.

Acceptance recorded; the next implementation batch is [stage 2a: recording ownership](P0-02a-recording-ownership.md).
