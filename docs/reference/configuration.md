# Recording storage and configuration reload

## Recording storage

`ClientConfig.save_audio` remains opt-in. `ClientConfig.audio_dir` selects where
new microphone recordings are stored:

| Setting | Location |
| --- | --- |
| `''` (default, including old configurations without this field) | `%LOCALAPPDATA%/CapsWriter-Offline/audio` on Windows |
| `'audio-data'` | `audio-data` inside the application folder; useful for portable installations |
| `r'D:\DictationAudio'` | An explicit directory, including another drive |

Paths support environment-variable expansion and `~`. Relative paths resolve
against the application folder, not the shell's working directory. If
`LOCALAPPDATA` is absent, the default is `~/.local/share/CapsWriter-Offline/audio`.
The new layout is `<audio_dir>/<YYYY>/<MM>/<recording>.mp3` (WAV without FFmpeg).
The date comes from the start of the recording. The default stays writable for a
normal Windows user even when the application is installed in a protected folder.
Other application data and configuration locations are unchanged.

Choose a writable folder yourself for portable installations. An explicit path
does not silently fall back to another location. If directory/file creation fails,
the client reports that audio could not be saved and continues recognition without
an audio archive for that task. Existing encoder/write failures retain their
bounded failure handling. No automatic retention/deletion is added.

The client tray's **Open recordings** action opens the currently effective folder.
Changes to `audio_dir` and `save_audio` wait for pending dictation tasks to finish,
including LLM work and archive writes.

### Legacy policy

Existing `<application>/<YYYY>/<MM>/assets/` files and Markdown are left in place.
There is no automatic migration, copy, deletion or search redirect. Existing links
continue to resolve at their original locations. Open those folders directly for
older audio; **Open recordings** opens only the new configured destination.
Changing `audio_dir` does not move previously saved files from any destination.

New transcript links use relative paths on the same drive and `file:///` URIs
across Windows drives. Some Markdown viewers restrict local-file links; open the
target in Explorer in that case. When moving a portable installation to another
computer, include its old year folders and transcript directory. If using the
per-user default, copy that audio separately and update paths/links deliberately.

## Reload behavior

Start the updated client/server once to load this implementation. Thereafter,
each running process reads its own `config_client.py` or `config_server.py` once
per second off the event loop. Two consecutive identical reads are required
before validation, so changes normally take about one to two seconds to prepare.
No model, microphone, GPU command or network resource is recreated by reload.

The complete candidate is parsed and type/range checked before any live setting
is published. Invalid syntax, unsupported expressions, duplicate/unknown fields,
invalid values and removal of previously present fields reject the candidate.
The previous effective configuration remains in use. A new edit supersedes any
pending candidate; partial files are never merged into the effective settings.
Older configurations may omit newly added settings and use template defaults.
To restore a default during editing, assign its value instead of deleting a field.

Reload accepts the declarative Python used by the shipped templates: assignments,
literals/containers, basic arithmetic, `Path` composition and the template's
environment/path lookups. It does not execute edited imports, functions, loops,
class methods or arbitrary calls. Custom executable Python configurations remain
startup-only; simplify them to declarative settings to use reload. Model argument
classes are validated and tracked for restart notifications as well.

The console and diagnostic log report:

- `Configuration validated; waiting for a safe task boundary`: prepared but not applied.
- `Configuration applied`: effective for subsequent client tasks.
- `Configuration applied to new tasks`: effective for new server task admissions.
- `Configuration reload rejected; last valid settings retained`: fix and save again.
- `Restart required (resource settings unchanged)`: named settings remain at startup values.

Messages identify field names, never configuration values or credentials. A valid
candidate containing both live and restart-only changes applies the live subset
and reports the restart subset explicitly. Restart-required values stay on disk.

### Client settings supported without restart

| Group | Fields |
| --- | --- |
| Interface | `ui_language` (`auto`, `en`, `zh-CN`); see [interface language](../development/localization.md) |
| Audio archive | `save_audio`, `audio_dir`, `audio_name_len` |
| Transcripts and action records | `save_transcripts`, `transcript_dir`, `transcript_save_original`, `save_llm_records` |
| Recognition | `language`, `mic_seg_duration`, `mic_seg_overlap`, `file_seg_duration`, `file_seg_overlap` |
| Task deadlines and file window | `mic_io_timeout`, `mic_result_timeout`, `file_io_timeout`, `file_result_timeout`, `file_max_inflight_chunks` |
| Text output | `paste`, `restore_clip`, `paste_apps`, `enter_apps`, `trash_punc`, `trash_punc_thresh`, `trash_punc_apps`, `traditional_convert`, `traditional_locale` |
| LLM selection | `llm_enabled`, `llm_correction_enabled`, `llm_translation_enabled`, `llm_default_preset` |
| Caret context | `caret_context_enabled`, `caret_context_before_chars`, `caret_context_after_chars` |

Microphone mode publishes only when all capture, upload, pending ASR, LLM, output
and archive work has settled. A continuous stream of tasks can postpone reload;
finish pending work to allow it. Publication shares the recording admission lock.
File mode publishes between files, after the previous file's cleanup. CLI output
formats and input scanning remain fixed for the current invocation.

All other client settings require restart: shortcut bindings/thresholds, input
device, idle suspension, ASR connection/TLS/authentication, WebSocket limits,
UDP controls/output, tray/logging resources, LLM directory/cancel key, file
discovery/output defaults and per-run logging. These are resource recreation or
invocation settings; the watcher reports their names and leaves resources intact.

Tray LLM toggles save to the same local configuration and follow the same task
boundary. Their effective checkmarks update after reload. Provider/preset TOML
files keep their existing per-request loading behavior: each request uses its
loaded catalog; an invalid catalog causes that action to fail with original
transcription retained. Python reload does not import legacy `LLM/*.py` or send
test requests to providers.

### Server settings supported without restart

The local server follows `ClientConfig.ui_language` at startup and after validated
edits to `config_client.py`, including the client's language menu. It observes a
detached configuration snapshot and applies only the language field. The server's
own `ui_language` remains a fallback for legacy client configurations without that
field. Other installations and remote clients do not change it over the network.
Server terminal/tray and worker notices follow the selected language; diagnostic
file records keep their stable English wording shown above.

`format_num` and `format_spell` update for new tasks. On the first audio message,
the server copies both settings into the connection-scoped audio cache and every
internal queued fragment. The worker uses that snapshot even if the file changes
before inference/final output. Existing connections can submit new tasks using
the updated values. The external WebSocket protocol is unchanged.

All other server settings require restart, including model paths/arguments,
network mode/address/port/authentication/TLS, queue and message limits, process
timeouts, aligner lifetime, GPU management/monitoring, logging and tray resources.
Restart only after tasks complete. Reload never interrupts an active task or
unloads a backend.
ASR and replacement aligner processes inherit a detached startup configuration
snapshot before importing server modules. They do not reread a partially edited
configuration or silently adopt pending model/resource changes.

Manual acceptance: [P1 validation checklist](../validation/P1-recording-storage-config-reload.md).
