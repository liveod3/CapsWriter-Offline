"""Device discovery never needs a real audio backend for regression coverage."""

import ast
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core.settings_gui import devices


def fake_audio(names):
    return SimpleNamespace(
        query_devices=lambda: [{'name': name, 'hostapi': api, 'max_input_channels': channels}
                               for name, api, channels in names],
        query_hostapis=lambda: [{'name': 'MME'}, {'name': 'Windows WASAPI'}],
        default=SimpleNamespace(device=(1, 0)),
    )


def test_inventory_filters_outputs_and_distinguishes_host_apis():
    result = devices.input_inventory(fake_audio([
        ('Speakers', 0, 0), ('Microphone', 1, 2), ('Microphone', 0, 1),
        ('Duplicate', 1, 1), ('duplicate', 1, 1),
    ]))
    assert result['default'] == 1 and not result['partial']
    assert [row['index'] for row in result['devices']] == [1, 2, 3, 4]
    assert result['devices'][0]['selector'] == 'Microphone, Windows WASAPI'
    assert [row['ambiguous'] for row in result['devices']] == [False, False, True, True]
    assert devices.validate_inventory(result) == result


def test_inventory_bounds_without_truncating_device_selectors():
    result = devices.input_inventory(fake_audio([('x' * 513, 0, 1)]))
    assert result['partial'] and not result['devices']
    result = devices.input_inventory(fake_audio([(f'Mic {index}', 0, 1) for index in range(300)]))
    assert result['partial'] and len(result['devices']) == devices.MAX_DEVICES


def test_visible_inputs_choose_endpoint_backend_without_merging_distinct_microphones():
    inventory = devices.input_inventory(fake_audio([
        ('Headset', 1, 1), ('Laptop microphone', 1, 2), ('Headset', 0, 1),
        ('Laptop microphone', 0, 2), ('Speakers', 1, 0),
    ]))
    inventory['devices'].append({'index': 8, 'name': 'PC Speaker kernel pin', 'api': 'Windows WDM-KS',
                                 'selector': 'PC Speaker kernel pin, Windows WDM-KS', 'ambiguous': False})
    assert [row['name'] for row in devices.visible_inputs(inventory)] == ['Headset', 'Laptop microphone']
    assert len(inventory['devices']) == 5  # Retain legacy selectors for compatibility checks.
    inventory['devices'] = [row for row in inventory['devices'] if row['api'] != 'Windows WASAPI']
    assert [row['api'] for row in devices.visible_inputs(inventory)] == ['MME', 'MME']
    assert devices.visible_inputs({'devices': [], 'default': -1}) == []


@pytest.mark.parametrize('frozen,executable,expected', [
    (False, 'C:/env/pythonw.exe', ['python.exe', 'start_client.py', '--list-input-devices']),
    (True, 'C:/app/start_desktop.exe', ['start_client.exe', '--list-input-devices']),
])
def test_probe_uses_same_installation_hidden_and_bounded(monkeypatch, frozen, executable, expected):
    inventory = devices.input_inventory(fake_audio([]))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(inventory).encode())
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, 'executable', executable)
    monkeypatch.setattr(devices.subprocess, 'run', run)
    assert devices.discover_inputs() == inventory
    command, options = calls[0]
    assert [Path(part).name for part in command] == expected
    assert options == {'capture_output': True, 'timeout': 8,
                       'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}


@pytest.mark.parametrize('failure,expected', [
    (subprocess.TimeoutExpired('private path', 8), 'timeout'),
    (OSError('private driver detail'), 'unavailable'),
    (SimpleNamespace(returncode=1, stdout=b'private driver detail'), 'unavailable'),
    (SimpleNamespace(returncode=0, stdout=b'[]'), 'unavailable'),
    (SimpleNamespace(returncode=0, stdout=b'x' * (devices.MAX_OUTPUT + 1)), 'unavailable'),
    (SimpleNamespace(returncode=0, stdout=b'\xff'), 'unavailable'),
])
def test_probe_failures_are_sanitized(monkeypatch, failure, expected):
    def run(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(devices.subprocess, 'run', run)
    assert devices.discover_inputs() == {'devices': [], 'default': -1, 'partial': False, 'error': expected}


def test_probe_entry_bypasses_client_and_configuration_imports(monkeypatch, capsys):
    import builtins
    import start_client
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith(('core.client', 'core.server', 'config_client', 'config_server')):
            pytest.fail('Device inventory imported an application resource')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setitem(sys.modules, 'sounddevice', fake_audio([('Synthetic mic', 0, 1)]))
    assert start_client.main(['--list-input-devices']) == 0
    assert json.loads(capsys.readouterr().out)['devices'][0]['name'] == 'Synthetic mic'


def test_gui_language_choices_match_server_contract():
    from core.settings_gui.choices import RECOGNITION_LANGUAGES
    from core.i18n import tr
    path = Path(__file__).resolve().parents[2] / 'core/server/engines/language.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    mapping = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.AnnAssign) and node.target.id == 'LANGUAGE_MAP')
    assert set(RECOGNITION_LANGUAGES) == set(mapping)
    assert all(tr('gui.asr_language.' + code) != 'gui.asr_language.' + code for code in mapping)
