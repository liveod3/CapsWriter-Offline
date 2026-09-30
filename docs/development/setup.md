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
./start.ps1 -Transcribe                  # Independent file transcription GUI
./start.ps1 -Server -Client Gui          # Server terminal and GUI client
./start.ps1 -Server -Transcribe          # Server terminal and file transcription GUI
./start.ps1 -Server -Client Console      # Server and client terminals
./start.ps1 -Server -Client Gui -WhatIf  # Preview without launching
```

`start.ps1` replaces `start_capswriter.ps1` and `start_desktop.ps1`; update existing shortcuts or commands. Select `-Server`, `-Client Gui|Console`, `-Transcribe`, or a combination. No target (including `-WhatIf` alone) prints help and starts nothing. Help and launcher messages use the system UI language (English or Simplified Chinese) without executing local Python configuration. A selected `-WhatIf` checks the environment and entry scripts but starts no applications. All selected entries are checked before any launch. Combining targets launches the server first without waiting for model readiness; clients use their configured endpoint. The helper does not detect or stop an already running server.

GUI launch uses a normal window startup state; `pythonw` itself avoids a console. The saved tray preference controls the microphone client's initial GUI visibility. Close an existing console client before starting desktop mode. See [desktop usage](../user/settings-gui.md) for start-in-tray, closing and explicit exit behavior.

## Open the file transcription GUI

Use `./start.ps1 -Transcribe` to open the independent file transcription window through `pythonw start_transcribe.pyw`. It can run beside a microphone client and does not start microphone capture or register recording shortcuts. Use `./start.ps1 -Server -Transcribe` when you also need to start the recognition server; wait for the server to be ready before beginning transcription. File transcription requires FFmpeg and the configured recognition endpoint.

Drag files or directories from File Explorer into the window and release them when the drop hint appears, choose files with the native file picker, or copy selections and press `Ctrl+V` in the window. Explicit paste also accepts absolute paths copied with **Copy as path**. A background importer filters supported media extensions, resolves and deduplicates paths, and follows `file_scan_recursive` (default `True`). The queue holds at most 200 files; each scan examines at most 20,000 directory entries with a depth limit of 32, skips directory links and junctions, and reports omissions or bounds. Choose output formats, then start transcription; files added during a running batch wait for the next start. Refer to the [transcription guide](../user/transcription.md) for supported media and output behavior.

The file window is centered within the primary screen work area at initial startup, then adjusted once after native frame margins settle. Later activation preserves the user's chosen position. If minimum dimensions exceed a small display, the title bar remains reachable.

A single Choose button opens the standard native multi-file dialog through QFileDialog.getOpenFileNames, without custom buttons or browsing behavior. Folders are imported through Explorer drag/drop or explicit paste using the same bounded background scanner. Cancellation returns no paths, and picker errors become a localized recoverable notice. Each new GUI window initially selects SRT only; other formats remain optional per batch.

The tall queue occupies the left column. Wheel notches advance one measured row with a 150 ms eased pixel animation; touchpad pixels, keyboard movement and direct scrollbar interaction retain native behavior. Export options, processing state and a collapsible activity log occupy the right column. With no active file, the state card shows only the idle message; progress and metrics are hidden. Completed-file details remain available in the queue and log. The active card orders filename, activity, combined total/processed/remaining audio, elapsed/ETA, average/last-block speed and the block diagram. Last-block audio/time details move to the speed tooltip; connection and stall-countdown text are absent from this card, while actual failures and log events remain. Normal waits between chunk results do not emit timer-based warnings; configured worker stall timeouts and connection failures still report errors. English page/section headings use title case; actions and statuses use sentence case.

Queue selection mode displays checkboxes backed by the existing Qt row selection rather than a separate execution filter. Row/checkbox drags snapshot the initial selection and apply a selected/unselected range anchored at the press row. Backtracking or crossing the anchor restores rows outside the new range to their original state. Fast jumps interpolate skipped rows and edge motion autoscrolls. Release, focus loss, hiding, mode changes and row rebuilding stop the gesture timer. Selection survives imports and reorders by path, including an empty selection in checkbox mode. Row dragging resumes outside checkbox mode; active batches still guard removal/reordering. The footer has only a red Clear queue action and Open folder. Clear queue confirms the full count with Cancel as default and rechecks editability and queue identity after the nested dialog. Per-selection removal lives in the counted red context action, capturing the displayed selection by path; Delete remains supported. Multi-selection movement labels show the eligible count. Up/down moves each contiguous group one position without changing internal order; a boundary group does not disable other movable groups. Top/bottom gathers selections in their existing relative order, preserving paused completed anchors. Only Open folder remains for accessing source-adjacent outputs. The lower queue detail is hidden for ordinary states and retained for failures/output paths. Checkbox indicators use a small muted custom drawing while retaining QCheckBox semantics and the larger hit target; Select mode/Done is a borderless text action with a keyboard focus cue.

The transcription settings dialog snapshots language, segment/overlap, in-flight window and I/O/progress deadlines for the whole batch without saving them. The client template retains 60-second file segments with 4-second overlap; existing local configuration remains authoritative. Confirmed progress arrives at segment boundaries, not at a guaranteed one-second interval. The model API does not expose a confirmed recognition position within a segment. Completed-chunk averaging does not change segmentation or interpolate recognized audio. Server configuration and credentials remain inherited; the settings dialog also displays the configured endpoint.

The terminal and GUI share [file progress calculations](../../core/file_progress.py), including the transcription timing boundary after media probing, confirmed-audio throughput, ETA and nearest-second formatting. Each advancing backend result records a completed chunk, its audio duration and wall interval since the preceding result (or transcription start). Average speed is total confirmed audio divided by the sum of those intervals; it updates only on new confirmed audio and holds between results. Intervals include upload, queueing and recognition, not isolated GPU time. Elapsed time and estimated ETA continue refreshing without advancing recognized audio. The local worker transport carries cumulative chunk counts plus last-chunk audio/time measurements, so coalesced GUI events do not lose completed-block counts. The final file summary uses actual audio duration divided by total transcription time.

The GUI paints completed, active and remaining blocks without interpolation or percentage labels. Visible block widths are proportional to unique audio duration, excluding overlap; the tail uses the probed duration until final confirmed audio corrects it. Unknown durations retain standard widths until confirmed. Its estimated total follows the server's segment-plus-two-overlaps threshold and segment stride, then uses the actual final count; unknown durations show only the confirmed count. Long files use a bounded viewport around the current block. Completion of all chunks can precede saving or child cleanup; a successful queue outcome still requires saved-output metadata and normal child exit. The batch summary measures full batch time independently. Queue removal and clearing are disabled and guarded while running; pausing enables editing without deleting disk files.

A timestamped event log immediately below progress shows file starts, processing stages, confirmed chunk progress, completion audio/time/speed, actual output paths, batch summaries, cancellations and actionable failures. Long messages and paths wrap to the viewport width. Distinct text colors identify information, progress, success, warning, error and output events; output-name collisions retain automatic numbered filenames and appear as warnings. A small outlined chevron collapses the log to a 52-pixel title row while collection continues. Follow and clear controls sit below the expanded log. It follows new entries until the reader scrolls up; re-enabling follow returns to the newest entry. The clear button requires Yes/No confirmation with No selected by default and removes displayed events only. The view inserts styled text without interpreting caller-provided HTML, retains at most 1,000 logical lines of 2,000 characters each in memory, and does not create a disk log. It receives curated metadata rather than recognition text or raw console output. Existing diagnostic persistence remains controlled by its own settings.

Windows cancellation reads a duplicated CRT stdin descriptor so the blocking control read does not stall native dependency initialization. Startup is bounded to 30 seconds, connection to 15 seconds, cancellation grace to 6 seconds and post-result shutdown to 10 seconds. Ordinary console/transcript output is excluded from the GUI transport. Run the synthetic QProcess/FFmpeg/WebSocket regression explicitly with `python -m pytest tests/integration/test_file_gui_e2e.py -m "integration and not windows and not manual"`; it requires local FFmpeg but no model or user media.

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

For agent-driven Windows GUI verification, first confirm that the launch runs on the user's interactive `WinSta0\Default` desktop. A sandbox may use an isolated desktop where processes respond, native windows report visible, and Qt captures render correctly while the user cannot see any window. Those checks validate internal behavior and layout only. Use an approved interactive execution context for the existing launch command, verify the newly owned window on `Default`, and confirm user-visible placement separately. Do not add application code that forces a desktop switch to compensate for the agent's execution environment.
