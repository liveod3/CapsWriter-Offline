import os
from collections.abc import Iterable
from pathlib import Path

# Git-tracked client configuration defaults.
# Copy this file to the repository root as config_client.py for first use.
# Keep local settings in that copy. Do not move or edit this template for local use.

# Configuration version.
__version__ = '2.6'

# Application directory when copied to the repository root.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# Client configuration.
# Supported settings reload after active tasks finish. Resource settings require
# restart; see docs/reference/configuration.md for the exact policy and feedback.
class ClientConfig:
    # Interface language only: 'auto' (system), 'en', or 'zh-CN'. Reloads between tasks.
    # The server in this installation also follows this saved preference.
    ui_language = 'auto'

    addr = '127.0.0.1'          # Server address.
    port = '6016'               # Server port.

    # Use the same environment token as the remote LAN server.
    auth_token = os.environ.get('CAPSWRITER_AUTH_TOKEN', '')

    # Enable for a TLS server; tls_ca_file can trust a private CA or self-signed certificate.
    use_tls = False
    tls_ca_file = ''

    # Bound response message size and the number of queued messages.
    websocket_max_message_bytes = 16 * 1024 * 1024
    websocket_max_queue = 16
    file_max_inflight_chunks = 4

    # Per-operation deadline for file connection, decoding and sends (seconds).
    file_io_timeout = 60.0
    # Maximum wait without advancing recognition progress, not total file time.
    # Increase for slow hardware or heavily concurrent file/dictation workloads.
    file_result_timeout = 600.0

    # Per-message microphone upload deadline (seconds).
    mic_io_timeout = 60.0
    # Final ASR result deadline from final-message submission; excludes LLM work.
    # Increase for slow hardware or concurrent recognition workloads.
    mic_result_timeout = 600.0

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

    threshold    = 0.3          # Shortcut activation threshold in seconds.

    paste        = False        # Output through the clipboard and simulated Ctrl+V.
    restore_clip = True         # Restore the clipboard after pasting.
    paste_apps   = ['WeiXin.exe', 'Telegram.exe']  # Always paste into matching applications.

    enter_apps   = [('happ.exe', 0.5), ('hexin.exe', 0.5)]  # (Executable, delay in seconds): press Enter after output, for example to select a stock.

    save_audio = False           # Save microphone recordings.
    # Empty: per-user LOCALAPPDATA/CapsWriter-Offline/audio on Windows.
    # Portable: 'audio-data'; relative paths are based on the application folder.
    # Absolute paths may use another drive. Existing recordings are never moved.
    audio_dir = ''
    audio_name_len = 20         # Number of leading transcript characters in audio filenames; keep below 200.
    
    language = 'auto'           # ASR language: 'auto', 'chinese', 'english', 'japanese', etc.; support varies by engine.

    trash_punc = '，。,.'       # Trailing punctuation to remove from recognition results.
    trash_punc_thresh = 8       # Remove trailing punctuation below this word-count threshold.
    trash_punc_apps = ['WeiXin.exe', ]   # Always remove trailing punctuation in these applications.

    traditional_convert = False     # Convert recognition results to Traditional Chinese.
    traditional_locale = 'zh-hant'  # Traditional locale: 'zh-hant' (standard), 'zh-tw' (Taiwan), or 'zh-hk' (Hong Kong).


    llm_enabled = False          # Master LLM switch; the tray can enable or disable all actions and save the setting.
    llm_correction_enabled = True   # Correction switch, subject to the master switch.
    llm_translation_enabled = True  # Translation switch; explicit translation commands trigger it by default.

    # Save dictation text independently of microphone audio.
    save_transcripts = True
    transcript_dir = 'logs/transcripts'
    transcript_save_original = False
    save_llm_records = False

    # Read text near the dictation insertion point; disabled means no text-control access.
    caret_context_enabled = False
    caret_context_before_chars = 800
    caret_context_after_chars = 200

    # At most one automatic preset; None requires an explicit trigger. Subject to llm_enabled.
    # Disabling a capability blocks both its automatic and explicit invocation.
    llm_default_preset = 'correct_asr'
    llm_config_dir = 'LLM'
    # Archive diagnostics by year/month; 0 disables expiry. Does not control transcript archives.
    save_diagnostic_logs = True
    diagnostic_log_retention_days = 30

    llm_stop_key = 'esc'        # Shortcut to cancel LLM output.

    enable_tray = True          # Enable the client tray icon.

    # Logging settings.
    log_level = 'DEBUG'          # Log level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', or 'CRITICAL'.

    mic_seg_duration = 60       # Microphone segment duration in seconds.
    mic_seg_overlap = 4         # Microphone segment overlap in seconds.
    # Input: None/'' follows the system default; otherwise use an index or unique name with Host API.
    # Example: 'Microphone (Realtek(R) Audio), Windows WASAPI'.
    input_device = None
    enable_idle_suspend = True  # Release the microphone when idle to avoid keeping headsets in call mode.
    idle_suspend_seconds = 20   # Idle time in seconds before suspension.

    # File defaults apply unless overridden for this CLI run; CLI options do not update configuration.
    file_seg_duration = 60      # File segment duration in seconds.
    file_seg_overlap = 4        # File segment overlap in seconds.
    file_scan_recursive = True  # Scan recursively unless --recursive/--no-recursive overrides this setting.
    # Write a separate log for each file transcription run under logs/transcribe/YYYY/MM/.
    file_separate_log = True
    file_media_extensions = (   # Media extensions included when scanning directories.
        '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma',
        '.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v', '.ts',
    )

    # Use these four output switches unless --format replaces the set for this run.
    file_save_srt = True        # Save SRT subtitles.
    file_save_txt = True        # Save TXT split at punctuation.
    file_save_json = True       # Save JSON results with original timestamps.
    file_save_merge = False     # Save merge.txt with unsplit paragraphs.

    udp_broadcast = False               # Broadcast recognition results over UDP.
    udp_broadcast_targets = [           # UDP destinations as (address, port) pairs.
        ('127.255.255.255', 6017),      # Local loopback broadcast.
        # ('192.168.1.255', 6017),      # Optional LAN broadcast example.
    ]

    udp_control = False             # Accept external START/STOP recording commands over UDP.
    udp_control_addr = '127.0.0.1'  # UDP control bind address; '0.0.0.0' allows external access.
    udp_control_port = 6018         # UDP control port.


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
