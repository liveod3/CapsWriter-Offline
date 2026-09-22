"""Query monthly LLM ledgers without importing audio/UI or contacting providers."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.i18n import Notice, initialize_tool_language, set_language, tr
from core.i18n.argparse import localize_parser_error
from core.llm_accounting.config import load_cost_config
from core.llm_accounting.ledger import read_month


class HelpFormatter(argparse.HelpFormatter):
    def start_section(self, heading):
        super().start_section(tr('cli.options') if heading == 'options' else heading)

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, tr('cli.usage') if prefix is None else prefix)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, tr('cli.error', prog=self.prog, message=localize_parser_error(message)))


def main(argv=None):
    initialize_tool_language()
    try:
        from config_client import ClientConfig
        config_directory = ROOT / getattr(ClientConfig, 'llm_config_dir', 'LLM')
    except ImportError:
        config_directory = ROOT / 'LLM'
    language = Parser(add_help=False)
    language.add_argument('--language', choices=['auto', 'en', 'zh-CN'])
    preliminary, _ = language.parse_known_args(argv)
    if preliminary.language:
        set_language(preliminary.language)
    parser = Parser(description=tr('cost.cli.description'), add_help=False,
                    formatter_class=HelpFormatter)
    parser.add_argument('-h', '--help', action='help', help=tr('cli.help'))
    parser.add_argument('--month', default=datetime.now().strftime('%Y-%m'), help=tr('cost.cli.month'))
    parser.add_argument('--directory', type=Path, help=tr('cost.cli.directory'))
    parser.add_argument('--config-dir', type=Path, default=config_directory, help=tr('cost.cli.config'))
    parser.add_argument('--json', action='store_true', help=tr('cost.cli.json'))
    parser.add_argument('--details', action='store_true', help=tr('cost.cli.details'))
    parser.add_argument('--language', choices=['auto', 'en', 'zh-CN'], help=tr('cost.cli.language'))
    args = parser.parse_args(argv)
    try:
        directory = args.directory
        if directory is None:
            config = load_cost_config(args.config_dir)
            directory = ROOT / config['tracking']['directory']
        report = read_month(directory, args.month, details=args.details)
    except Exception as exc:
        detail = (exc.args[0].localized() if exc.args and isinstance(exc.args[0], Notice)
                  else tr('cost.cli.failed', kind=type(exc).__name__))
        print(detail, file=sys.stderr)
        return 1
    if args.json or args.details:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(tr('cost.cli.header', month=report['month'], requests=report['requests'],
             unknown=report['unknown_cost_requests']))
    for status, count in report['statuses'].items():
        print(tr('cost.cli.status', status=tr('cost.status.' + status), count=count))
    for currency, sums in sorted(report['currencies'].items()):
        print(tr('cost.cli.currency', currency=currency, reported=sums['provider_reported'],
                 estimated=sums['rate_estimate'], tokens=sums['token_estimate'],
                 possible=sums['possible_cost'], total=sums['planning_total']))
    for model in report['models']:
        print(tr('cost.cli.model', provider=model['provider'], model=model['model'],
                 count=model['requests'], unknown=model['unknown']))
    print(tr('cost.cli.note'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
