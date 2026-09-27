"""Adapt Python 3.11 argparse errors without changing process-global gettext.

Match complete parser-generated templates, preserving argument names and values.
Application validation messages have already been translated at their source.
"""

import argparse
import sys
import re

from . import tr


_ERRORS = (
    (r'unrecognized arguments: (.*)', 'cli.parse.unrecognized'),
    (r'the following arguments are required: (.*)', 'cli.parse.required'),
    (r'one of the arguments (.*) is required', 'cli.parse.one_required'),
    (r'ambiguous option: (.*) could match (.*)', 'cli.parse.ambiguous'),
    (r'invalid choice: (.*) \(choose from (.*)\)', 'cli.parse.choice'),
    (r'not allowed with argument (.*)', 'cli.parse.conflict'),
    (r'ignored explicit argument (.*)', 'cli.parse.explicit'),
    (r'expected one argument', 'cli.parse.one'),
    (r'expected at most one argument', 'cli.parse.at_most_one'),
    (r'expected at least one argument', 'cli.parse.at_least_one'),
    (r'expected (\d+) arguments?', 'cli.parse.count'),
    (r'invalid (.*) value: (.*)', 'cli.parse.type'),
    (r'unknown parser (.*) \(choices: (.*)\)', 'cli.parse.parser'),
    (r'unexpected option string: (.*)', 'cli.parse.unexpected'),
)


def localize_parser_error(message):
    argument = re.fullmatch(r'argument ([^:]+): (.*)', message, flags=re.DOTALL)
    if argument:
        return tr('cli.parse.argument', value0=argument[1], value1=localize_parser_error(argument[2]))
    for pattern, message_id in _ERRORS:
        match = re.fullmatch(pattern, message, flags=re.DOTALL)
        if match:
            return tr(message_id, **{f'value{i}': value for i, value in enumerate(match.groups())})
    return message


class LocalizedHelpFormatter(argparse.HelpFormatter):
    def start_section(self, heading):
        heading_id = {'options': 'cli.options', 'positional arguments': 'cli.positionals'}.get(heading)
        super().start_section(tr(heading_id) if heading_id else heading)

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, tr('cli.usage') if prefix is None else prefix)


class LocalizedArgumentParser(argparse.ArgumentParser):
    """Localize product help without changing argparse's process-global gettext."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('formatter_class', LocalizedHelpFormatter)
        kwargs['add_help'] = False
        super().__init__(*args, **kwargs)
        self.add_argument('-h', '--help', action='help', help=tr('cli.help'))

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, tr('cli.error', prog=self.prog, message=localize_parser_error(message)))
