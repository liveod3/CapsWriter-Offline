# Architecture

CapsWriter separates capture and presentation from inference. The WebSocket contract is shared, while each process owns its resources and task state.

## Follow a task

1. `core/client/cli.py` selects microphone, file transcription, or local subtitle rebuilding before importing hardware/UI modules.
2. Microphone capture uses a bounded per-recording bridge. File transcription uses FFmpeg and a bounded in-flight upload window.
3. The client sends JSON audio messages defined in `core/protocol.py`. Audio is Base64-encoded 16 kHz mono float32, despite the `binary` subprotocol name.
4. The server validates messages and maintains connection-scoped task audio buffers. Internal Task records preserve `(socket_id, task_id)` identity.
5. TaskBuffer schedules segments round-robin while preserving each task's FIFO order. One ASR worker executes inference and optional punctuation/alignment.
6. The parent routes results or terminal failures to the owning connection. Client task ownership rejects foreign, late, and duplicate outcomes.
7. The client saves file outputs or processes dictation through optional LLM, focus-protected insertion, and independent archives.

`rebuild-srt` takes a separate local path: edited TXT plus timed JSON produces new SRT without audio capture or inference.

## Keep resource ownership explicit

| Owner | Resources and responsibilities |
| --- | --- |
| Client audio manager | PortAudio lifecycle and device monitoring under a lifecycle lock |
| Client event loop | Upload/result tasks, deadlines, LLM requests, and bounded cleanup |
| Tk host thread | Windows and UI mutations through queued work |
| Server event loop | Connections, input validation, and bounded result delivery |
| Recognition subprocess | ASR, punctuation, session state, and scheduling |
| Aligner sibling process | On-demand alignment model and its native GPU resources |
| Parent process manager | Startup readiness, worker supervision, shutdown, and idle aligner replacement |

PortAudio callbacks must not block. Blocking queue reads run through an executor. Aligner IPC uses neutral pickleable records rather than backend implementation objects. Idle alignment cleanup exits the entire process; abnormal exit or timeout triggers bounded service failure rather than an unlimited wait.

## Separate text paths

`text` uses [text overlap merging](text-merging.md) independently of timestamps. `text_accu`, tokens, and timestamps support timed output. EngineCapabilities determines whether punctuation or alignment assistance is needed. Model/contract limits and timestamp-quality labeling still have open work in [TODO](../../TODO.md).

LLM is a separate, optional client action with no conversation history. Providers and presets load from TOML per request. UI localization does not translate recognition text, prompts, or configuration IDs. See [localization](localization.md) and [configuration snapshots](../reference/configuration.md).

## Read the boundaries before editing

[AGENTS.md](../../AGENTS.md#preserve-architecture-boundaries) owns the detailed concurrency, privacy, network, and packaging rules. Its implementation map identifies the maintained entry points. Keep those rules centralized rather than copying a second checklist here.
