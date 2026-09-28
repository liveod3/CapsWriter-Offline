# Run from source

Use this guide to start a source checkout. For environment selection, dependency installation, local configuration protection, and verification commands, follow [AGENTS.md](../../AGENTS.md#prepare-the-environment). The current workspace uses the existing Conda environment `capswriter`; commands below assume its interpreter is active.

## Prepare the checkout

1. Check `git status --short` and preserve local changes.
2. Verify the project interpreter with `python --version`.
3. Install missing project dependencies and initialize only missing root configurations using the commands in AGENTS.md.
4. Download the selected [recognition model](../user/models.md) only if you intend to run recognition. Configure FFmpeg for file transcription.

## Start recognition

Start the independently managed server from the repository root:

```powershell
python start_server.py
```

Wait for model readiness, then open the desktop client without a persistent client terminal:

```powershell
./start.ps1 -Client Gui
```

The unified helper locates the registered `capswriter` environment and uses `pythonw start_desktop.pyw` for the GUI client. It opens the main GUI and automatically connects to the configured service without managing the server. These launch commands start real audio, shortcut and UI resources; use help for argument inspection. The original console entry remains `python start_client.py` (or explicit `mic`).

The Windows helper requires an explicit launch selection:

```powershell
./start.ps1                              # Help only; no environment required
./start.ps1 -Help                        # Help only
./start.ps1 -Server                      # Server terminal only
./start.ps1 -Client Console              # Console client only
./start.ps1 -Client Gui                  # GUI client only
./start.ps1 -Server -Client Gui          # Server terminal and GUI client
./start.ps1 -Server -Client Console      # Server and client terminals
./start.ps1 -Server -Client Gui -WhatIf  # Preview without launching
```

`start.ps1` replaces `start_capswriter.ps1` and `start_desktop.ps1`; update existing shortcuts or commands. Select `-Server`, `-Client Gui|Console`, or both. No target (including `-WhatIf` alone) prints help and starts nothing. Help and launcher messages use the system UI language (English or Simplified Chinese) without executing local Python configuration. A selected `-WhatIf` checks the environment and entry scripts but starts no applications. All selected entries are checked before any launch. Combining targets launches the server first without waiting for model readiness; the client uses its configured endpoint and existing reconnect behavior. The helper does not detect or stop an already running server.

GUI launch uses a normal window startup state; `pythonw` itself avoids a console. The saved tray preference alone controls initial GUI visibility. Close an existing console client before starting desktop mode. See [desktop usage](../user/settings-gui.md) for start-in-tray, closing and explicit exit behavior.

## Use the command line

```powershell
python start_client.py --help
python start_client.py transcribe --help
python start_client.py transcribe --format srt,txt,json --no-recursive "D:\Media"
python start_client.py rebuild-srt --text "edited.txt" --json "timestamps.json"
```

Replace sample paths with your files. Transcription requires the server; subtitle rebuilding does not. CLI options apply to one invocation without modifying local defaults. See the [transcription guide](../user/transcription.md) for output and timestamp limits.

## Validate changes

Use [AGENTS.md](../../AGENTS.md#verify-the-change) as the single verification matrix. Pure checks do not need models or live hardware. Record manual validation separately in [validation records](../validation/README.md); do not infer release readiness from source tests.
