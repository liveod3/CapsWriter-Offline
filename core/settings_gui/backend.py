"""Blocking settings operations shared by standalone and attached GUI adapters."""

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from core.config_reload import CandidateError
from core.correction_prompts import resolve_preset, snapshot_options
from core.diagnostics import storage_path
from core.i18n import Notice, localize_notice
from core.llm_accounting.config import load_cost_config
from core.llm_accounting.ledger import read_month
from core.settings import SettingsService
from core.settings_cli import public_values
from .catalog import CatalogEditor


def safe_error(exc):
    reason = exc.args[0] if exc.args and isinstance(exc.args[0], Notice) else type(exc).__name__
    return localize_notice(reason)


class Backend:
    def __init__(self, root, operations=None):
        self.root = Path(root)
        self.operations = operations
        self.settings = operations.settings if operations else SettingsService.standalone(self.root / 'config_client.py')

    def setting(self, action, *args, **kwargs):
        if self.operations:
            future = self.operations.submit(getattr(self.operations, action + '_settings')(*args, **kwargs))
            if future is None:
                raise CandidateError(Notice('settings.stopped'))
            return future.result(timeout=10)
        return getattr(self.settings, action)(*args, **kwargs)

    def config(self, *, effective=False):
        snapshot = self.setting('read')
        if snapshot.error:
            raise CandidateError(snapshot.error)
        values = snapshot.effective if effective and snapshot.effective else snapshot.saved
        return values['ClientConfig']

    def editor(self):
        # The running service owns its directory until restart, even after a saved edit.
        return CatalogEditor(self.root / self.config(effective=True)['llm_config_dir'])

    def dispatch(self, method, params):
        if method == 'input_devices':
            from .devices import discover_inputs
            return discover_inputs()
        if method == 'status':
            if not self.operations:
                return None
            future = self.operations.submit(self.operations.read_status())
            return future.result(timeout=10) if future else None
        if method in ('advanced', 'advanced_path'):
            import subprocess
            path = (self.editor().directory / 'presets.toml' if params.get('file') == 'presets'
                    else self.root / 'config_client.py')
            if method == 'advanced_path':
                return str(path)
            subprocess.Popen(['notepad.exe', str(path)])
            return None
        if method in ('history_query', 'history_read', 'history_open', 'history_open_path'):
            from .history import day_path, query_history, read_history_entry
            config = self.config(effective=True)
            directory = storage_path(self.root, config.get('transcript_dir', 'records/transcripts'))
            if method == 'history_query':
                result = query_history(directory, **params)
                result['saving_enabled'] = bool(config.get('save_transcripts') or config.get('save_llm_records'))
                return result
            if method == 'history_read':
                from .history_detail import request_cost
                result = read_history_entry(directory, **params)
                result['cost'] = request_cost(self.root, config.get('llm_config_dir', 'LLM'),
                                              params['day'], result['stages']['request'])
                return result
            path = day_path(directory, params.get('day'))
            if not path.is_file():
                raise ValueError(Notice('gui.history_unavailable'))
            if method == 'history_open_path':
                return str(path)
            import subprocess
            subprocess.Popen(['notepad.exe', str(path)])
            return None
        if method == 'read':
            snapshot = self.setting('read')
            result = public_values(asdict(snapshot))
            result['error'] = localize_notice(snapshot.error) if snapshot.error else None
            if self.operations:
                result['runtime'] = self.dispatch('status', {})
            return result
        if method == 'action':
            if not self.operations or params.get('name') not in ('toggle_pause', 'reconnect_microphone'):
                raise CandidateError(Notice('settings.stopped'))
            future = self.operations.submit(getattr(self.operations, params['name'])())
            if future is None:
                raise CandidateError(Notice('settings.stopped'))
            result = future.result(timeout=10)
            return localize_notice(result) if isinstance(result, Notice) else None
        if method in ('save', 'validate'):
            if 'llm_default_preset' in params['changes']:
                default = params['changes']['llm_default_preset']
                if default is not None and default not in self.editor().read()['presets']:
                    raise CandidateError(Notice('validation.config.llm_default_preset_must_be_one_preset_id'))
            snapshot = self.setting(method, params['changes'], revision=params['revision'])
            return public_values(asdict(snapshot))
        if method == 'catalog':
            return self.editor().read()
        if method == 'catalog_save':
            return self.editor().save(**params, default=self.config().get('llm_default_preset'))
        if method == 'preview':
            snapshot = self.setting('validate', params['changes'], revision=params['revision'])
            config = SimpleNamespace(**snapshot.saved['ClientConfig'])
            editor = self.editor()
            if params.get('draft'):
                draft = params['draft']
                _, _, catalog = editor.candidate(**draft)
                identifier = draft['identifier']
            else:
                docs, _ = editor.documents()
                catalog = editor.validate(docs)
                identifier = params['identifier']
            preset = resolve_preset(catalog.presets[identifier], snapshot_options(config),
                                    context_enabled=config.caret_context_enabled)
            return {'system_prompt': preset.system_prompt, 'context_allowed': preset.use_caret_context}
        if method == 'diagnostics':
            return recent_diagnostics(self.root, self.config(effective=True))
        if method == 'costs':
            config = load_cost_config(self.editor().directory)
            return read_month(self.root / config['tracking']['directory'], params['month'])
        raise ValueError(Notice('settings.unknown_field'))


def recent_diagnostics(root, config):
    """Read bounded tails only; text/context copies stay in explicitly opened log files."""
    directory = storage_path(root, config['diagnostic_log_dir']) / 'client'
    # Bound directory traversal to recent month directories and files, not arbitrary archives.
    import heapq
    def modified(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0
    paths = []
    for year in heapq.nlargest(2, directory.glob('[0-9][0-9][0-9][0-9]')):
        for month in heapq.nlargest(2, year.glob('[0-9][0-9]')):
            paths.extend(heapq.nlargest(4, month.glob('client-*.jsonl*'), key=modified))
    records = []
    for path in sorted(paths, key=modified, reverse=True)[:4]:
        try:
            with path.open('rb') as stream:
                stream.seek(0, 2)
                start = max(0, stream.tell() - 128 * 1024)
                stream.seek(start)
                if start:
                    stream.readline()
                lines = stream.read(128 * 1024).splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    record.pop('content', None)
                    records.append(record)
            except (ValueError, UnicodeError):
                continue
    return sorted(records, key=lambda record: str(record.get('timestamp', '')))[-80:]
