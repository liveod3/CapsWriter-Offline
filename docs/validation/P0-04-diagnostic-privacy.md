# Recognition content in routine diagnostics

Status: accepted by the user on 2026-09-22 after self-review and the manual runs
recorded below. The user authorized committing this complete item and explicitly
instructed the agent to stop afterward, without starting the next item.

## Outcome and boundaries

Content isolation is applied at the identified application logging sources:
recognition/formatting text, alignment tokens, transcript-derived filenames and
content-bearing errors in recognition, alignment, transport, clipboard and file
output. These records retain task IDs, lengths, timings and error categories.
Uncaught ASR/aligner entry failures exit with code 1 without printing exception
chains. Unexpected batch failures return failure, preserving the client CLI's
nonzero exit behavior.

Native llama.cpp diagnostics are forwarded as text through the existing callbacks,
with correct severity and literal rendering. No message classification layer,
blanket replacement or new diagnostic mode is added. The temporary
`core/log_metadata.py` helper has been removed. This does not claim that arbitrary
third-party messages are content-free: a confirmed content leak must be addressed
at its specific source, rather than deleting all native diagnostic explanations.

Normal text injection, the client's `Transcription:` / `LLM action:` result
preview, selected input/output paths in the file UI, exported TXT/JSON/SRT and
explicitly enabled transcript/LLM records remain product output. Disabling records
does not hide the intentional result preview. Redirecting the entire terminal to a
file also captures that preview; it is not the application's diagnostic sink.

Changes apply to new diagnostics. Existing logs and user records are preserved;
local configuration and credentials are unchanged. The user authorized a scoped
review of the manual run's diagnostic logs, recorded below. This item does not
reorganize logging ownership, retention or configuration.

## Changed areas

- Server: `worker/pipeline.py`, formatter/merger messages, `connection/ws_recv.py`,
  ASR/aligner process boundaries, model loading and alignment error forwarding.
- Engines: the four existing `llama.py` callbacks forward native text with corrected
  severity and literal rendering. Alignment fallback and FunASR initialization
  errors omit content. Paraformer ignores an unsupported language override without
  echoing it.
- Client: batch/file/subtitle diagnostics, audio file registration and renaming,
  WebSocket error conversion, clipboard and UDP output failures.
- Regression evidence: `tests/unit/test_diagnostic_privacy.py`.

## Automated evidence

Environment: existing Conda `capswriter`, Python 3.11.15.

- Default suite: **446 passed, 2 deselected**; configured coverage **84.79%**.
- Ruff: configured project targets pass. Mypy: seven selected source files pass.
- Compileall: entries, templates, `core`, `LLM`, tests and existing local configs
  pass. `git diff --check` passes.
- Thirty-four new synthetic cases cover application content isolation and native
  diagnostic forwarding. Application cases inspect latest, monthly, per-run and
  Rich console handlers at DEBUG level: synthetic transcript, output, prompt,
  caret reference and credential markers must not appear. Native callback cases
  separately require original diagnostic text, correct severity, literal brackets
  and percent signs, UTF-8 replacement and blank-message suppression. INFO does
  not reach a WARNING-level console, while warning/error explanations do.
  Record tests cover all four transcript/LLM-record switch combinations.
- Covered failure paths include punctuation/ITN, ASR/aligner startup/request/
  cleanup, remote alignment errors, native callbacks, LLM provider errors,
  WebSocket closure/transport errors, audio renaming, clipboard, file batches and
  subtitle rebuild. Native callback and alignment functions are executed from
  their source with synthetic inputs, without importing DLLs or loading models.

No real microphone, GPU/model inference, cloud provider, input injection or release
build was run. Synthetic checks do not establish the behavior of every third-party
native library or a packaged application; verify the actual configured backend
during the following manual acceptance.

## One manual acceptance

The [ggml_log_level definition](https://github.com/ggml-org/llama.cpp/blob/master/ggml/include/ggml.h)
defines DEBUG=1, INFO=2, WARN=3 and ERROR=4. NONE, continuation and unknown values
are forwarded at DEBUG without maintaining shared cross-thread severity state.
All four callbacks are tested with 120 INFO records followed by visible warning
and error explanations. The eight callback checks failed against the former
byte-count replacement and pass with direct text forwarding.

1. Restart client and server to load the changes. Use a distinctive non-private
   test sentence for ordinary dictation and a short file transcription. Confirm
   normal text output and exported files. The server should show progress and
   metadata without application-generated intermediate/final recognition text;
   the client result preview should still show the intended output. Native
   warnings must now show their actual explanations instead of only a byte count.
2. Inspect entries created during that run in `logs/client_latest.log`,
   `logs/server_latest.log`, `logs/diagnostics/YYYY/MM/` and, when enabled,
   `logs/transcribe/YYYY/MM/`. The test sentence, prompt/reference text and any
   transcript-derived audio/output filename must be absent. Judge new entries;
   old content may still exist in retained files.
3. With transcript and LLM records disabled, confirm no new content archive is
   written. If you normally enable those records or saved audio, restore your
   settings and confirm records/audio filenames still work while diagnostics
   stay content-free. Automated tests cover switch combinations and injected
   failures; no real API request or destructive failure is required for acceptance.

Earlier manual evidence on 2026-09-22: the user exercised one dictation and one
file transcription after the severity fix, then authorized inspection of the
corresponding local diagnostic logs. The scoped latest entries, monthly archives
and per-run file log show successful dictation/LLM completion and successful file
output, with no ERROR/CRITICAL records or observed transcript/prompt echoes. The
startup INFO-as-ERROR regression did not recur. No private text, filenames or log
copies are included in this record. This run preceded restoration of native
diagnostic text; the subsequent run below covers that final change.

The run also showed repeated native warnings during model loading, high dedicated
GPU-memory pressure and ten normal idle aligner replacements. The local and
template `aligner_idle_timeout` values are both 1 second; each file segment loaded
an aligner that subsequently exited on that idle timeout. These are normal idle
exits, not observed worker crashes. Those earlier byte-count records cannot reveal
their original warning messages, and GPU pressure alone does not establish memory
swapping. No model, timeout or memory settings were changed during this log review.
After native text forwarding was restored, the user restarted the server and
repeated dictation and file transcription on 2026-09-22. The authorized review of
that run's latest, monthly and per-run logs confirms both completed, with no
ERROR/CRITICAL records and no obsolete byte-count wrapper messages. The supplied
dictation sentence and its constituent clauses were absent from the diagnostic
logs; the intentional client transcription/LLM preview remained visible. This is
run-specific evidence, not a claim about all possible third-party log payloads.

Native warnings now identify the actual settings: direct I/O disables mmap, and
the configured context windows (ASR 2048, aligner 3072) are smaller than their
training windows (65536, 8192). These warnings do not by themselves report model
load failure or actual input truncation. The file run again caused ten normal
idle aligner replacements and high GPU-memory warnings. No changes to model
loading, context capacity or timeout settings were made as part of this review.
Successful diagnostic outcomes do not establish transcription accuracy beyond
the user's reported manual scope.

Acceptance result: the user accepted the item as complete and authorized commit
on 2026-09-22. No broader hardware, model or release certification is implied.
