"""Comment-preserving, conflict-detecting transactions for static LLM files."""

from dataclasses import asdict
import os
from pathlib import Path
import re
import tempfile

import tomlkit

from core.config_reload import read_source
from core.file_lock import file_lock
from core.i18n import Notice
from core.llm_config import parse_catalog
from core.settings import SettingsConflict, source_revision


class CatalogEditor:
    def __init__(self, directory):
        self.directory = Path(directory)

    def documents(self):
        provider = self.directory / 'providers.toml'
        if not provider.exists():
            provider = self.directory / 'providers.template.toml'
        paths = [provider, self.directory / 'presets.toml']
        sources = [read_source(path) for path in paths]
        docs = [tomlkit.parse(source.decode('utf-8-sig')) for source in sources]
        revision = source_revision(b'\0'.join(str(p.name).encode() + b'\0' + s for p, s in zip(paths, sources)))
        return docs, revision

    @staticmethod
    def validate(docs):
        return parse_catalog(docs[0].unwrap().get('providers', {}), docs[1].unwrap().get('presets', {}))

    def read(self):
        docs, revision = self.documents()
        catalog = self.validate(docs)
        providers = {}
        for key, provider in catalog.providers.items():
            values = asdict(provider)
            values['has_api_key'] = bool(values.pop('api_key'))
            providers[key] = values
        return {'revision': revision, 'providers': providers,
                'presets': {key: asdict(preset) for key, preset in catalog.presets.items()}}

    def candidate(self, kind, identifier, changes, revision, delete=False):
        if kind not in ('providers', 'presets') or not isinstance(identifier, str):
            raise ValueError(Notice('gui.invalid_id'))
        docs, current = self.documents()
        if current != revision:
            raise SettingsConflict()
        self.validate(docs)
        index = 0 if kind == 'providers' else 1
        table = docs[index].setdefault(kind, tomlkit.table())
        if identifier not in table and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identifier):
            raise ValueError(Notice('gui.invalid_id'))
        if delete:
            table.pop(identifier, None)
        else:
            allowed = ({'kind', 'base_url', 'model', 'timeout', 'api_key_env', 'api_key'} if index == 0 else
                       {'name', 'provider', 'system_prompt', 'triggers', 'use_caret_context',
                        'temperature', 'max_tokens', 'prompt_mode'})
            if not isinstance(changes, dict) or not changes.keys() <= allowed:
                raise ValueError(Notice('settings.unknown_field'))
            entry = table.setdefault(identifier, tomlkit.table())
            for key, value in changes.items():
                if key not in entry or entry[key] != value:
                    entry[key] = value
            if index == 1 and entry.get('prompt_mode') == 'correction':
                entry.pop('system_prompt', None)
        catalog = self.validate(docs)
        return docs, index, catalog

    def save(self, kind, identifier, changes, *, revision, delete=False, default=None):
        with file_lock(self.directory / '.catalog.lock', timeout=2):
            docs, index, catalog = self.candidate(kind, identifier, changes, revision, delete)
            if default is not None and default not in catalog.presets:
                raise ValueError(Notice('validation.config.llm_default_preset_must_be_one_preset_id'))
            data = tomlkit.dumps(docs[index]).encode('utf-8')
            if len(data) > 1024 * 1024:
                raise ValueError(Notice('gui.protocol'))
            path = self.directory / ('providers.toml' if index == 0 else 'presets.toml')
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.settings-', delete=False) as out:
                    temporary = Path(out.name)
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                if self.documents()[1] != revision:
                    raise SettingsConflict()
                os.replace(temporary, path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        return self.read()
