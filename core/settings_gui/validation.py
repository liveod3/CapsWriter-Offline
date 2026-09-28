"""Immediate field feedback; the shared service validates every complete write."""

from core.i18n import tr


def field_errors(values):
    errors = {}

    def reject(name, key):
        errors[name] = tr('gui.' + name) + ': ' + tr('gui.invalid.' + key)

    port = str(values['port'])
    digits = port.lstrip('0')
    if not port.isascii() or not port.isdigit() or not 1 <= len(digits) <= 5 or int(digits) > 65535:
        reject('port', 'port')
    if not values['addr'].strip() or any(c in values['addr'] for c in ('/', '\\', '\0', ' ')):
        reject('addr', 'host')
    language = values['language']
    if not language.strip() or len(language) > 32:
        reject('language', 'language')
    if values['idle_suspend_seconds'] <= 0:
        reject('idle_suspend_seconds', 'positive')
    duration, overlap = values['mic_seg_duration'], values['mic_seg_overlap']
    if not 0.1 <= duration <= 120:
        reject('mic_seg_duration', 'duration')
    if not 0 <= overlap <= 30 or overlap >= duration:
        reject('mic_seg_overlap', 'overlap')
    if values['audio_name_len'] > 200:
        reject('audio_name_len', 'name_length')
    for name in ('transcript_dir', 'audio_dir', 'tls_ca_file'):
        value = values[name]
        if '\0' in value or (name == 'transcript_dir' and not value.strip()):
            reject(name, 'path')
    return errors
