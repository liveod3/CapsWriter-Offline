# P1 logging and record management

Accepted by the user on 2026-09-23, with commit explicitly authorized. This work follows the user's explicit permission to keep diagnostic text copies alongside user-readable records, with documented independent controls. Acceptance covers the implementation and automated validation described here; the remaining live/hardware checks below were not performed.

## Implemented scope

- One buffered JSONL sink per process under client/server directories; no shared latest-file truncation and no separate file-transcription sink.
- Independent client/server persistence, DEBUG-by-default templates, explicit bounded text/reference opt-ins, localized console feedback and content-free file-write failure handling.
- Session rotation, age expiry, component storage budgets, exact owned filename matching and active-session file locks. No cleanup of user records, audio, legacy directories or unknown files.
- One Markdown task entry for transcript/action records, request/task identifiers, optional original/prompt/reference fields, cross-process append locking and saving before insertion/UDP failures.
- Reorganized schema 2.7 templates and local main configuration sections, expanded setting comments, a concise Chinese user guide, an English event/setting contract and a read-only log viewer.

## Validation

Environment: existing Conda `capswriter`, Python 3.11.15, invoked directly after the noninteractive shell could not find Conda. No dependency installation or model download.

- Default suite with configured coverage: **696 passed, 8 deselected**, 85.22% configured coverage.
- New diagnostic/record cases include independent persistence/text/reference switches, console exclusion, bounded text copies, credential exclusion, malformed-reader input, structured socket/task identity, spawned writers, concurrent daily-history appends, rotation, expiry, budget cleanup, active-session protection, full queues, disk failures, reference snapshots and insertion failure.
- Focused lifecycle/configuration run: 51 passed before the final default suite.
- Syntax compilation, configured Ruff and mypy checks passed. Internal-language check: 312 files, zero violations. Documentation check: 51 pages, zero violations. Final diff checks passed.
- Temporary test/cache data used an ignored workspace development directory, outside runtime logs. Tests used synthetic text/audio and mock transports; no microphone capture, key simulation, cloud LLM calls, GPU commands or packaging run.

The first full run exposed a timing-test mock shared with the new background writer. Diagnostic retention and file-lock clocks now retain their own monotonic function reference, so mocking a worker clock cannot consume a finite worker-test clock sequence. The subsequent full run passed.

## Local configuration and old data

Existing local assignment expressions were compared as syntax trees and preserved, including model paths, shortcuts, credentials and audio directory. The legacy `logs/transcripts` destination was deliberately redirected to `records/transcripts`; old records were not moved. Schema version became 2.7, new diagnostic text switches were enabled locally under the user's authorization, and reference capture switches remain off. Public templates keep text/reference capture off. Local settings/backups are ignored and not part of the change set.

Old validation temporary files (2,606 files) and old per-run transcription logs (17 files) were moved into an ignored sibling `old-logs-<timestamp>` archive after workspace containment, reparse-point and exclusive-read checks. No content was read for that inventory. No historical files were deleted.

The old client/server latest files initially could not be opened exclusively and were left in place without terminating running processes. During the user's follow-up cleanup review, exclusive checks succeeded: the two latest files, 627 old diagnostic files and four text-action records were moved into the same sibling archive. The empty, unreferenced `hf_download.log` was removed. The old `logs/transcripts` history remains in place. Archived Markdown keeps the same directory depth for relative audio links. Restart the client and server after review to use the new diagnostic ownership and configuration schema.

The two pre-existing user edits in recording-ownership and multilingual-interface validation documents were not modified by this work.

## Limits and follow-up

- Real microphone/UI interaction, native model behavior, cloud providers and frozen-package behavior were not exercised. Restart and a user-driven dictation/file task remain the manual check.
- Diagnostic persistence is best-effort: full queues, unavailable disks or forced process termination can lose records. Storage budget is soft while sessions are active; JSONL records can exceed a rotation target individually.
- Legacy metadata events still contain some fields inside English messages; the new pipeline events have structured identifiers. The viewer does not impose cross-process global chronological order.
- Text diagnostics intentionally contain private content when enabled; native backend messages retain their own diagnostic wording. This does not authorize additional capture or credential logging.
- The separate P1 output-persistence item remains open for clipboard restoration and competing/partial file exports. This change covers saving enabled dictation history before insertion/broadcast.

See the [user guide](../user/logs-and-records.md), [technical contract](../reference/logging-and-records.md) and [active TODO](../../TODO.md#logging-and-record-management).
