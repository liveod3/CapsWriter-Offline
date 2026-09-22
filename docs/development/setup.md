# Run from source

Use this guide to start a source checkout. For environment selection, dependency installation, local configuration protection, and verification commands, follow [AGENTS.md](../../AGENTS.md#prepare-the-environment). The current workspace uses the existing Conda environment `capswriter`; commands below assume its interpreter is active.

## Prepare the checkout

1. Check `git status --short` and preserve local changes.
2. Verify the project interpreter with `python --version`.
3. Install missing project dependencies and initialize only missing root configurations using the commands in AGENTS.md.
4. Download the selected [recognition model](../user/models.md) only if you intend to run recognition. Configure FFmpeg for file transcription.

## Start recognition

Open two terminals in the repository root. Start the server in the first:

```powershell
python start_server.py
```

Wait for model readiness, then start the microphone client in the second:

```powershell
python start_client.py mic
```

No client arguments also select microphone mode. This starts real audio, shortcut, and UI resources; use help commands for argument inspection without launching them.

The Windows helper can locate the registered environment and open both terminals:

```powershell
./start_capswriter.ps1 -WhatIf
./start_capswriter.ps1
```

`-ServerOnly` and `-ClientOnly` select one process and are mutually exclusive. `-WhatIf` previews generated launch commands without starting applications.

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
