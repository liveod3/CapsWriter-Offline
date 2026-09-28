"""Small, explicit common-settings surface; advanced configuration remains editable."""

# A tuple describes a choice, None a nullable string, and scalar types typed inputs.
PAGES = {
    'general': {
        'ui_language': ('auto', 'zh-CN', 'en'), 'enable_tray': bool, 'start_minimized': bool,
        'language': str, 'input_device': None,
        'enable_idle_suspend': bool, 'idle_suspend_seconds': float,
        'mic_seg_duration': float, 'mic_seg_overlap': float,
    },
    'text': {
        'llm_enabled': bool, 'llm_correction_enabled': bool, 'llm_translation_enabled': bool,
        'llm_default_preset': None, 'llm_correction_level': ('minimal', 'natural', 'fluent'),
        'llm_correction_numbers': bool, 'llm_correction_punctuation': bool,
        'llm_correction_fillers': bool, 'llm_correction_english': bool,
        'llm_correction_homophones': bool, 'caret_context_enabled': bool,
        'caret_context_before_chars': int, 'caret_context_after_chars': int,
    },
    'services': {'addr': str, 'port': str, 'use_tls': bool, 'tls_ca_file': str},
    'records': {
        'paste': bool, 'restore_clip': bool, 'traditional_convert': bool,
        'traditional_locale': ('zh-hant', 'zh-tw', 'zh-hk'), 'trash_punc': str,
        'trash_punc_thresh': int, 'save_transcripts': bool, 'transcript_dir': str,
        'transcript_save_original': bool, 'save_llm_records': bool, 'save_llm_context': bool,
        'save_audio': bool, 'audio_dir': str, 'audio_name_len': int,
    },
    'diagnostics': {
        'save_diagnostic_logs': bool, 'log_level': ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        'diagnostic_include_text': bool, 'diagnostic_include_context': bool,
        'llm_cost_tracking': bool,
    },
}
