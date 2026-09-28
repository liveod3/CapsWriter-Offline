"""Enumerate input-device metadata in a bounded, fresh PortAudio process."""

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_DEVICES = 256
MAX_OUTPUT = 512 * 1024


def visible_inputs(inventory):
    """Choose one endpoint backend, rather than exposing aliases and kernel pins."""
    rows = inventory['devices']
    apis = {row['api'] for row in rows}
    preferred = next((api for api in ('Windows WASAPI', 'Windows DirectSound', 'MME') if api in apis), None)
    if preferred is None:
        preferred = next((row['api'] for row in rows if row['index'] == inventory['default']), None)
        preferred = preferred or next(iter(sorted(apis)), None)
    return sorted((row for row in rows if row['api'] == preferred), key=lambda row: row['name'].casefold())


def input_inventory(audio):
    """Read public discovery APIs only; never open streams or refresh live PortAudio."""
    devices = audio.query_devices()
    apis = audio.query_hostapis()
    default = int(audio.default.device[0])
    rows = []
    skipped = False
    for index, info in enumerate(devices):
        if info['max_input_channels'] <= 0:
            continue
        name, api = str(info['name']), str(apis[info['hostapi']]['name'])
        # Do not truncate a selector: that could silently select a different device.
        if not name or len(name) > 512 or not api or len(api) > 128:
            skipped = True
            continue
        rows.append({'index': index, 'name': name, 'api': api, 'selector': name + ', ' + api})
    counts = Counter(row['selector'].casefold() for row in rows)
    for row in rows:
        row['ambiguous'] = counts[row['selector'].casefold()] > 1
    return {'devices': rows[:MAX_DEVICES], 'default': default,
            'partial': skipped or len(rows) > MAX_DEVICES, 'error': None}


def probe_main():
    """Early client entry, isolated from executable settings and application resources."""
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32')
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002)
    try:
        import sounddevice
        result = input_inventory(sounddevice)
    except Exception:
        result = {'devices': [], 'default': -1, 'partial': False, 'error': 'unavailable'}
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + '\n')
    return 0


def validate_inventory(value):
    if not isinstance(value, dict) or value.get('error') not in (None, 'unavailable'):
        raise ValueError('Invalid device inventory')
    rows = value.get('devices')
    if (not isinstance(rows, list) or len(rows) > MAX_DEVICES
            or type(value.get('default')) is not int or type(value.get('partial')) is not bool):
        raise ValueError('Invalid device inventory')
    for row in rows:
        if (not isinstance(row, dict) or type(row.get('index')) is not int or row['index'] < 0
                or type(row.get('ambiguous')) is not bool
                or not isinstance(row.get('name'), str) or not 1 <= len(row['name']) <= 512
                or not isinstance(row.get('api'), str) or not 1 <= len(row['api']) <= 128
                or row.get('selector') != row['name'] + ', ' + row['api']):
            raise ValueError('Invalid device inventory')
    return value


def discover_inputs():
    """A fresh process discovers hotplug changes without reinitializing a live stream."""
    executable = Path(sys.executable)
    if getattr(sys, 'frozen', False):
        command = [str(executable.with_name('start_client.exe'))]
    else:
        if executable.name.casefold() == 'pythonw.exe':
            executable = executable.with_name('python.exe')
        command = [str(executable), str(Path(__file__).resolve().parents[2] / 'start_client.py')]
    try:
        completed = subprocess.run(
            command + ['--list-input-devices'], capture_output=True, timeout=8,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if completed.returncode or len(completed.stdout) > MAX_OUTPUT:
            raise ValueError('Device probe failed')
        return validate_inventory(json.loads(completed.stdout))
    except subprocess.TimeoutExpired:
        error = 'timeout'
    except (OSError, ValueError):
        error = 'unavailable'
    return {'devices': [], 'default': -1, 'partial': False, 'error': error}
