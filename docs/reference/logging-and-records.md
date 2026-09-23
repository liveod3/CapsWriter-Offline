# Logging and record management

This is the configuration and data contract for schema 2.7. The [user guide](../user/logs-and-records.md) provides concise Chinese instructions. Defaults describe tracked templates, not an existing installation.

## Ownership and directories

| Category | Owner | Default destination | Format |
| --- | --- | --- | --- |
| Client diagnostics | Each client process | `logs/client/YYYY/MM/client-<run_id>.jsonl` | One JSON object per event |
| Server diagnostics | Each server, ASR and aligner process | `logs/server/YYYY/MM/server-<run_id>.jsonl` | Same schema, independent files |
| Dictation and successful LLM content | Client record writer | `records/transcripts/YYYY/MM/DD.md` | Human-readable Markdown |
| Microphone audio | Client audio writer | `records/audio/YYYY/MM/` | MP3, or WAV fallback |

`run_id` contains launch date/time, PID and a random suffix. It identifies one process sink, not the whole client/server installation. Date directories describe session creation; crossing midnight does not move an open diagnostic file. Record timestamps include date, milliseconds and local UTC offset. Markdown days follow recording start time.

No process shares an active diagnostic file with another process. The ASR/aligner use server configuration, including in the detached worker startup snapshot. Client configuration remains in that snapshot for existing UI-language compatibility, not server log persistence. A client-side helper may own an additional client process log when it emits diagnostics.

`core.logger` configures one bounded local writer queue per logger/process and a separate localized console handler. File creation, formatting, writes, rotation and periodic cleanup run in the diagnostic writer except initial directory/lease setup. Existing package imports still initialize their logger; importing a package can create a session lease. There is no latest-log duplicate, no transcription-specific file handler and no log-forwarding process.

Startup directory failure leaves console feedback available. The file handler reports write failure once without echoing the failed message or exception payload. Queue capacity is 256 records; full queues drop new records rather than block the producer. Later writes report the drop count. Normal shutdown requests a drain and waits up to two seconds for the writer; forced process termination can lose buffered events. This is best-effort diagnostics, not durable accounting.

## Diagnostic settings

Both `ClientConfig` and `ServerConfig` own these fields, except the client-only reference switch. Every diagnostic setting requires a process restart; worker replacements retain the server startup snapshot.

| Field | Template default | Contract |
| --- | --- | --- |
| `save_diagnostic_logs` | `True` | Gate all project diagnostic file sinks for that side; console/product output remains |
| `diagnostic_log_dir` | `'logs'` | Root; append `client` or `server`; application-relative, absolute, environment and home expansion supported |
| `log_level` | `'DEBUG'` | Minimum file severity; DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `diagnostic_include_text` | `False` | Include explicit ASR/LLM/final-text copies in INFO content events; independent of user history |
| `diagnostic_include_context` | `False` | Client only: include the actual LLM caret reference; requires text diagnostics and no additional capture |
| `diagnostic_text_max_chars` | `16000` | Per field; integer 1..65536; retain original character count and truncation flag |
| `diagnostic_log_retention_days` | `30` | Age of inactive session families based on last file modification; 0 disables age expiry only |
| `diagnostic_log_file_mb` | `10` | Positive integer MiB rotation target per process file; a large individual record may exceed it |
| `diagnostic_log_backups` | `5` | Positive integer count of rotated segments per session |
| `diagnostic_log_budget_mb` | `200` | Positive integer soft budget per component directory, including active segments |

Legacy `file_separate_log` is accepted but ignored. A file batch uses the already-configured client sink, and cannot override disabled diagnostic persistence. Old `client_latest.log`, `server_latest.log`, `diagnostics` and `transcribe` trees are not new-write targets.

## Event schema and severity policy

Every JSON object has `timestamp`, `level`, `component`, `run_id`, `pid`, `process`, `thread`, `event`, `source` and `message`. IDs, `data` and `content` appear only when supplied by the source. IDs are opaque values; server task identity must include both `socket_id` and `task_id`.

Existing localized `Notice` events use their stable message ID as `event` and their English wording as `message`. Legacy free-form/native records use `python.message`; many older counters remain in the message rather than separate numeric fields. Do not claim all legacy events already have task IDs or a uniform metric schema. Exception details retain type and stack locations, excluding exception-message/local-variable dumps from the file formatter.

| Severity | Source policy | Existing examples |
| --- | --- | --- |
| DEBUG | Internal transitions/counters useful for diagnosis; avoid per-sample logging | Recorder task creation; stream lifecycle detail; merge counts; formatted input/output lengths |
| INFO | Successful milestones, task summaries, bounded explicit content snapshots | Model ready; ASR started/decoded/completed; LLM preparation/completion/cancellation |
| WARNING | Recoverable degradation or fallback; action remains usable | LLM error with original-text fallback; alignment fallback; repeated aligner exits |
| ERROR | A requested operation failed | Recognition pipeline failure; transcript archive failure; rejected file task |
| CRITICAL | An unrecoverable service-wide failure | Reserved for service-fatal outcomes; not every component currently emits this level |

Minimum severity includes more severe levels. Severity does not grant permission to capture content. INFO content events therefore require both persistence/content switches and an INFO-or-DEBUG threshold. ERROR messages never gain private payloads merely because they describe an error.

Console diagnostics normally start at WARNING. `console_handled` prevents duplicate terminal feedback for tasks that own their UI. Content events never enter the standard console handler. Product transcription previews and progress are independent UI outputs. Third-party/native messages retain their original diagnostics; they are not guaranteed to follow every project event field.

## Structured pipeline events and text copies

| Event | Location | Identity and payload |
| --- | --- | --- |
| `file.batch_started`, `file.batch_finished` | Client | `batch_id`; same sink as all client events |
| `asr.segment_started` | Server | `task_id`, `socket_id`; source and offset in `data` |
| `asr.task_finished` | Server | `task_id`, `socket_id`; final character count |
| `llm.request_started` | Client | `request_id`; preset, provider ID, model, input/reference character counts |
| `asr.decoded_text` | Server, text opt-in | Raw decoded segment text |
| `asr.final_text` | Server, text opt-in | Merged ASR text before formatting and final formatted text |
| `dictation.asr_text` | Client, text opt-in | Received ASR text and `task_id` |
| `llm.request_text` | Client, text opt-in | Prepared LLM input and system prompt; reference only with context opt-in |
| `llm.response_text` | Client, text opt-in | Successful provider result and `request_id` |
| `dictation.final_text` | Client, text opt-in | Final/fallback text, task and available request IDs, before insertion |
| `file.final_text` | Client, text opt-in | File recognition text before output serialization |

Each `content` field has `{text, chars, truncated}`. JSON escaping prevents embedded newlines from creating fake physical records. The bounded allowlist accepts text snapshots, not authentication headers, provider configuration objects, API keys, endpoint URLs or audio bytes. User-authored text may itself contain sensitive material; this is an explicit content archive when enabled.

Request-preparation events do not prove the HTTP request reached a provider: local credential checks can still reject it. The input is the selected/trigger-stripped transcript and constructed system prompt. Context is the reference included in that request payload (currently bounded to 3500 characters). Capture permissions do not enable reference retrieval, LLM routing or extra network calls. No clipboard, selection, history or additional UI reads are introduced.

The server has ASR data but not client LLM messages. A client copy does not suppress a server copy. Correlate client/server by task, server connections by socket/task, and LLM stages by request ID; not all old event messages carry structured IDs. The final client content event connects task and request IDs when text diagnostics are enabled.

## User records and switches

These client fields apply after the active dictation/ASR/LLM/output/archive work finishes, or between file tasks. No user-record expiry is introduced.

| Field | Template default | Meaning |
| --- | --- | --- |
| `save_transcripts` | `True` | Save final dictation text |
| `transcript_dir` | `'records/transcripts'` | Shared daily Markdown destination for dictation and successful action records |
| `transcript_save_original` | `False` | Add differing ASR text only when transcript saving is enabled |
| `save_llm_records` | `False` | Add successful action input, output, system prompt, preset and request ID |
| `save_llm_context` | `False` | Add actual sent reference only when action records are enabled |
| `save_audio` | `False` | Save microphone audio independently |
| `audio_dir` | `'records/audio'` | Application-relative or explicit path; `''` preserves the legacy per-user default |
| `audio_name_len` | `20` | Leading ASR characters in final audio filename, 0..200; 0 omits text |

One task appends at most one history entry even when both transcript and action saving are enabled. If only action saving is enabled, only successful processed actions are archived. The final text appears once; identical LLM output is not repeated in a second block. The entry includes recording time, task ID, outcome (`completed`, `fallback`, `cancelled`), optional original/action/prompt/reference fields and an audio link when a file exists. Outcome describes text processing, not verified insertion into the target application.

Archive saving occurs before text insertion and UDP output, so insertion failure does not skip the enabled record. Disk/archive failure is reported without discarding usable text. The daily Markdown writer uses a thread lock and a cross-process file lock with a two-second acquisition limit. An adjacent `.lock` file is synchronization metadata, not another content copy. There is no claim of transaction-level durability after power loss.

Audio uses existing streaming ownership: FFmpeg MP3 at 192 kbit/s, or 16-bit WAV when unavailable/startup fails. Existing audio paths and links remain valid. Paths are never moved merely because a setting changes. LLM cost accounting remains its independent monthly SQLite store; file TXT/SRT/JSON outputs remain beside their source output destination. Neither is subject to diagnostic expiry.

## Rotation, retention and safe cleanup

Rotation appends `.1` through the configured backup count. Each live session holds an OS file lock on a same-stem `.lock` file. Cleanup only considers exact owned session filenames beneath the component's year/month directories and acquires the session lease before deleting. Symlinks/reparse points and paths outside the resolved component root are skipped. Unknown filenames, the current session, other active processes, records/audio and legacy layouts remain untouched.

Cleanup runs on the writer's first event and at most hourly thereafter while events continue. It expires old inactive session families, then removes oldest inactive families until the soft component budget is met. Active files count toward usage but are not removed, so the budget is intentionally soft. Rotation still bounds a long-running process independently of age cleanup. Closed processes release and normally remove their lease file; cleanup can reuse a stale crash lease.

## Viewing, configuration compatibility and validation

`scripts/read_logs.py` streams JSONL as readable text or filtered JSON, supports minimum severity/exact task/exact request filters, and hides explicit `content` unless `--content` is supplied. It does not sort all processes into a global timeline, and an ID filter excludes legacy rows without that ID. Malformed records are reported by file and line without echoing their raw payload. Rotated segments can be read too.

Configuration templates are grouped by user purpose, with comments explaining defaults, side effects and reload/restart boundaries. Existing local expressions are preserved during a reviewed configuration merge, including custom model paths, shortcuts and credentials. Missing new fields use safe defaults; server persistence no longer inherits the client's save flag. No automatic legacy data migration or deletion runs at application startup.

Tests use synthetic content, mock transports, temporary directories and spawned Python writers. They cover independent switches, content truncation, console exclusion, credentials, task identity, process ownership, queue overflow, rotation, active-session protection, expiry/budget cleanup, write failures, reference snapshots, record preservation before insertion and configuration reload. See [validation evidence](../validation/P1-logging-records.md) for results and remaining manual scope.
