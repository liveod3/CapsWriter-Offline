# Project audit — 2026-09-16

This report records evidence for the active [TODO](../../TODO.md). It is not a second backlog. Baseline: Git `d0dae70`, with the existing documentation cleanup in the working tree; application code was not changed during this audit.

## Scope and confidence

The review covered first-party entry points/configuration, client audio and shortcuts, WebSocket/protocol boundaries, server scheduling and process supervision, engine adapters and alignment, result delivery and persistence, LLM/caret/UDP boundaries, UI lifecycle, diagnostics/history, packaging and automated checks. Findings below distinguish synthetic reproductions from static failure paths and product choices.

This is a repository-wide architecture and boundary audit, not certification of every implementation line or supported runtime. It did not exercise microphones, global input, a visible GUI, GPU/model inference, cloud providers, a complete package build or a clean Windows installation. Vendored/export code was inspected at integration boundaries, not exhaustively audited. No dependency vulnerability database scan, Git-history secret scan or upstream license inventory was performed. Private recordings, credentials and existing logs were not used as fixtures. The historical [Qwen runtime report](../BUG-2026-08-09-QWEN-ASR-RUNTIME-HALLUCINATION.md) remains unverified.

## Verification completed

The existing `capswriter` environment used Python **3.11.15**. No application or model was started and no dependencies were installed.

| Check | Result and limit |
| --- | --- |
| Default pytest suite with configured coverage | **191 passed, 2 deselected**; coverage **51.41%**, above the configured 50% gate. This measures only the configured module subset, not the whole application. |
| Native Windows menu tests | **2 passed**; handle/bitmap/submenu checks, not interactive focus or DPI validation. |
| Ruff | Passed for configured roots; selected fatal/syntax rules only, with engine code excluded by configuration. |
| mypy | Passed for the seven selected protocol/schema/merger/format files. |
| compileall | Passed for entry points, templates, core, LLM, tests and existing local configurations. |
| pip check | No broken installed requirements; this is not a vulnerability or reproducibility check. |
| Additional mocked boundary probes | **14 assertions confirmed the current failure cases** listed below; synthetic data, no microphone, network or model. These are local audit probes, not added regression tests. |

The first pytest attempt encountered 66 fixture errors from an inaccessible default temporary directory. Re-running with a new workspace-local `--basetemp` and `-p no:cacheprovider` passed; those errors are not counted as product defects. Coverage is uneven: protocol 86%, WebSocket receive 32%, task handler 36% and token merger 11%. Passing the aggregate gate does not resolve the uncovered failure paths.

## Findings

### A01 — Cross-connection task identity

**Confirmed by probes 1–2.** [WorkerState.get_session](../../core/server/state.py) and [TaskBuffer](../../core/server/worker/task_handler.py) key shared worker state by `task_id`. Two connections using the same ID receive the same session; disconnect cleanup for one can remove the other's queued work. Receive-side per-connection audio caches do not protect these downstream structures. This is a P0 isolation defect: carry `(socket_id, task_id)` through scheduling, processing, results and cleanup, with collision regressions.

### A02 — Recording ownership and recovery

**Confirmed by probes 3–5; additional lifecycle risks are static.** Separate [ShortcutTask](../../core/client/shortcut/task.py) instances can both launch while consuming the same untagged, unbounded [input queue](../../core/client/state.py). A stream-readiness timeout leaves recording flags set. [AudioStreamManager](../../core/client/audio/stream.py) accepts audio from a callback belonging to an older readiness event.

Recovery also creates background reopen threads without a single recovery owner or a complete join/shutdown barrier. Callback logging and thread-to-loop submission have failure paths that need isolation from real-time capture. The recorder performs synchronous FFmpeg pipe writes in an async path, and closing stdin does not verify process completion. These observations justify a P0 lifecycle task covering exclusive recording ownership, bounded buffering, stale callbacks, timeout/cancel cleanup and exit. Actual device-switch latency and driver behavior still require Windows tests; the audit does not prove a particular driver deadlock.

### A03 — Terminal outcomes and process supervision

**Confirmed by probe 6; supervision and send-loop findings are static.** An exception in [TaskHandler.loop](../../core/server/worker/task_handler.py) is logged without returning a terminal task failure or clearing its session. The [protocol](../../core/protocol.py) has audio/result messages but no explicit task error/cancel contract. Client waiting state has no general per-task deadline that guarantees an outcome.

[ProcessManager](../../core/server/worker/process_manager.py) checks ASR liveness while waiting for model readiness, but ongoing supervision focuses on the aligner. A live but wedged aligner can time out without being replaced. [ws_send](../../core/server/connection/ws_send.py) retries broad exceptions without a backoff/terminal policy; a permanently failing queue can repeatedly fail. Server tray shutdown also needs to schedule event-loop operations on the owning thread. P0 completion means each accepted task has one observable terminal outcome, bounded recovery and cleanup across worker failure, disconnect and shutdown. This does not imply every ordinary disconnect currently spins.

### A04 — Recognition text in default diagnostics

**Confirmed statically.** [TaskPipeline](../../core/server/worker/pipeline.py) logs raw model text at info level and formatted text at debug level; [ws_send](../../core/server/connection/ws_send.py) logs result content. Default diagnostic settings can persist these messages independently of transcript-history preferences. Turning off client transcript history therefore does not guarantee that recognition content is absent from diagnostic files.

Remove content from routine diagnostics before treating save switches as an adequate privacy boundary. Preserve useful task IDs, lengths, timings and error categories. User-visible recognition output and explicitly enabled content records are intentional data paths and must not be translated or removed by this work. Redaction is P0 and can ship before the broader logging redesign.

### A05 — Audio, result and engine contracts

**Confirmed by probes 7–9 and 12; Qwen behavior is a static adapter finding.** [Protocol parsing](../../core/protocol.py) validates many incoming audio fields, but accepts non-finite float samples. Result parsing accepts a string in `is_final`, non-finite duration and mismatched tokens/timestamps. Internal duration-to-byte conversion can produce a non-float-aligned slice for an otherwise accepted fractional duration.

[FileTranscriber](../../core/client/transcribe/file_transcriber.py) allows an in-flight window of one. With overlap enabled, the first chunk can be shorter than the server's threshold for producing a response; the sender then waits for credit before sending the chunk needed to reach that threshold. Defaults avoid this reproduction, but the accepted configuration deadlocks. Separately, the [Qwen adapter](../../core/server/engines/qwen_asr_gguf/asr_engine.py) truncates audio above its configured `chunk_size`; accepted client/server segment settings can exceed that limit. No real Qwen inference was run, and this does not explain the historical hallucination report.

P1 work should validate both protocol directions, sample-aligned slicing, pending task/result ownership and compatible flow-control/engine limits. Reject or split unsupported input explicitly instead of silently discarding audio. Add boundary regressions, including the safe defaults as controls.

### A06 — Output failure and content persistence

**Confirmed by probes 11 and 13; file-write races are static risks.** [TextOutput](../../core/client/output/text_output.py) restores the old clipboard after a delay even if the user copied new text in the meantime. [ResultProcessor](../../core/client/output/result_processor.py) performs output before saving records; an output exception skips the save path despite its independent setting. The last result remains in memory, so this is not proof that every recovery route loses the text.

File output uses existence checks before writes rather than exclusive reservation of an output set; competing processes can race and partial format failures can leave incomplete bundles. P1 work should preserve newer clipboard content, make enabled record saving independent of injection/broadcast failures, and give file output clear partial-failure/no-overwrite behavior. Cover these paths with mocked output failures and temporary files, without typing into real applications.

### A07 — Unlabelled estimated timestamps

**Confirmed by probe 10.** When an engine provides no timestamps and alignment returns no result, [TaskPipeline](../../core/server/worker/pipeline.py) assigns characters uniformly spaced timestamps. Exported tokens/timestamps do not identify that fallback as estimated. This permits plausible-looking but unaligned subtitles and later rebuilds.

P1 completion requires timestamp provenance/quality to survive protocol and JSON output, with a visible warning or an explicit policy for estimated SRT output. Keep usable text when alignment fails. Real alignment accuracy remains a separate validation task.

### A08 — Logging ownership and retention

**Confirmed statically; concurrent file corruption was not reproduced.** [Logger.setup](../../core/logger.py) reads `ClientConfig` for server diagnostic persistence too. Multiple server processes can open the same `server_latest.log`; its truncation has no shared process owner. [Diagnostic archives](../../core/log_archive.py) already provide bounded rotation, dated/process-specific files and scoped cleanup, so retention does not need to be invented from scratch.

The [file runner](../../core/client/manager/file_runner.py) adds a separate transcription log per batch/run, with a separate switch and no matching retention policy. Toast logging can reuse an existing logger; it is not invariably an independent backend. [DiaryWriter](../../core/client/diary/diary_writer.py) intentionally keeps transcript and LLM-action content separate from diagnostics.

The requested logging consolidation is justified by configuration coupling, sink ownership and inconsistent policies. Shared infrastructure need not mean one file or one save switch. Preserve content/audio independence and existing user data; test multiprocessing ownership, retention and save-switch combinations.

### A09 — Tk host shutdown

**Confirmed statically; no interactive shutdown failure was reproduced.** The recording indicator already posts work to the Toast manager's UI queue. The remaining concrete gap is lifecycle ownership: [ToastMessageManager](../../core/ui/toast_manager.py) starts a Tk thread, while application shutdown does not provide a complete stop/destroy/join path for that host. Quitting the main loop alone does not establish safe object destruction and rejection of late work.

A P1 task should close the existing host safely and settle task-owned status on timeout/cancel/exit. The audit found insufficient evidence to require a wholesale replacement of Toast infrastructure or a migration of numerous current notification callers. Separate that architectural preference from the demonstrated lifecycle gap.

### A10 — Release reproducibility and artifact safety

**Archive filtering confirmed by probe 14; other findings are static.** [zip_release](../../zip_release.py) admits diagnostic logs and transcript files when they exist in the distribution tree. This is a risk when packaging a reused/populated tree, not evidence that a published package contains private data. Build specs use junctions into the working tree, so safe packaging needs a clean, immutable staging boundary. The client-only spec also lacks the project LICENSE in its explicit copy list.

Runtime requirements are mostly unconstrained. [WebSocket connection setup](../../core/client/connection/websocket_manager.py) enables `proxy=None` for versions starting at 14, although automatic proxy support arrived in 15.0 according to the [official changelog](https://websockets.readthedocs.io/en/stable/project/changelog.html). Installed version 16.0 avoids this compatibility branch; version 14 was not executed in this audit. Include the supported dependency range in release tests.

[Release smoke](../../.github/workflows/release-smoke.yml) checks that combined-package EXEs exist; it does not launch them or cover the client-only artifact. P1 completion needs supported dependency/backend constraints, both artifact variants, artifact content checks, required licenses and clean-machine startup/upgrade evidence. Template-only configuration and provider-template protections already exist; retain them rather than reopening a claim that builds always copy developer credentials. Artifact privacy and launch checks are release gates, even while broader reproducibility work is incremental.

## Synthetic reproduction inventory

These are bounded audit experiments, not new permanent tests. The inputs and observed behaviors are recorded here so the evidence does not depend on retaining a local scratch script.

| Probe | Setup | Observed current behavior |
| --- | --- | --- |
| 1 | Request worker sessions for sockets A/B with the same task ID. | Same session object, still associated with A. |
| 2 | Queue A/B work with the same ID, then clean up disconnected A. | B's pending work is removed too. |
| 3 | Launch two shortcut instances with mocked recording workers. | Both launches succeed against one shared audio queue. |
| 4 | Make the stream-ready event wait time out. | Global and shortcut recording flags remain true. |
| 5 | Deliver a callback carrying an old readiness event. | Audio is still queued. |
| 6 | Raise a synthetic pipeline exception during the worker loop. | No terminal response; session remains. |
| 7 | Parse aligned float32 audio containing NaN. | Audio validation accepts it. |
| 8 | Parse a result with string `is_final`, NaN duration and unequal token/time arrays. | Result validation accepts it. |
| 9 | Connect real sender/receiver logic with mocked transport/decoder: duration 1 s, overlap 0.2 s, in-flight limit 1. | First chunk produces no server task; next send waits for unavailable credit. |
| 10 | Finish a file task with a non-timestamp engine and an aligner returning `None`. | Uniform timestamps appear without an estimated-quality label. |
| 11 | Change mocked clipboard content during the paste restoration delay. | Restoration overwrites the new copy. |
| 12 | Use accepted segment duration 0.10002 s and zero overlap. | Computed slice is 6,401 bytes, invalid for float32 decoding. |
| 13 | Make output injection raise while record saving is enabled. | The independent save method is not reached. |
| 14 | Pass diagnostic-log and transcript paths to the archive inclusion predicate. | Both are included. |

## Requested features and priority decisions

| Request | Current evidence | Decision |
| --- | --- | --- |
| Multilingual UI | Inline English menu labels coexist with Chinese status/dialog text; no shared locale catalog covers the product. | Retain as a user-selected P1 feature. English/Simplified Chinese are a proposed initial pair, not a newly confirmed requirement. |
| English internals | Maintained comments, developer documents and diagnostics mix languages. | Retain as a user-selected P1 standard, migrated incrementally. Preserve user text, language fixtures and upstream/history records. User-facing terminal messages belong to UI policy. |
| LLM cost estimates/alerts | The provider returns text, discards usage metadata, and has no price/budget model. | Retain as a user-selected P1 feature. Usage plumbing precedes estimates/alerts; rates, currencies and thresholds are design choices, not audited provider billing facts. |
| Logging consolidation | A08 establishes shared-file ownership, configuration coupling and retention inconsistencies. | Retain as P1 infrastructure work; ship A04 redaction independently at P0. |

P0 means an existing isolation, recording, task-completion or diagnostic-privacy failure that should be addressed before broader rollout. It does not mean the audit demonstrated frequent failures in ordinary single-user use. P1 combines requested features and evidenced hardening; it is not a promise to implement every item in one release.

Strict offline mode remains an optional P2 safeguard. The implemented local ASR mode restricts server listening and does not promise to block independently enabled cloud LLM/UDP traffic. Default UDP control is disabled and loopback-bound; configuring a non-loopback listener currently permits unauthenticated commands, so wider exposure needs an explicit policy. Check actual endpoint addresses, not provider labels, when defining offline behavior.

The audit retains existing local/LAN authentication, message/resource caps, scheduler FIFO/fairness tests, static LLM configuration, opt-in caret context, bounded provider responses/deadlines, scoped archive cleanup and template-based packaging protections as completed capabilities. They are not generic unfinished backlog items. UI replacement was narrowed to A09; release verification was raised from P2 to P1. New entries separate terminal failures from redaction and add protocol/media contracts, persistence and timestamp quality. The active list grows from 12 to 16 outcomes without restoring the old task inventory. Unselected product ideas remain scope choices, not features proven unnecessary or already implemented.
