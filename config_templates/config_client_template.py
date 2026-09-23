"""CapsWriter client settings.

Quick guide (Chinese): docs/user/logs-and-records.md
Exact logging/record contract: docs/reference/logging-and-records.md
Edit the root config copy. Keep assignments inside their classes.
Comments describe defaults; preserve your own values when upgrading.
"""

import os
from collections.abc import Iterable
from pathlib import Path

# Git-tracked client configuration defaults.
# Copy this file to the repository root as config_client.py for first use.
# Keep local settings in that copy. Do not move or edit this template for local use.

# Configuration version.
__version__ = '2.7'

# Application directory when copied to the repository root.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# Client configuration.
# Supported settings reload after active tasks finish. Resource settings require
# restart; see docs/reference/configuration.md for the exact policy and feedback.
class ClientConfig:

    # ----------------------------------------------------------------------------
    # 01  Interface
    # Language reloads between tasks; tray changes require restart.
    # ----------------------------------------------------------------------------

    # Interface language only: 'auto' (system), 'en', or 'zh-CN'. Reloads between tasks.
    # The server in this installation also follows this saved preference.
    ui_language = 'auto'

    # Enable the client tray icon.
    enable_tray = True

    # ----------------------------------------------------------------------------
    # 02  User records
    # Reload between tasks. User-owned content is NEVER automatically expired. See
    # docs/user/logs-and-records.md.
    # ----------------------------------------------------------------------------

    # Save final dictation text as daily Markdown. Independent of diagnostics and audio.
    save_transcripts = True

    # Daily history directory: YYYY/MM/DD.md. Absolute or application-relative; ~ and environment
    # supported.
    transcript_dir = 'records/transcripts'

    # Append differing ASR original text; requires save_transcripts. Final text is always retained.
    transcript_save_original = False

    # Append successful LLM input, final reply, system prompt and request/preset IDs to the SAME
    # history.
    save_llm_records = False

    # Sensitive: append only the caret reference actually sent. Requires save_llm_records; no extra
    # capture.
    save_llm_context = False

    # Save microphone audio separately; MP3 with FFmpeg, otherwise WAV. No automatic deletion.
    save_audio = False

    # Audio directory: YYYY/MM/. Empty keeps the legacy per-user LOCALAPPDATA location; old audio
    # stays put.
    audio_dir = 'records/audio'

    # Leading ASR characters in filenames, 0..200. Use 0 for timestamp-only filenames.
    audio_name_len = 20

    # ----------------------------------------------------------------------------
    # 03  Diagnostic logs
    # ALL changes below require restart. Files are JSONL; DEBUG does not enable text/context
    # capture.
    # ----------------------------------------------------------------------------

    # Save runtime diagnostics. False disables ALL application diagnostic files; console feedback
    # remains.
    save_diagnostic_logs = True

    # Root directory; client/ and server/ are created below it. Relative to the application folder.
    diagnostic_log_dir = 'logs'

    # DEBUG: detailed; INFO: milestones; WARNING: degraded; ERROR: failed; CRITICAL: service
    # unusable.
    log_level = 'DEBUG'

    # Sensitive: also save ASR text, LLM input/prompt/reply and final text. Independent of user
    # records.
    diagnostic_include_text = False

    # Sensitive: also save the caret reference actually sent to LLM. Requires include_text; client
    # only.
    diagnostic_include_context = False

    # Per text field, 1..65536 characters; longer text is explicitly marked truncated. INFO/DEBUG
    # only.
    diagnostic_text_max_chars = 16000

    # Delete inactive diagnostic sessions older than this many days; 0 disables AGE expiry only.
    diagnostic_log_retention_days = 30

    # Rotate each process file at approximately this many MiB; positive integer.
    diagnostic_log_file_mb = 10

    # Keep this many rotated files per process session; positive integer.
    diagnostic_log_backups = 5

    # Soft MiB budget PER client/server directory; oldest inactive sessions go first. Active files
    # stay.
    diagnostic_log_budget_mb = 200

    # ----------------------------------------------------------------------------
    # 04  Recording shortcuts
    # Restart required. See the key reference at the end of this file.
    # ----------------------------------------------------------------------------

    # Recording shortcuts.
    shortcuts = [
        {
            'key': 'ctrl_r',     # Listen for right Ctrl.
            'type': 'keyboard',     # Keyboard shortcut.
            'suppress': False,      # Allow the original key event.
            'hold_mode': False,      # Toggle recording: press once to start and again to stop.
            'enabled': True         # Enable this shortcut.
        },
        {
            'key': 'x2',
            'type': 'mouse',
            'suppress': False,
            'hold_mode': False,
            'enabled': True
        },
    ]

    # Shortcut activation threshold in seconds.
    threshold = 0.3

    # ----------------------------------------------------------------------------
    # 05  Text output
    # Reload between tasks. These settings change insertion/formatting, not diagnostic persistence.
    # ----------------------------------------------------------------------------

    # Output through the clipboard and simulated Ctrl+V.
    paste = False

    # Restore the clipboard after pasting.
    restore_clip = True

    # Always paste into matching applications.
    paste_apps = ['WeiXin.exe', 'Telegram.exe']

    # (Executable, delay in seconds): press Enter after output, for example to select a stock.
    enter_apps = [('happ.exe', 0.5), ('hexin.exe', 0.5)]

    # Trailing punctuation to remove from recognition results.
    trash_punc = '，。,.'

    # Remove trailing punctuation below this word-count threshold.
    trash_punc_thresh = 8

    # Always remove trailing punctuation in these applications.
    trash_punc_apps = ['WeiXin.exe', ]

    # Convert recognition results to Traditional Chinese.
    traditional_convert = False

    # Traditional locale: 'zh-hant' (standard), 'zh-tw' (Taiwan), or 'zh-hk' (Hong Kong).
    traditional_locale = 'zh-hant'

    # ----------------------------------------------------------------------------
    # 06  LLM and caret reference
    # Capability/context switches reload between tasks; directory and stop key require restart.
    # ----------------------------------------------------------------------------

    # Master LLM switch; the tray can enable or disable all actions and save the setting.
    llm_enabled = False

    # Correction switch, subject to the master switch.
    llm_correction_enabled = True

    # Translation switch; explicit translation commands trigger it by default.
    llm_translation_enabled = True

    # At most one automatic preset; None requires an explicit trigger. Subject to llm_enabled.
    # Disabling a capability blocks both its automatic and explicit invocation.
    llm_default_preset = 'correct_asr'

    llm_config_dir = 'LLM'

    # Shortcut to cancel LLM output.
    llm_stop_key = 'esc'

    # Content-free monthly accounting; configure LLM/costs.toml.
    llm_cost_tracking = True

    # Read text near the dictation insertion point; disabled means no text-control access.
    caret_context_enabled = False

    caret_context_before_chars = 800

    caret_context_after_chars = 200

    # ----------------------------------------------------------------------------
    # 07  Microphone and recognition
    # Language/segment/deadline values reload between tasks; hardware/idle settings require restart.
    # ----------------------------------------------------------------------------

    # ASR language: 'auto', 'chinese', 'english', 'japanese', etc.; support varies by engine.
    language = 'auto'

    # Input: None/'' follows the system default; otherwise use an index or unique name with Host
    # API.
    # Example: 'Microphone (Realtek(R) Audio), Windows WASAPI'.
    input_device = None

    # Release the microphone when idle to avoid keeping headsets in call mode.
    enable_idle_suspend = True

    # Idle time in seconds before suspension.
    idle_suspend_seconds = 20

    # Microphone segment duration in seconds.
    mic_seg_duration = 60

    # Microphone segment overlap in seconds.
    mic_seg_overlap = 4

    # Per-message microphone upload deadline (seconds).
    mic_io_timeout = 60.0

    # Final ASR result deadline from final-message submission; excludes LLM work.
    # Increase for slow hardware or concurrent recognition workloads.
    mic_result_timeout = 600.0

    # ----------------------------------------------------------------------------
    # 08  File transcription
    # Segment/deadline/window settings reload between files. Output/scan defaults apply on next
    # invocation.
    # ----------------------------------------------------------------------------

    # File defaults apply unless overridden for this CLI run; CLI options do not update
    # configuration.
    # File segment duration in seconds.
    file_seg_duration = 60

    # File segment overlap in seconds.
    file_seg_overlap = 4

    # Per-operation deadline for file connection, decoding and sends (seconds).
    file_io_timeout = 60.0

    # Maximum wait without advancing recognition progress, not total file time.
    # Increase for slow hardware or heavily concurrent file/dictation workloads.
    file_result_timeout = 600.0

    file_max_inflight_chunks = 4

    # Scan recursively unless --recursive/--no-recursive overrides this setting.
    file_scan_recursive = True

    # Media extensions included when scanning directories.
    file_media_extensions = (
        '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma',
        '.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v', '.ts',
    )

    # Use these four output switches unless --format replaces the set for this run.
    # Save SRT subtitles.
    file_save_srt = True

    # Save TXT split at punctuation.
    file_save_txt = True

    # Save JSON results with original timestamps.
    file_save_json = True

    # Save merge.txt with unsplit paragraphs.
    file_save_merge = False

    # Retired compatibility field; ignored. File tasks use the client diagnostic file.
    file_separate_log = False

    # ----------------------------------------------------------------------------
    # 09  Server connection
    # Restart required. LAN authentication is not encryption; enable TLS when needed.
    # ----------------------------------------------------------------------------

    # Server address.
    addr = '127.0.0.1'

    # Server port.
    port = '6016'

    # Use the same environment token as the remote LAN server.
    auth_token = os.environ.get('CAPSWRITER_AUTH_TOKEN', '')

    # Enable for a TLS server; tls_ca_file can trust a private CA or self-signed certificate.
    use_tls = False

    tls_ca_file = ''

    # Bound response message size and the number of queued messages.
    websocket_max_message_bytes = 16 * 1024 * 1024

    websocket_max_queue = 16

    # ----------------------------------------------------------------------------
    # 10  UDP integration
    # Restart required. Disabled by default; broadcasts can transmit transcription text.
    # ----------------------------------------------------------------------------

    # Broadcast recognition results over UDP.
    udp_broadcast = False

    # UDP destinations as (address, port) pairs.
    udp_broadcast_targets = [
        ('127.255.255.255', 6017),      # Local loopback broadcast.
        # ('192.168.1.255', 6017),      # Optional LAN broadcast example.
    ]

    # Accept external START/STOP recording commands over UDP.
    udp_control = False

    # UDP control bind address; '0.0.0.0' allows external access.
    udp_control_addr = '127.0.0.1'

    # UDP control port.
    udp_control_port = 6018


# Shortcut configuration reference.
r"""
Shortcut fields:
  key        - Key name from the list below.
  type       - Input type: 'keyboard' or 'mouse'.
  suppress   - Suppress the original key event when True.
  hold_mode  - True: hold to record; False: press once to start and again to stop.
  enabled    - Enable this shortcut.

Suppression and restoration:
  - Suppressed hold mode replays short presses below the threshold (default: 0.3 seconds).
  - Unsuppressed lock keys (CapsLock/NumLock/ScrollLock) are replayed to restore their state.

Available key names:

  Letters and digits: a - z, 0 - 9 (main keyboard).

  Symbols: , . / \ ` ' - = [ ] ; '


  Function keys: f1 - f24

  Modifiers:
      ctrl_l,   ctrl_r,
      shift,  shift_r,
      alt_l,    alt_gr,
      cmd,    cmd_r

  Special keys:
      space, enter, tab, backspace, delete, insert, home, end
      page_up, page_down, esc, caps_lock, num_lock, scroll_lock
      print_screen, pause, menu

  Arrow keys: up, down, left, right

  Mouse buttons: x1, x2

Example configurations:
  {'key': 'caps_lock', 'type': 'keyboard', 'suppress': False, 'hold_mode': True, 'enabled': True}, 
  {'key': 'f12', 'type': 'keyboard', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
  {'key': 'x2', 'type': 'mouse', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
"""
