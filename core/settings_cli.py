"""File-only configuration commands; never import executable local settings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict

from core.config_reload import CandidateError
from core.i18n import Notice, localize_notice, set_language, tr
from core.i18n.argparse import LocalizedArgumentParser
from core.settings import SettingsService


def configure_parser(parser):
    parser.add_argument('--server', action='store_true', help=tr('settings.cli.server'))
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('show', help=tr('settings.cli.show'))
    commands.add_parser('check', help=tr('settings.cli.check'))
    prompt = commands.add_parser('prompt', help=tr('settings.cli.prompt'))
    prompt.add_argument('--preset', default='correct_asr', help=tr('settings.cli.preset'))
    edit = commands.add_parser('set', help=tr('settings.cli.set'))
    edit.add_argument('changes', metavar='JSON', help=tr('settings.cli.changes'))
    edit.add_argument('--revision', required=True, help=tr('settings.cli.revision'))


def add_settings_parser(subparsers):
    configure_parser(subparsers.add_parser('settings', help=tr('settings.cli.description')))


def public_values(values):
    """Omit configured credentials from console/JSON output at every depth."""
    if isinstance(values, dict):
        return {
            key: '<redacted>' if key in {'auth_token', 'api_key', 'password', 'secret'} else public_values(value)
            for key, value in values.items()
        }
    if isinstance(values, (list, tuple)):
        return [public_values(value) for value in values]
    return str(values) if isinstance(values, Path) else values


def main(argv=None, *, root=None):
    root = Path(root) if root is not None else (
        Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    )
    # Follow only a statically validated preference. Even broken/executable local
    # configuration must leave the file editor and its help accessible.
    client = SettingsService.standalone(root / 'config_client.py')
    preference = client.read()
    set_language(preference.saved['ClientConfig'].get('ui_language', 'auto') if preference.saved else 'auto')
    parser = LocalizedArgumentParser(prog='start_client settings', description=tr('settings.cli.description'))
    configure_parser(parser)
    args = parser.parse_args(argv)
    service = SettingsService.standalone(root / 'config_server.py', server=True) if args.server else client
    try:
        if args.action == 'set':
            try:
                raw = sys.stdin.read(1024 * 1024 + 1) if args.changes == '-' else args.changes
                if len(raw) > 1024 * 1024:
                    raise ValueError
                changes = json.loads(raw)
            except ValueError:
                raise CandidateError(Notice('settings.cli.invalid_json')) from None
            snapshot = service.save(changes, revision=args.revision)
        else:
            snapshot = service.read()
        if snapshot.error:
            raise CandidateError(snapshot.error)
        result = {
            'revision': snapshot.revision, 'valid': True,
            'effective': None, 'pending': None, 'restart_required': None,
            'message': tr('settings.cli.file_only'),
        }
        if args.action == 'show':
            result['saved'] = public_values(snapshot.saved[service.section])
        if args.action == 'prompt':
            if args.server:
                raise CandidateError(Notice('validation.prompt.client_only'))
            from core.correction_prompts import inspect_prompt

            config = SimpleNamespace(**snapshot.saved['ClientConfig'])
            preview = inspect_prompt(root / config.llm_config_dir, config, preset_id=args.preset)
            result['prompt'] = asdict(preview)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, SyntaxError) as exc:
        reason = exc.args[0] if exc.args and isinstance(exc.args[0], Notice) else type(exc).__name__
        print(tr('settings.cli.failed', reason=localize_notice(reason)), file=sys.stderr)
        return 2
