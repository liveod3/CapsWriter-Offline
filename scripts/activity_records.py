"""Inspect activity metadata or explicitly import existing cost ledgers."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.activity.queries import task_detail
from core.activity.store import import_legacy
from core.i18n import initialize_tool_language, tr


def main(argv=None):
    initialize_tool_language()
    from scripts.llm_costs import Parser, HelpFormatter
    parser = Parser(description=tr('gui.activity_cli'), formatter_class=HelpFormatter, add_help=False)
    parser.add_argument('-h', '--help', action='help', help=tr('cli.help'))
    parser.add_argument('--directory', required=True, type=Path, help=tr('gui.activity_cli_directory'))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--import-legacy', action='store_true', help=tr('gui.activity_cli_import'))
    action.add_argument('--task', help=tr('gui.activity_cli_task'))
    args = parser.parse_args(argv)
    try:
        report = import_legacy(args.directory) if args.import_legacy else task_detail(args.directory, args.task)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(tr('gui.activity_cli_failed', kind=type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
