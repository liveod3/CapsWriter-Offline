# Agent guide

This guide applies to the entire repository. Preserve local customization, real-time audio behavior, and user data. Read it before working, then read the target module, its callers, state, and configuration. Check for more specific directory instructions. Explicit user instructions in the current conversation take precedence.

This file is the single source for Agent environment, architecture, privacy, and verification rules. Use the [documentation index](docs/README.md) for user and developer topics and [TODO.md](TODO.md) for active work.

## Understand the product

CapsWriter-Offline targets 64-bit Windows 10/11. A client captures microphone audio or decodes media and sends it over WebSocket. An ASR subprocess runs recognition and optional punctuation. A separate sibling process loads forced alignment on demand. The client performs optional single-request LLM processing, text insertion, subtitle output, and independent content archiving.

The default ASR path can run offline. Cloud LLM, remote ASR, and UDP have separate network boundaries; there is no application-wide strict offline switch. UI language, ASR language, LLM translation targets, and user content are independent.

The tracked [client](config_templates/config_client_template.py) and [server](config_templates/config_server_template.py) templates define current fields, defaults, and `__version__` (currently `2.7`). Actual execution uses ignored root copies. Current template shortcuts are right Ctrl (`ctrl_r`) and mouse X2 in toggle mode (`hold_mode=False`). Never infer local settings from older documentation or overwrite them with defaults.

## Prepare the environment

1. Run `git status --short` before changing files. Treat existing edits as user work; do not reset, overwrite, or format unrelated files.
2. Reuse the prepared Conda environment `capswriter` in this workspace. Record its actual Python version:

   ```powershell
   conda run -n capswriter python --version
   ```

3. If Conda is absent from the noninteractive shell, read `$env:USERPROFILE\.conda\environments.txt`, locate the registered `capswriter` environment, and call its `python.exe`. Do not create another environment or install into system Python unless the project environment is confirmed missing or unusable. Never write the resolved machine-specific path into repository files.
4. If direct invocation cannot find Conda DLLs or tools, use `conda run` or restore activation settings. Do not reinstall dependencies into system Python.

CI and configured Ruff/mypy targets use Python 3.11. Development versions are pinned in `requirements-dev.txt`. Runtime dependencies are not fully locked, and a broader Python support range has not been validated. In commands below, `python` means the verified project interpreter.

Install missing dependencies only as needed:

```powershell
python -m pip install -r requirements-client.txt -r requirements-server.txt
python -m pip install -r requirements-dev.txt
```

Source imports, CLI help, and some tests need local configuration. Initialize only missing files; syntax-only checks do not require initialization:

```powershell
if (!(Test-Path config_client.py)) { Copy-Item config_templates/config_client_template.py config_client.py }
if (!(Test-Path config_server.py)) { Copy-Item config_templates/config_server_template.py config_server.py }
```

Use [source setup](docs/development/setup.md) for launch commands. `start_capswriter.ps1` starts applications unless `-WhatIf` is supplied; it is not an ordinary static check. Server startup normally requires complete models. Do not download large models for code checks. File transcription requires FFmpeg; missing ffprobe degrades duration estimates.

## Locate the implementation

| Area | Entry points |
| --- | --- |
| Source/frozen startup | `start_client.py`, `start_server.py`; preserve Windows `freeze_support()` |
| CLI and drag-and-drop | `core/client/cli.py`; parse before importing audio/UI modules |
| Wire contract | `core/protocol.py`; read for any client/server change |
| Microphone and shortcuts | `core/client/audio/`, `core/client/shortcut/`, `core/client/app.py` |
| File transcription and SRT | `core/client/transcribe/`, `core/client/manager/file_runner.py` |
| Server connections | `core/server/connection/` |
| Scheduling and task state | `core/server/schema.py`, `state.py`, `worker/task_handler.py` |
| Model and process ownership | `core/server/engines/manager.py`, `worker/process_manager.py`, `worker/aligner_worker.py` |
| LLM configuration and requests | `core/client/llm/`, `LLM/providers.template.toml`, `LLM/presets.toml` |
| Caret reference | `core/client/caret_context.py`, `caret_worker.py` |
| Localization | `core/i18n/`; [localization design](docs/development/localization.md) |
| Shared UI and native tray | `core/ui/`; native pystray 0.19.5 integration is isolated in `tray_native.py` |
| Archives | `core/diagnostics.py`, `core/logger.py`, `core/client/diary/diary_writer.py` |
| Shared text tools | `core/tools/`; [merge behavior](docs/development/text-merging.md) |
| Packaging | `build.spec`, `build-client.spec`, `build_hook.py`, `build_llm.py`, `zip_release.py` |
| Quality gates | `tests/`, `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/` |

## Make reviewable changes

- Prefer the smallest change that completes the requested outcome. Read code ownership before touching derived engine code. Do not bulk-format copied export/GGUF tools or zhconv.
- Keep UTF-8. Maintained developer/Agent documents, plans, comments, docstrings, internal exceptions, and project-owned diagnostic file records use English. Chinese user guides remain supported. Follow the [writing guide](docs/development/writing-guide.md) and the [scoped language check](docs/development/internal-language.md).
- Keep product labels, CLI help, and terminal diagnostics in locale resources. Use `Notice` for controlled diagnostics/exceptions and `tr` at display boundaries. Keep stable IDs separate from labels. Preserve user text, language fixtures, recognition rules, task prompts, and upstream attribution.
- Edit tracked configuration templates for field, default, comment, or version changes. For existing local copies, merge only necessary structural changes while retaining user values. Use safe defaults through `getattr(..., default)` or migration logic for older configurations. Never make a feature depend only on an ignored local edit.
- Root configurations are executable Python at startup. LLM configuration is static TOML. Never execute legacy `LLM/*.py` to migrate settings; inspect it statically without printing secrets.
- `LLM/providers.toml` is ignored and may contain credentials. Never print it in full or commit it. Prefer environment keys; explicit `api_key_env` takes precedence over `api_key`. Missing local configuration falls back to the public template; only first edit creates a local copy.
- Never commit keys, tokens, private audio, transcripts, clipboard contents, logs, real model paths, local backups, model binaries, or build artifacts.
- Search with `rg`/`rg --files`. Expand ignored-file searches only for relevant paths. Keep temporary scripts in a temporary/local work directory; do not remove the user's scripts.

## Preserve architecture boundaries

### Audio and threads

PortAudio callbacks must return promptly. Do not wait for the network, perform heavy file I/O, close streams, or reinitialize PortAudio there. Start, stop, reopen, monitoring, and idle suspension can race: use an initialized lifecycle lock or a single state machine and avoid duplicate recovery threads.

Do not use sounddevice private APIs for ordinary lifecycle work. Where compatibility requires them, isolate version checks, failure paths, and Windows validation. Submit to asyncio loops through thread-safe entry points and handle closed loops.

### WebSocket and processes

Keep blocking I/O and CPU inference off event-loop threads. Recognition stays in the worker subprocess; blocking queue reads use an executor. Keep Task, Result, and queued state pickleable. Do not enqueue WebSockets, Tk objects, locks, or local closures.

Preserve `(socket_id, task_id)` identity through buffering, scheduling, processing, result routing, and cleanup. Same-ID tasks on separate connections must remain independent; see `tests/unit/test_connection_task_isolation.py` and the [accepted validation scope](docs/validation/P0-01-connection-task-isolation.md).

For wire changes, update serialization, parsing, client sends, server receives/results, client handling, and compatible defaults together. Validate untrusted types, required fields, enums, Base64, sample alignment, slice ranges, and resource limits. Do not introduce unbounded caches.

Keep local mode loopback-only and LAN mode Bearer-authenticated with `CAPSWRITER_AUTH_TOKEN` of at least 32 characters. Authentication is not encryption. TLS requires explicit configuration; see [network setup](docs/user/network.md).

Forced alignment uses `ProcessAlignerProxy` and a sibling process. Preserve request IDs, bounded queues, timeout handling, idle exit, and parent supervision. Do not unload shared GPU backends inside ASR. Check startup, crash, timeout, cancellation, and shutdown paths together.

### UI and input

Create and modify Tk objects on their owning thread. Other threads should enqueue work for the existing manager's `root.after(...)` polling. Existing cross-thread `after` calls must handle root-not-ready, stopped-mainloop, and destroyed-window states; `after` is not unconditionally safe.

Global shortcut callbacks must not wait for Tk, the network, or models. Keep recording indicators, tray state, and `ClientState.recording` consistent on start, cancel, failure, and completion. Changes to hold/toggle/suppression require short/long/repeated press, auto-repeat, first use after pause, shutdown, and administrator-window checks.

### Data and privacy

Templates enable transcripts but disable audio saving, LLM, and caret reference. Keep each control independent. New collection or transmission must be explicit, explainable, and disableable.

LLM requests contain the current transcription and, only when enabled by both client and preset, the current caret reference. Do not add history or selection reads. Local ASR binding does not prevent cloud LLM or UDP traffic. Check actual endpoints even for providers named Ollama or LMStudio. Verify claimed offline boundaries with mocks.

Keep ordinary diagnostic events content-free. Explicit diagnostic text copies are allowed only through the bounded `log_content` API when `diagnostic_include_text` is enabled; caret references additionally require `diagnostic_include_context`. User authorization for these opt-ins does not enable extra UI reads or network requests. Never log configured credentials or authentication headers. Keep metadata useful when content is disabled, preserve readable native diagnostics, and treat enabled text copies and old archives as private user content. See [logging contract](docs/reference/logging-and-records.md).

### Packaging and generated data

Packages copy defaults from `config_templates/`, never root local configurations. `build_llm.py` copies only public provider defaults and presets; do not junction the local LLM directory or package its credentials.

Windows specs can create junctions into the working tree. Inspect resolved link targets before cleaning, moving, or archiving build directories. Never assume `dist/` is an independent copy. Historical Win7 comments do not establish compatibility. Keep `build/`, `dist/`, `logs/`, year directories, model binaries, and bytecode outside ordinary edits.

## Verify the change

Run checks appropriate to scope and report omissions with reasons. A configured workflow is not proof of a successful remote run.

Minimum syntax and diff checks:

```powershell
python -m compileall -q start_client.py start_server.py config_templates core LLM tests scripts
if (Test-Path config_client.py) { python -m compileall -q config_client.py }
if (Test-Path config_server.py) { python -m compileall -q config_server.py }
git diff --check
git status --short
```

For documentation-only work, verify links, commands, and source facts; do not start the application or require the full test suite. For Python changes, run relevant tests after preparing configuration/dependencies. Protocol, scheduling, and thread/process lifecycle changes require the default suite:

```powershell
python -m pytest -q
```

Apply these quality gates to code changes as appropriate:

```powershell
python -m ruff check start_client.py start_server.py config_templates core LLM tests scripts
python -m mypy core/protocol.py core/server/schema.py core/server/merger core/tools/format_tools.py
python -m pytest --cov --cov-report=term-missing --cov-fail-under=50
python scripts/check_internal_language.py
python scripts/check_docs.py
```

The 50% coverage threshold applies only to configured modules, not the whole repository. Default pytest excludes `integration`, `windows`, and `manual`; `tests/conftest.py` also marks tests by directory. Native menu tests inspect handles, bitmaps, and mappings without reading user text or starting hardware; they do not replace interaction/DPI checks. The integration directory currently contains guidance only. Select safe integration tests with `-m "integration and not windows and not manual"` when applicable.

Pre-commit runs Python lint, type, and syntax checks; pytest runs at pre-push. Do not install hooks or run repository-wide automatic fixes for ordinary checks. Windows quality CI uses Python 3.11 and initializes missing configurations from templates. Release smoke runs quality, builds the combined package, and checks EXE existence; it does not run the package or validate a clean machine.

Use focused boundary checks:

| Change | Required cases |
| --- | --- |
| Protocol/network | Valid and malformed messages, enums/Base64, limits, disconnects, concurrent tasks |
| Scheduler/merging | Per-task FIFO, fairness, disconnect cleanup, exactly one final outcome |
| Audio | Idempotent start/stop, pause/resume, device changes/removal, recovery, short cancel, no restart after exit |
| UI/tray | Primary/secondary monitors, mixed DPI, nonoverlapping hints, no late Tk access |
| LLM | Reload, single default, explicit override, routing, missing key, timeout, cancel, original-text fallback, zero requests when disabled |
| Caret/records | Passwords, selection, unsupported providers, timeout/focus change, fixed chunk snapshot, save-switch combinations, month rollover |
| Packaging | Source checks first, clean-machine validation when requested, no local/generated data |

Before real microphone input, global key simulation, network LLM calls, GPU management commands, model downloads, or full packaging, check environment and user intent. Do not add these to static/documentation work. Existing explicit authorization remains valid while scope is unchanged.

## Maintain tests and handoff

Put pure/mock tests in `tests/unit/`, cross-component tests in `tests/integration/`, and desktop tests in `tests/windows/`. Keep existing root protocol/scheduler tests in place. `.gitignore` allows tests despite its general `test_*.py` rule; verify new files with `git status` or `git check-ignore`, without force-adding them.

Prefer deterministic boundary tests for protocol validation, text algorithms, TaskBuffer fairness, mocked audio lifecycle, and mocked LLM/privacy behavior. Never depend on real keys, private audio, or installed large models. Use synthetic silence/sine fixtures with explicit sample rate and dtype.

High-risk code includes audio streams, client shortcut ownership, connection/task boundaries, scheduling, ASR/aligner supervision, LLM routing, and copied engine exports. Consult the [current audit](docs/archive/PROJECT_AUDIT_REPORT-2026-09-16.md) as dated evidence, not as a second backlog. Historical fixes do not replace checking current callers and tests.

Before handoff, verify that edits stay in scope, existing user work remains, configuration defaults/failure paths/cleanup are covered, and no private data is included. Update the matching TODO entry with implementation and validation status. Mark accepted work complete and commit only after user acceptance when requested. Record newly found out-of-scope defects in TODO instead of silently fixing them. Report changed files, results, checks, and remaining manual validation.
