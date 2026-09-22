# P1 recording storage and configuration reload

Status: accepted by the user on 2026-09-22; approved for commit.
The user reported no material issues after the manual handoff. This records
acceptance of the feature scope, without claiming that every checklist scenario,
hardware combination or release environment was individually verified.

## Manual acceptance

Restart the updated source client and server once. Keep their consoles visible.
Use short non-private test dictations. Preserve your preferred configuration values
and restore temporary test settings afterwards.

1. Set `save_audio = True`, `audio_dir = ''` in `config_client.py`. Save, wait for
   `Configuration applied`, then dictate. Use **Open recordings**: the new file
   should be in `%LOCALAPPDATA%/CapsWriter-Offline/audio/YYYY/MM`. Play it and
   follow its link from the saved transcript. No new audio should appear under
   a year directory directly in the application folder.
2. Set `audio_dir = 'audio-data'`; repeat without restarting. Then try an absolute
   directory containing spaces, preferably on a second drive. Check playback and
   the transcript link. Verify an older recording and its old link still work.
3. Start a dictation and save a different `audio_dir` and `save_audio = False`
   while recording. Finish and wait for ASR/LLM/output. The in-flight recording
   should retain its original destination; later dictation should save no audio.
4. Set a destination beneath an existing ordinary file, or a directory you know
   is unwritable, and enable saving. Dictation should continue with an explicit
   storage failure message, without silently saving elsewhere. Restore the path.
5. Edit `language`, `trash_punc_thresh` or transcript options while idle. Observe
   application without restart. Repeat during recording and while a result/LLM
   is pending: wait-for-boundary feedback should precede application. If using
   batch transcription, change a supported setting during the first file and
   verify it applies after cleanup, before the next file.
6. Save an incomplete assignment such as `save_audio =`, then an invalid value
   such as `save_audio = 'yes'`. The console should reject both, preserve current
   behavior and remain quiet until another edit. Restore valid values and verify
   recovery. Removing an existing field should also be rejected; restore it.
7. Change a restart-only client field (for example `input_device` or `port`) along
   with a live field. Only the live field should apply. The console should name
   the restart requirement and the microphone/connection should remain intact.
   Restore the resource value before any later restart.
8. Save changed server `format_num`/`format_spell`; compare subsequent dictations
   containing Chinese numbers and mixed Chinese/English text. During a long file
   task, change them again: that task should keep its original formatting and a
   newly submitted task should use the new settings. No model reload should occur.
9. Change a server port or model argument, and try invalid server syntax/values.
   Check restart/rejection feedback while existing tasks continue. Restore these
   values before restarting. Check no credential/configuration values appear in
   the reload messages.
   In particular, after an aligner idle exit, submit another file while an invalid
   server edit is still on disk: the replacement worker should use its retained
   startup configuration and continue normally.
10. Toggle a tray LLM option while a task is pending. It should save immediately
    and become effective after the task finishes. Existing Provider/preset TOML
    edits should still affect the next LLM request. Only run actual provider calls
    if you intend to use that provider. Finally exit both processes, including
    once with a pending edit, and verify normal bounded cleanup.

## Automated evidence

Environment: existing `capswriter` Conda environment, Python 3.11.15 on Windows.
Final default suite: **472 passed, 2 deselected**, with **85.13%** coverage of the
repository's configured coverage modules (50% required; not whole-repository coverage).
Ruff, the repository's seven-module mypy gate, compileall (including local
configurations) and `git diff --check` passed. The default suite and coverage
gate were run with an isolated pytest temporary directory and coverage data file;
the normal temporary/cache directories had inherited permission restrictions.

Tests use isolated temporary directories, synthetic samples and mocks. They do
not access a real microphone, provider or model. See `tests/unit/test_config_reload.py`
and `tests/unit/test_recording_storage.py`; existing audio, connection/task isolation
and shutdown regressions are included in the default suite.
`tests/unit/test_worker_configuration.py` additionally exercises real Windows
spawn with an invalid disk configuration, detached snapshot values and replacement
aligner configuration retention. Root local configurations parse successfully;
only the missing `audio_dir = ''` field was added locally, preserving all existing
user values. The pre-existing caret-context TODO entry is preserved.

The automated checks do not certify real model behavior, Windows playback/link
handling, protected directories, multi-drive paths or interactive tray behavior.
The user accepted the feature scope after the manual handoff; individual checklist
results were not itemized. Release packaging and clean-machine installation have
not been exercised in this item.

## Changed files

- Configuration engine/bootstrap: `core/config_reload.py`, `core/worker_bootstrap.py`,
  both `config_templates/config_*_template.py` files, `start_server.py`.
- Client integration: `core/client/app.py`, `core/client/manager/file_runner.py`,
  `core/client/manager/tray_manager.py`.
- Recording/archive handling: `core/client/audio/storage.py`,
  `core/client/audio/file_manager.py`, `core/client/audio/recorder.py`,
  `core/client/diary/diary_writer.py`.
- Server integration: `core/server/app.py`, `core/server/connection/ws_recv.py`,
  `core/server/schema.py`, `core/server/formatter/text_formatter.py`,
  `core/server/worker/pipeline.py`, `core/server/worker/process_manager.py`.
- Tests: new `test_config_reload.py`, `test_recording_storage.py`,
  `test_worker_configuration.py`; adapted `test_audio_file_lifecycle.py` and
  `test_connection_task_isolation.py`, all under `tests/unit/`.
- Documentation/data exclusion: `.gitignore`, `TODO.md`,
  `docs/configuration-reload.md` and this acceptance record.
- Local-only compatibility addition: ignored `config_client.py` (`audio_dir` only).
