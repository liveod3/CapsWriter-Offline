"""File editor CLI remains independent of local executable config and hardware."""

import json
import io
import subprocess
import sys
from pathlib import Path

import pytest

from core.settings_cli import main


def client_file(tmp_path):
    path = tmp_path / 'config_client.py'
    path.write_text(
        "class ClientConfig:\n    ui_language = 'en'\n    auth_token = 'private-test-token'\n",
        encoding='utf-8',
    )
    return path


def test_cli_show_check_save_and_conflict_share_validation(tmp_path, capsys):
    path = client_file(tmp_path)
    assert main(['show'], root=tmp_path) == 0
    output = capsys.readouterr().out
    assert 'private-test-token' not in output
    saved = json.loads(output)
    assert saved['effective'] is None and saved['pending'] is None
    assert saved['saved']['auth_token'] == '<redacted>'
    revision = saved['revision']
    assert main(['set', '{"ui_language":"zh-CN"}', '--revision', revision], root=tmp_path) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['revision'] != revision
    assert "ui_language = 'zh-CN'" in path.read_text(encoding='utf-8')
    stable = path.read_bytes()
    assert main(['set', '{"save_audio":true}', '--revision', revision], root=tmp_path) == 2
    assert capsys.readouterr().err
    assert path.read_bytes() == stable
    assert main(['check'], root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out)['valid']
    assert main(['set', '{"save_audio":"yes"}', '--revision', result['revision']], root=tmp_path) == 2
    assert path.read_bytes() == stable


@pytest.mark.parametrize('changes', ['{', '[]', '"private-invalid-value"', '{"save_audio":NaN}'])
def test_cli_rejects_bad_input_without_echoing_values(tmp_path, capsys, changes):
    path = client_file(tmp_path)
    main(['show'], root=tmp_path)
    revision = json.loads(capsys.readouterr().out)['revision']
    original = path.read_bytes()
    assert main(['set', changes, '--revision', revision], root=tmp_path) == 2
    assert 'private-invalid-value' not in capsys.readouterr().err
    assert path.read_bytes() == original


def test_server_edit_is_validated_without_starting_server(tmp_path, capsys):
    client_file(tmp_path)
    path = tmp_path / 'config_server.py'
    path.write_text("class ServerConfig:\n    ui_language = 'auto'\n", encoding='utf-8')
    assert main(['--server', 'show'], root=tmp_path) == 0
    revision = json.loads(capsys.readouterr().out)['revision']
    assert main(['--server', 'set', '{"format_num":false}', '--revision', revision], root=tmp_path) == 0
    assert 'format_num = False' in path.read_text(encoding='utf-8')


def test_entrypoint_settings_never_imports_config_audio_or_ui(tmp_path):
    client_file(tmp_path)
    script = '''
import functools
import importlib.abc
import sys

class RejectHardware(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'config_client', 'config_server', 'sounddevice', 'tkinter', 'core.client.app', 'core.server.app'}:
            raise AssertionError('Unexpected import: ' + fullname)

sys.meta_path.insert(0, RejectHardware())
import core.settings_cli
core.settings_cli.main = functools.partial(core.settings_cli.main, root=sys.argv[1])
import start_client
raise SystemExit(start_client.main(['settings', 'check']))
'''
    result = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['valid']


def test_broken_executable_config_does_not_block_settings_help(tmp_path, capsys):
    path = tmp_path / 'config_client.py'
    source = "raise AssertionError('private-do-not-execute')\n"
    path.write_text(source, encoding='utf-8')
    with pytest.raises(SystemExit) as help_exit:
        main(['--help'], root=tmp_path)
    assert help_exit.value.code == 0
    capsys.readouterr()
    assert main(['check'], root=tmp_path) == 2
    assert 'private-do-not-execute' not in capsys.readouterr().err
    assert path.read_text(encoding='utf-8') == source


def test_cli_stdin_edits_support_shell_pipelines(tmp_path, capsys, monkeypatch):
    path = client_file(tmp_path)
    main(['show'], root=tmp_path)
    revision = json.loads(capsys.readouterr().out)['revision']
    monkeypatch.setattr('sys.stdin', io.StringIO('{"ui_language":"zh-CN"}\n'))
    assert main(['set', '-', '--revision', revision], root=tmp_path) == 0
    assert "ui_language = 'zh-CN'" in path.read_text(encoding='utf-8')
