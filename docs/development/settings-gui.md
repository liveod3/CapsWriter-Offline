# Client desktop ownership

The desktop is a PySide6 QtWidgets main window with an overview and integrated settings, backed by the existing settings service. It supervises one hidden client; audio, shortcuts, WebSocket, LLM and caret behavior remain in that existing client implementation. The ASR server remains independent and is never started or stopped by the GUI. The settings package has an inert initializer and can be imported before executable local configuration. See the [user guide](../user/settings-gui.md) and [validation record](../validation/P1-settings-gui.md).

## Process and thread boundaries

Each microphone-mode client starts with `dictation_paused=True` and
`dictation_manually_paused=False` before publishing runtime state or creating
resource managers. `MicRunner` skips stream startup. This is the existing idle
suspension state: the first shortcut opens the microphone and records through the
normal asynchronous resume path. Failed wakeup leaves standby retryable. Runtime
snapshots include `manually_paused` so the overview distinguishes standby with
shortcut guidance from deliberate manual pause, which still blocks shortcuts.
File transcription, subtitle rebuilding and existing UDP pause rules are unchanged.

Qt widgets and QApplication belong to the GUI process main thread. This follows [Qt's GUI threading requirement](https://doc.qt.io/qt-6/threads-qobject.html) and isolates Qt's DPI behavior from the existing Tk process settings. A worker performs blocking file/pipe operations; a 50 ms main-thread timer consumes its bounded result queue. Requests are serialized. A two-second poll reads settings/catalog revisions; between those reads, desktop mode checks content-free runtime flags at 200 ms intervals without reading configuration files. Busy operations skip polling rather than accumulating requests. Background polling leaves controls enabled and queues at most one foreground action; client autosaves keep controls enabled while catalog mutations remain explicit foreground actions. Explicit desktop exit always asks for confirmation, with Cancel as the default, then flushes valid client edits before shutdown. A failed write cancels exit rather than losing drafts.

The window, overview and Qt tray reuse `assets/client-icon.ico`. Recording adds the console client's red badge with a light outline; stopping or losing runtime status clears it. The icon is only replaced on recording-state changes, while tooltip text conveys readiness, pause and connection state. At the user's request, font sizes return to the earlier compact scale: 12 logical pixels for body controls, 11 for small state labels and 15 for card titles. Font selection follows the system UI font and its glyph fallbacks rather than forcing Microsoft YaHei UI for every control. Closed choices and numeric controls ignore wheel events even when focused, allowing the surrounding page to scroll; direct typing and keyboard editing remain available, and an open choice popup retains list scrolling.

`DesktopBackend` starts `ClientSession` automatically on the first read. Source mode uses the environment's console Python with `CREATE_NO_WINDOW`, including when the desktop itself runs under `pythonw`; frozen mode uses sibling `start_client.exe --desktop-worker`. Before sending boot, the parent assigns only its child to a Windows job with kill-on-close ownership, containing client helpers but no external server. The worker separates native/Python stdout from its RPC pipe before importing the real client. Inherited binary stdin/stdout carry one JSON object per line, bounded at 2 MiB, with sequence IDs and serialized calls. Operations use `ClientOperations.submit()` to access the existing asyncio loop; file operations continue through the shared off-loop settings service. Status reads expose content-free connection, recording, pause, file-mode, microphone-readiness and task-count values.

`DesktopPresence` combines a process lock and a user-scoped Qt local pipe, keyed by canonical installation directory. A second desktop invocation activates the existing window and exits; it never starts a second client. This activation pipe carries no configuration or operation payload and has no TCP listener. The desktop owns its Qt tray; the hidden client's old tray is disabled. The legacy explicit `mic` workflow retains `SettingsProcess`, whose tray opening creates/reuses a settings-only child. `settings gui` remains a standalone static editor with no client ownership.

Normal desktop closure hides the window when a tray is available and preserves drafts and dictation. Without a tray it follows explicit Quit. The saved `start_minimized` preference starts hidden only when a tray exists. Quit confirms intent, warns about active tasks, waits for current operations and valid client autosaves, asks before discarding invalid/client-error or catalog drafts, then requests client shutdown off the Qt thread. Shutdown acknowledges and leaves the control read loop before finalization; output EOF releases stdin for independent client exit. The non-daemon control/stop threads are signaled and joined. The parent uses bounded waits and then closes its job and reaps its child. Abnormal desktop exit also closes owned processes; a failed close leaves a retryable UI. Startup errors reveal the window once and retain settings access.

The legacy settings child's parent EOF closes its window without a modal dialog; explicit console-client exit terminates/reaps that owned child. Application termination can interrupt an editor operation, whose file replacement is atomic; it does not provide a multi-file transaction or durable transaction acknowledgement. Standalone console and desktop clients are not mutually exclusive across entry modes: users must exit the old console client before switching to desktop mode.

The existing Tk host now rejects submissions when shutdown begins, discards queued work, cancels overlay timers, releases widget references, quits/destroys on its owner and joins with a two-second bound. Shutdown before root readiness and after a queued close cannot start another main loop. Stopping an unused host does not create a thread, and late first use cannot start one. No overlay migration or placement redesign is included.

## Files and drafts

Client edits use `SettingsService` and the existing reloader. The standalone entry point uses the static parser and explicitly reports runtime state as unknown. It never executes root configuration, starts dictation or imports Tk. Attached editing uses the runtime LLM directory until a restart applies any saved directory change.

TOML edits use TOML Kit to preserve comments and unrelated fields, sharing the request loader's `parse_catalog()` validation. A revision covers both the selected provider file (including template fallback identity) and the preset file. Cooperating writers share a catalog lock; candidates are validated, fsynced, rechecked and atomically replaced one file at a time. External editors that do not honor the lock retain the small final check/replace race already documented for Python settings. Files and candidate serialization are bounded at 1 MiB. Deleting a referenced provider or saved default preset is rejected.

Only changed visible fields are written. Opening and saving another field preserves original numeric/string types and precision; rendering a numeric value does not authorize normalizing it. Existing provider keys are represented only by a presence flag. An empty replacement leaves the key unchanged; explicit clear writes an empty key. Environment-variable precedence remains unchanged. Failed saves retain drafts and never print raw parser exceptions or values.

There is no permanent reload button. Background revision changes refresh clean client forms and clean catalog editors automatically, preserving catalog selection. Client drafts block automatic client-form replacement; either catalog draft blocks catalog replacement. Conflicts preserve the old revision so a save cannot silently overwrite external edits. Only a detected conflict reveals the contextual **Use file version** action, with confirmation before discarding drafts. The ordinary footer shows autosave status and exposes Retry only after a failure. Advanced-file access is on a dedicated settings page.

Client fields autosave after a 600 ms debounce; numeric editing commits on completion. Immediate field validation checks the displayed draft, and the shared service still validates the entire revision-checked atomic write. Invalid drafts block the write. Errors remain inline without modal retry loops. A completed write acknowledges only its submitted values, preserving edits made during I/O for the next save. Programmatic population and catalog choice refresh suppress edit signals. Provider selection saves immediately on user activation of the dropdown. Client and catalog writes remain separate operations. The GUI does not promise atomic changes across them. The backend and CLI retain explicit prompt inspection, but the common GUI no longer exposes arbitrary preset editing or prompt preview. Provider selection edits only the built-in cleanup action provider field; custom prompt text and context permissions remain untouched.

GUI labels retain their startup locale. Language edits save automatically and show a GUI restart explanation, even when the hidden client can publish the locale between tasks. Fully quitting and reopening updates the window and desktop tray; hiding to the tray does not. Common client fields and catalog inputs have adjacent circular help buttons with accessible names/descriptions. Native Qt tooltips display escaped, width-bounded title/body text on hover, with click and keyboard access. Errors and changed-state badges remain inline, while permanent explanations are removed. Short numeric/code/choice fields use bounded widths. Settings groups use two columns once their available width reaches 920 logical pixels and return to one column below it; reflow moves the existing cards without rebuilding fields, changing drafts or dispatching writes.

Field captions prefer their measured single-line text width and wrap only when space
requires it. They no longer impose the former 180-pixel label minimum or 290-pixel
caption maximum. Label bounds update after font/style changes and when shown; the
help icon stays six pixels from the text box instead of following an expanded label.
All common-settings groups use the same subtle row dividers and spacing, including
Records, recognition-service connection and Diagnostics. Troubleshooting actions
also have separators.

Advanced uses a scrollable page with client-configuration and LLM-preset action cards,
followed by a separate file-change guidance panel. Cards are side by side from 700
logical pixels of available content width and stacked below it. In two-column mode
they share a compact content-driven height and align their action buttons. A short
intro and normal-contrast descriptions replace the long muted introductory block;
existing file-open routes and conflict/restart behavior are unchanged.

## Settings page organization and final formatting

General owns interface/startup preferences. Dictation contains three separate cards:
Microphone and capture, Text formatting, and Output method. Records owns only
persistence preferences. The former Text processing page is labeled LLM processing;
its stable internal `text` ID is retained. Navigation shortcuts resolve page IDs
rather than positional indices. Device discovery/watch visibility follows Dictation.
Moving fields does not change saved keys, values, autosave or restart requirements.
Final formatting controls stay independent of the LLM master switch.

Microphone result handling passes the unmodified ASR result into the optional LLM
action. It then applies Traditional conversion and trailing-punctuation trimming
to the returned text, including skipped/failed actions. The length limit counts
final text. The final formatted value goes to insertion, UDP, cached output and
final history; ASR, LLM input and raw LLM output remain distinct archive stages.
Existing cancellation/focus guards and save permissions remain unchanged. File
transcription/subtitle paths are unaffected.

## LLM processing

The common page uses Text cleanup as the user-facing name for the built-in
`correct_asr` action. Stable IDs, correction prompt composition and capability
routing remain compatible. The page always offers the cleanup provider and three
editing strengths, with five prompt modules grouped separately as Cleanup details.
Caret reference controls remain independent. Translation, arbitrary presets,
triggers, default routing and separate capability switches are advanced file
configuration; they are not silently reset by opening or saving this page. A
contextual notice identifies advanced configurations that bypass composed cleanup.

A fixed provider selector writes only `provider` on `correct_asr`; if that
entry is absent, selecting a provider creates the standard composed action.
Provider connection creation, deletion and credential editing are absent from the
GUI. The selector reads configured entries without probing endpoints or discovering
models. A content-free `credentials_ready` flag uses the transport's explicit
environment-variable precedence and permits keyless services; configured-but-empty
credentials disable that choice. The saved choice remains visible even when it
is unavailable, and no fallback selection or configuration write occurs on load.
The selected provider owns the required model ID, protocol, endpoint, credentials
and timeout. There is no model input or preset model override. A read-only details
panel displays the configured model, API type and rates. Rates use the existing
cost configuration and exact endpoint/model matching, with currency, per-million
token units and update date. Missing, invalid and expired rates receive distinct
messages; missing rates never imply zero. Optional rate categories appear only
when configured. Cost/credential metadata refreshes without discarding selection
drafts, even when catalog revisions have not changed. No live pricing lookup is
performed. The provider help button and selector have an explicit 20-pixel gap.

Provider selection autosaves on the combo box activation signal with existing
revision checks. Loading, metadata refresh and reselecting the saved value do not
write configuration. The serialized foreground transaction briefly disables the
controls until completion, preventing a later selection from overtaking a write.
Save failures remain inline and preserve the draft; reselecting retries. Queued
and active writes complete before close/exit, and failures cancel pending exit.
The next request reads the new provider; in-flight requests keep their snapshot. The catalog transaction API remains available to file tooling, but
there is no provider editor in the window. Other client controls keep
autosave. All visible labels and help use locale resources; API/model/provider IDs
remain stable. The ASR connection page now contains only the recognition endpoint.
The LLM master switch immediately gates provider selection, cleanup cards, caret
controls, LLM records, context diagnostic copies and accounting preferences. Whole
rows/cards are disabled, with explicit muted label/input/help styling. Values and
unsaved provider drafts remain intact; loading settings and finishing foreground
operations reapply the gate. Audio, ordinary transcript history and ASR controls
remain independent. The capture entry point also checks the LLM master switch,
so disabled context controls cannot still collect references for ASR. Advanced offers a preset-file action; the running worker resolves the effective
LLM directory and the GUI owns the external editor process. No model listing,
provider probe, extra request, caret capture or automatic configuration migration
is added.

## Input device and language choices

Recognition choices persist canonical lowercase language names from the server language contract. A contract test reads that mapping statically without importing server startup. Labels are localized; the common list is not server capability negotiation, and help explicitly explains model-dependent support. Unknown saved languages and legacy device values remain selectable without silent conversion.

General-page entry, debounced device notifications and explicit Refresh dispatch `input_devices` through the serialized GUI worker. Desktop mode handles this locally rather than forwarding it into the active audio client. A fresh hidden helper from the same source environment or packaged `start_client.exe` runs the early `--list-input-devices` entry before executable configuration, client or server imports. It only reads sounddevice's public discovery APIs; it never creates a stream or reinitializes live PortAudio. A fresh process avoids stale hotplug inventories. Execution is bounded to eight seconds, with timeout cleanup by `subprocess.run`, bounded structured output and sanitized failures. Process-local Windows error mode prevents native device-driver failures from creating modal crash-report dialogs.

The normal menu includes system default and readable endpoint names from one preferred backend: WASAPI, then DirectSound or MME if unavailable. This hides alternate host interfaces and kernel pins rather than guessing physical identity by similar names. Output-only devices are excluded. Full metadata remains available for validating existing nonpreferred selectors, which remain visible when selected. New explicit selections persist the exact `name, host API` selector instead of an unstable discovery index. Exact duplicate selectors are disabled; legacy indices, empty defaults, custom selectors and disconnected selections retain their types and values. Refresh suppresses edit signals and preserves the current draft, so metadata arrival cannot write settings or discard edits made during discovery. Repeated in-flight refreshes coalesce; busy dispatch retries through one timer. Normal window closure waits for the bounded active probe; actual exit stops retries. Microphone selection retains the existing restart requirement. Device metadata remains local and is not added to diagnostic archives or model requests.

`device_watch.py` subscribes to Windows Core Audio notifications on the Qt owner thread using the isolated `endpoint_notifications.py` binding. The callback publishes only a bounded marker; it performs no I/O, widget mutation or COM operations. A Qt timer drains and debounces changes for 500 ms, retaining dirtiness while settings are hidden. Registration failure falls back to a 15-second visible-page refresh. Repeated events during discovery request one follow-up; busy work and open device menus defer queries. If a menu opens during a query, the result is deferred by requerying after it closes. Identical inventories keep the existing model. Discovery never inserts a loading row, including first use. Persistent device errors use a fixed inline status-icon slot with retained hidden space and accessible hover/click/keyboard details, so success/failure/retry cannot move adjacent settings. All common-settings groups use subtle separators between their rows. Actual close/exit unregisters notifications before releasing references, stops timers and ignores late callbacks. Hiding to the tray retains the subscription without periodic device probes. Audio-stream monitoring and recovery remain owned by the client and are unchanged; following system default differs from pinning an explicit microphone.

Native callback layout and lifetime rules follow Microsoft's [IMMNotificationClient contract](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nn-mmdeviceapi-immnotificationclient) and [registration contract](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nf-mmdeviceapi-immdeviceenumerator-registerendpointnotificationcallback). Native tests register read-only notifications and exercise the callback ABI with synthetic IDs, including the by-value property key; they never change a system default or open audio streams.

## Recognition history

The history workspace uses one shared white rounded panel. A localized total/range,
search controls and wrapping calendar filters remain above an independently scrolling
single-column list. Dates group the newest-first records. Clicking a row or pressing
Enter/Space expands its own detail below the header without closing other records.
A visible scrollbar and a fixed compact footer keep the result interval beside
numbered page links. The current page remains editable for direct jumps.

`history_widgets.py` owns wrapped row headers, disclosure arrows and animated pixel
wheel scrolling; high-resolution touchpad deltas retain native handling. Keyboard
navigation, scrollbar dragging, range changes and hiding stop pending animation.
A bounded main-thread layout adjustment preserves the clicked/visible row position
across deferred Qt child layouts. User scrolling and hiding cancel that adjustment.

`history.py` queries the existing daily Markdown files under the effective `transcript_dir`; standalone mode uses the saved directory. There is no new index, collection, archive format or diagnostic text copy. The full history query reads content on entry, search/paging, relative-date rollover or record selection. The latest-five preview refreshes only while its page is visible, independently of status polling. The GUI worker performs reads off the Qt thread through the existing serialized adapter. Returned content stays inside the owned local pipe and plain-text widgets; no provider, caret or clipboard read is involved. Clipboard writes require the explicit Copy action.

Queries accept independent inclusive date bounds, a case-insensitive keyword of at most 200 characters and a bounded page number. The GUI uses calendar popups with all-dates, today, last-seven-days, current-month and custom choices; the older month parameter remains available to internal callers. Discovery visits only the three-level `YYYY/MM/DD.md` archive layout, pruning directories outside the date range, and stops after 20,000 directory entries. Reads enforce containment in the resolved archive root and use at most 2 MiB from each day tail and 16 MiB per query. At most 10,000 records are inspected; limits and unreadable files are reported, not silently treated as complete results. Results contain 30 bounded previews per page, newest date/time first. Page numbers clamp to the last available page when concurrent archive changes reduce the result count. Existing `### HH:MM:SS` headings delimit records; old unstructured daily notes remain a day record. Markdown is inherently ambiguous if user text itself contains timestamp headings or metadata labels; this reader does not rewrite or promise lossless semantic reconstruction of legacy archives. The original day file remains accessible.

History queries retain visible content until the newest response arrives. Unchanged
entry identities (day, byte offset, length and digest) reuse their expanded widgets;
other rows are disposed when replacing the page. Read details and queued expansion
requests are bounded by the current 30-row page. Individual expansion errors stay
with their row and can be retried by collapsing and reopening it. Blank results use
a centered hint inside the list, without a separate detail pane.

Preset date changes submit immediately; custom dates and keywords debounce for
300 ms. Enter and Refresh submit immediately; Clear filters resets both criteria.
Numbered page links and previous/next controls share a right-aligned group with the
visible result interval. Enter or leaving the current page field submits a changed,
bounded page number; filter changes reset to page one. Partial/unreadable/saving-off
notices remain explicit.

A history-local dispatcher accepts one request at a time. Queries coalesce to the
latest navigation intent and supersede queued detail reads. Expanded rows queue
independent digest-validated reads, so opening another row cannot put its content
in an earlier row. Query generations reject stale results/errors; per-operation
tokens also reject callbacks arriving after stop/resume. A main-thread retry timer
waits for the shared serialized dispatcher, including quiet polling and foreground
settings work. Detail loading never disables the whole page. Timers and pending
work stop on actual close/exit, not tray hiding.

The full query refreshes when a visible relative range changes at a local date
boundary. A one-second GUI timer checks only the date and stops while hidden.
Re-entry, manual refresh, pagination and reselecting a period resolve relative
dates. Today, Monday-based This week, rolling Last 7 days and This month follow the
current date; custom dates remain fixed. Backend reads remain serialized.

Detail requests carry a validated day, byte range and SHA-256 digest. Changed/deleted content gives a controlled error; details are limited to 64,000 characters and explicitly marked when clipped. All payloads stay below the existing 2 MiB pipe limit. Plain text display keeps Markdown links and HTML inert. Opening a day file uses Notepad with separate argv entries; desktop mode obtains the validated effective path from its worker but launches the editor in the GUI process so client shutdown cannot kill the user's editor. Disabled saving does not prevent querying existing records. Files are read live, so pagination is not a transactional snapshot across simultaneous archive changes.

`history_detail.py` parses saved stages; `history_view.py` displays them in each expanded record. Final result opens by default, with Processing, Saved request, Usage and cost, and Raw record tabs retaining all previously available details. Detail tabs fit their active contents; long text fields remain height-bounded and scrollable. Missing stages remain explicitly unavailable, including ambiguous deduplication of original text. Only a complete successful action record permits reconstructing omitted identical LLM output from the final text. Actual saved prompts are shown without reading current presets. No new archive fields or save permissions are introduced.

When a saved request ID exists, detail loading first uses the unified activity database's indexed accounting view, then the recording month and neighboring legacy ledgers. Unified connections have a two-second lock timeout; legacy lookups retain a one-second timeout. Records longer than 65,536 SQLite characters are rejected. The GUI receives only allowed model/status/duration/usage/amount/provenance fields, never raw rate or endpoint metadata. Provider charges, rate estimates, token estimates and possible incomplete-request costs remain distinct; absent IDs, absent rows, invalid settings and unavailable ledgers do not imply zero and cannot block viewing the transcript. No retrospective rate calculation or network call is made.

The Statistics page also reads the independent [activity database](../reference/activity-database.md)
for dictation timing, recent task outcomes, median/P95 and unavailable counts. LLM timing
groups preserve provider/model identity. Diagnostics exposes `save_runtime_statistics`
separately from log severity. Opening the page never enables collection or performs extra
audio, UI-context or provider reads.

Subtle and sidebar buttons have explicit hover/pressed/focus styles that override their ID-specific base rules. GUI buttons use pointing-hand cursors. Exit client is a full-width, left-aligned outlined action at the bottom of the sidebar; the redundant local-settings footer has been removed. This addresses discoverability without revisiting the deferred Chinese font policy.

## Statistics dashboard

Statistics is separate from Diagnostics. Its internal `status` route and `StatusPage`
class remain stable. `status_data.py` reads existing transcript
archives and accounting ledgers; it neither collects new content nor changes save
permissions. Accepted runtime tasks and saved text entries have separate cards.
Today, this-week (Monday start), this-month, this-year, seven-day and thirty-day
calendar windows include today and scope all summary cards, timing
distributions and accounting groups. Recent previews belong to Overview, immediately
below Your recording shortcuts. `recent_words.py` shows the latest five records as fully expanded
rows without an inner scroll area, collapse controls, period selector or refresh button.
It refreshes on entry and every thirty seconds while visible. Clicking a row, Enter or
Space reopens the digest-validated record and copies only the complete final stage;
changed, empty or truncated results are rejected. Pending copies leave the row
unchanged; failures remain visible beside the clicked record. Successful copies from recent rows and the full history
use a shared Qt child-widget toast centered horizontally at 80% of the application
window height. It lasts 2.4 seconds, restarts on repeated copies, follows window
resizes, accepts no input or focus, and hides when the page/window hides or closes.
This is independent of the dictation status overlay. Clipboard contents are never
read. Hiding or closing stops preview refresh and ignores stale copy callbacks.

Usage queries inspect the unified file and intersecting/adjacent monthly SQLite
files read-only, including January for a thirty-day window spanning a short February.
Aware request timestamps are filtered against the shared half-open window. Reads are
bounded to 10,000 records per month and 30,000 per complete query,
65,536 characters per record, twelve currencies,
128 accounting groups, a two-second lock timeout and a two-second execution budget
per database. Partial, malformed and unavailable data remain explicit.
Tokens use recorded provider totals or known input plus output, without
adding cached/reasoning subsets or heuristic estimates. Amounts retain
currency and provider/rate/token/possible-cost provenance; missing usage or charges
never imply zero. Cost tracking preferences live in Records.

`StatusPage` uses the existing serialized background worker, one active request
and one coalesced pending action. It refreshes on entry, manually and every thirty
seconds while visible. Hidden pages stop timers and reject obsolete callbacks;
generation checks also protect filter changes. Busy-worker
retries preserve the current view, errors remain inline, and unchanged rows retain
their widgets. Closing waits for bounded active reads/copies and stops refresh.
Four overview cards adapt to one, two or four columns. Two dedicated timing cards
show a highlighted latest observation, an exceptional-outcome badge and task timestamp,
then four separate median/mean/maximum/P95 tiles. Units appear in the header;
sample counts appear below the tiles; P95 uses a small-sample marker with hover help.
Routine completed badges and the extra response-time section heading are omitted.
Field definitions live on card labels; observation tooltips retain the task timestamp
and outcome. The four overview titles use the same prominent card-title style as
the duration titles. Timing cards stack
below 700 logical pixels of content width. Colors supplement text, never replace it.
Preview rows support mouse,
Enter and Space activation with plain-text labels.

`statistics_view.py` contains localized card labels and duration formatting. The three
detail table tabs and Data health block were removed following user review; their
unused table widgets were also removed. Database collection, query contracts and CLI
task inspection remain available. Read failures and query caps still appear on the
overview, and missing timing values never become zero. See the
[trial plan](../validation/P1-activity-database.md) for the remaining real-use acceptance gate.
Exceptionally large metadata is constrained to a one-MiB summary payload by reducing
detail/group lists and marking partial results; scalar aggregates remain intact.
This leaves headroom below the owned pipe's two-MiB message limit.

## Diagnostics and packaging

Diagnostics separates logging controls, runtime checks/actions and a full-width
report card. Pause/resume and microphone reconnect retain the running-client
requirement. Recent diagnostics inspect only the latest month directories and up
to four newest diagnostic files, reading at most 128 KiB from each and displaying
at most 80 events. Partial JSON lines are skipped and the `content` field is
excluded. The report renders timestamp, level, message and remaining metadata as
readable blocks; its copy action writes only that displayed report. Monthly cost
summaries now belong to Statistics, while historical-month queries remain available
through the CLI. Neither view collects new user content or contacts a provider.

The client dependency set adds pinned `PySide6-Essentials`, `tomlkit` and Windows `pywin32`; QtWidgets needs no Addons package. Client PyInstaller analyses collect both entry scripts and filter each EXE's script table so windowed `CapsWriter.exe` executes only `start_desktop.pyw` and console `start_client.exe` executes only `start_client.py`, preserving common runtime hooks. Server analysis continues to exclude Qt. The release smoke workflow checks all three combined-package entry points. All local configuration and credentials remain excluded by the existing packaging policy. Qt's [high-DPI support](https://doc.qt.io/qt-6/highdpi.html) informs the layout, but synthetic scale-factor renders do not verify physical monitor movement, focus, accessibility clients or clean-machine release behavior. Renaming remains a separate TODO item.

The unified `start.ps1` requires explicit `-Server` and/or `-Client Gui|Console` selection; no arguments display help without starting anything. It replaces both previous PowerShell helpers while preserving separate Python GUI/console entries. GUI process launch must not use `SW_HIDE` to avoid a console: windowed Python already serves that purpose. Windows can honor startup `SW_HIDE` while Qt reports the main widget as visible. The shared reveal path checks native visibility after Qt show and resets hide/show when necessary, including tray activation, repeat launch and failure feedback. Native synthetic tests trigger the real tray QAction and assert `IsWindowVisible`, including minimized/maximized states.
