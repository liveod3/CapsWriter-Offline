"""Read JSONL diagnostics as compact text; filter without creating another archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}


def iter_records(paths):
    """Stream files, retaining file/line coordinates for damaged records."""
    for path in paths:
        with path.open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError('Expected a JSON object')
                    yield record
                except ValueError:
                    yield {'level': 'WARNING', 'event': 'reader.invalid_record',
                           'message': f'Invalid JSON record at {path.name}:{number}'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path, help='One diagnostic file or a directory to read recursively')
    parser.add_argument('--level', choices=LEVELS, default='DEBUG', help='Minimum severity to show')
    parser.add_argument('--task', help='Exact task ID to show')
    parser.add_argument('--request', help='Exact LLM request ID to show')
    parser.add_argument('--content', action='store_true', help='Also print explicitly saved text copies (sensitive)')
    parser.add_argument('--json', action='store_true', help='Emit filtered JSONL; content requires --content too')
    args = parser.parse_args(argv)
    if not args.path.exists():
        parser.error('Diagnostic path does not exist')
    paths = sorted(args.path.rglob('*.jsonl*')) if args.path.is_dir() else [args.path]
    for row in iter_records(paths):
        if LEVELS.get(row.get('level'), 0) < LEVELS[args.level]:
            continue
        if args.task and row.get('task_id') != args.task:
            continue
        if args.request and row.get('request_id') != args.request:
            continue
        content = row.pop('content', None)
        if args.content and content is not None:
            row['content'] = content
        if args.json:
            print(json.dumps(row, ensure_ascii=False))
            continue
        identity = ' '.join(f'{key}={row[key]}' for key in ('task_id', 'socket_id', 'request_id', 'batch_id') if row.get(key))
        print(f"{row.get('timestamp', '')} {row.get('level', ''):7} "
              f"{row.get('component', '')} pid={row.get('pid', '')} {row.get('event', '')} {identity}")
        print('  ' + str(row.get('message', '')))
        if row.get('data'):
            print('  ' + json.dumps(row['data'], ensure_ascii=False))
        if args.content and content:
            print(json.dumps(content, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
