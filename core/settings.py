"""Application-facing configuration transactions, independent of UI and hardware.

All file operations are blocking: desktop adapters must run them off their owner
loop. Saved values are never published here; the application owns safe admission.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import os
import tempfile
import tokenize
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from core.config_reload import (
    CandidateError, ConfigReloader, CLIENT_LIVE, SERVER_LIVE, read_source, restart_fields,
)
from core.file_lock import file_lock
from core.i18n import Notice


class SettingsConflict(CandidateError):
    """The editor's revision no longer matches the on-disk document."""

    def __init__(self):
        super().__init__(Notice('settings.conflict'))


@dataclass(frozen=True)
class SettingsSnapshot:
    """Detached private values; repr and notices deliberately omit their contents.

    None effective/pending/restart means no running process is attached, rather
    than a claim that the file is already effective in another process.
    """

    revision: str | None
    saved: dict | None = field(repr=False)
    effective: dict | None = field(repr=False)
    pending: tuple[str, ...] | None
    restart_required: tuple[str, ...] | None
    error: Notice | str | None = None


def source_revision(source):
    return hashlib.sha256(source).hexdigest()


def patch_source(original: bytes, section: str, values: dict) -> bytes:
    """Replace literal values only; retain unrelated code, comments and encoding."""
    source = original.decode('utf-8-sig')
    tree = ast.parse(source)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == section]
    if len(classes) != 1:
        raise CandidateError(Notice('settings.class_missing'))
    config = classes[0]
    data = source.encode('utf-8')
    lines = data.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    comment_offsets = [
        offsets[token.start[0] - 1]
        + len(token.line[:token.start[1]].encode('utf-8'))
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    edits = []
    found = set()
    for node in config.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in values:
                continue
            if target.id in found or len(targets) != 1 or node.value is None:
                raise CandidateError(Notice('validation.settings.ambiguous_text_mode_assignment'))
            found.add(target.id)
            value = node.value
            replacement = repr(values[target.id])
            try:
                if repr(ast.literal_eval(value)) == replacement:
                    continue
            except (ValueError, SyntaxError):
                pass
            start = offsets[value.lineno - 1] + value.col_offset
            end = offsets[value.end_lineno - 1] + value.end_col_offset
            if any(start <= position < end for position in comment_offsets):
                # A whole-field replacement cannot preserve the attachment of
                # comments inside a list/dict expression. Require explicit file editing.
                raise CandidateError(Notice('settings.commented_value', field=target.id))
            edits.append((start, end, replacement.encode('utf-8')))
    missing = values.keys() - found
    if missing:
        # Single-line classes have no reusable body indentation. Refuse ambiguous edits.
        if config.body[0].lineno == config.lineno:
            raise CandidateError(Notice('settings.multiline_class'))
        newline = b'\r\n' if b'\r\n' in data else b'\n'
        indent = lines[config.body[0].lineno - 1][:config.body[0].col_offset]
        addition = newline + newline.join(
            indent + f'{name} = {values[name]!r}'.encode('utf-8') for name in sorted(missing)
        ) + newline
        position = offsets[config.end_lineno]
        edits.append((position, position, addition))
    for start, end, replacement in sorted(edits, reverse=True):
        data = data[:start] + replacement + data[end:]
    ast.parse(data)
    return (b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b'') + data


class SettingsService:
    """Revision-checked read/validate/save shared by tray, CLI and future GUI.

    Structured edits are limited to the main client/server section. Additional
    template classes participate in validation and restart reporting, but retain
    their expressions. Provider/preset TOML continues to use its existing loader.
    """

    def __init__(self, reloader: ConfigReloader, *, attached=True):
        self.reloader = reloader
        self.attached = attached
        self.path = reloader.path
        self.section = reloader.section

    @classmethod
    def standalone(cls, path, *, server=False):
        from config_templates import config_client_template, config_server_template

        template = config_server_template if server else config_client_template
        section = 'ServerConfig' if server else 'ClientConfig'
        reloader = ConfigReloader(
            path, template, template, section, SERVER_LIVE if server else CLIENT_LIVE, lambda _: None,
        )
        # This is a file editor, not a snapshot of an independently running process.
        reloader.required = {}
        return cls(reloader, attached=False)

    def _snapshot(self, source, values, parsed):
        current = self.reloader.effective() if self.attached else None
        pending = restart = None
        if current is not None:
            pending = tuple(sorted(
                f'{self.section}.{name}' for name, value in values[self.section].items()
                if name in self.reloader.live and value != current[self.section][name]
            ))
            restart = restart_fields(values, current, self.section, self.reloader.live)
            restart.extend(
                key for key, value in self.reloader.metadata.items() if parsed.get(key) != value
            )
            restart = tuple(sorted(restart))
        return SettingsSnapshot(source_revision(source), copy.deepcopy(values), current, pending, restart)

    def read(self):
        """Report invalid files without losing the last effective application state."""
        source = None
        try:
            source = read_source(self.path)
            values, parsed = self.reloader.prepare(source)
            return self._snapshot(source, values, parsed)
        except (OSError, ValueError) as exc:
            error = exc.args[0] if isinstance(exc, CandidateError) else type(exc).__name__
            return SettingsSnapshot(
                source_revision(source) if source is not None else None, None,
                self.reloader.effective() if self.attached else None, None, None, error,
            )

    def _candidate(self, changes, revision):
        original = read_source(self.path)
        if source_revision(original) != revision:
            raise SettingsConflict()
        # Refuse to silently repair or partially overwrite an invalid external edit.
        self.reloader.prepare(original)
        if not isinstance(changes, dict) or not changes.keys() <= self.reloader.defaults[self.section].keys():
            raise CandidateError(Notice('settings.unknown_field'))
        # Only literals may enter the source writer. Never serialize executable objects.
        try:
            ast.literal_eval(repr(changes))
        except (ValueError, SyntaxError):
            raise CandidateError(Notice('settings.literal_required')) from None
        data = patch_source(original, self.section, changes)
        values, parsed = self.reloader.prepare(data)
        return original, data, values, parsed

    def validate(self, changes, *, revision):
        """Preview a full candidate without writing or publishing it."""
        original, data, values, parsed = self._candidate(changes, revision)
        preview = self._snapshot(data, values, parsed)
        # Keep the editor's base revision, so a subsequent save still detects conflicts.
        return SettingsSnapshot(
            source_revision(original), preview.saved, preview.effective,
            preview.pending, preview.restart_required,
        )

    def save(self, changes, *, revision):
        """Save atomically; cooperating writers serialize, stale editors must refresh."""
        lock_path = self.path.with_name('.' + self.path.name + '.lock')
        with file_lock(lock_path, timeout=2):
            original, data, values, parsed = self._candidate(changes, revision)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.settings-', delete=False) as out:
                    temporary = Path(out.name)
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                # No I/O is performed under the reloader's publication lock.
                with self.reloader.editing() if self.attached else nullcontext():
                    if read_source(self.path) != original:
                        raise SettingsConflict()
                    os.replace(temporary, self.path)
                return self._snapshot(data, values, parsed)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
