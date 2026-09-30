"""Exercise the Windows source launcher without starting application processes."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell required")

HARNESS = r"""
param($Launcher, $Options, $OutputPath, $EnvironmentPath, $Culture, $FailLaunch)
$ErrorActionPreference = 'Stop'
$env:CONDA_PREFIX = $EnvironmentPath
$env:USERPROFILE = Split-Path -Parent $EnvironmentPath
$originalPath = $env:PATH
$global:launcherTestCalls = @()
function Get-UICulture { [pscustomobject]@{ Name = $Culture } }
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle)
    $global:launcherTestCalls += @{
        executable = $FilePath; arguments = @($ArgumentList)
        directory = $WorkingDirectory; style = "$WindowStyle"; path = $env:PATH
    }
    if ($FailLaunch -eq 'yes') { throw 'Synthetic launch failure' }
}
$parameters = @{}
(Get-Content -LiteralPath $Options -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties |
    ForEach-Object { $parameters[$_.Name] = $_.Value }
$failure = $null
try { & $Launcher @parameters }
catch { $failure = $_.Exception.Message }
@{ calls = @($global:launcherTestCalls); failure = $failure; restored = ($env:PATH -ceq $originalPath) } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
"""


@pytest.fixture
def launch(tmp_path):
    # Spaces and apostrophes exercise both native argv and generated shell quoting.
    checkout = tmp_path / "source user's copy"
    (checkout / "core/i18n").mkdir(parents=True)
    shutil.copyfile(ROOT / "start.ps1", checkout / "start.ps1")
    shutil.copyfile(ROOT / "core/i18n/launcher.json", checkout / "core/i18n/launcher.json")
    for name in ("start_server.py", "start_client.py", "start_desktop.pyw", "start_transcribe.pyw"):
        (checkout / name).touch()
    environment = tmp_path / "env user's copy" / "capswriter"
    environment.mkdir(parents=True)
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8")

    def run(options, *, prepared=True, culture="en-US", missing=None, fail=False):
        if prepared:
            for name in ("python.exe", "pythonw.exe"):
                (environment / name).touch()
        if missing:
            (checkout / missing).unlink()
        arguments = tmp_path / "options.json"
        arguments.write_text(json.dumps(options), encoding="utf-8")
        output = tmp_path / "result.json"
        result = subprocess.run(
            [
                POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(harness), str(checkout / "start.ps1"), str(arguments),
                str(output), str(environment), culture, "yes" if fail else "no",
            ],
            capture_output=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(output.read_text(encoding="utf-8-sig")), result.stdout

    return run


@pytest.mark.parametrize("options", [{}, {"Help": True}, {"WhatIf": True}, {"Server": False},
                                       {"Transcribe": False},
                                       {"Server": True, "Client": "Gui", "Transcribe": True,
                                        "Help": True}])
@pytest.mark.parametrize("culture", ["en-US", "zh-CN"])
def test_help_needs_no_environment_and_launches_nothing(launch, options, culture):
    result, output = launch(options, prepared=False, culture=culture)
    assert result["failure"] is None
    assert result["calls"] == []
    assert b"-Client Gui" in output and b"-Server" in output and b"-Transcribe" in output


@pytest.mark.parametrize("options,entries", [
    ({"Server": True}, ["start_server.py"]),
    ({"Client": "Console"}, ["start_client.py"]),
    ({"Client": "Gui"}, ["start_desktop.pyw"]),
    ({"Transcribe": True}, ["start_transcribe.pyw"]),
    ({"Server": True, "Transcribe": True}, ["start_server.py", "start_transcribe.pyw"]),
    ({"Client": "Gui", "Transcribe": True}, ["start_desktop.pyw", "start_transcribe.pyw"]),
    ({"Server": True, "Client": "Console", "Transcribe": True},
     ["start_server.py", "start_client.py", "start_transcribe.pyw"]),
    ({"Server": True, "Client": "Console"}, ["start_server.py", "start_client.py"]),
    ({"Server": True, "Client": "gui"}, ["start_server.py", "start_desktop.pyw"]),
])
def test_explicit_targets_and_preview(launch, options, entries):
    preview, _ = launch({**options, "WhatIf": True})
    assert preview["failure"] is None and preview["calls"] == []
    result, _ = launch(options)
    assert result["failure"] is None and result["restored"]
    assert len(result["calls"]) == len(entries)
    for call, entry in zip(result["calls"], entries):
        assert call["style"] == "Normal"
        if entry.endswith(".pyw"):
            assert Path(call["executable"]).name == "pythonw.exe"
            assert call["arguments"] == [f'"{Path(call["directory"]) / entry}"']
        else:
            command = base64.b64decode(call["arguments"][-1]).decode("utf-16-le")
            expected = str(Path(call["directory"]) / entry).replace("'", "''")
            assert f"'{expected}'" in command
            assert "-NoExit" in call["arguments"]


@pytest.mark.parametrize("options", [{"Client": "wrong"}, {"Client": ""},
                                       {"ClientOnly": True}, {"Desktop": True}])
def test_invalid_selection_cannot_launch(launch, options):
    result, _ = launch(options)
    assert result["failure"] and result["calls"] == []


@pytest.mark.parametrize("missing", ["start_desktop.pyw", "start_transcribe.pyw"])
def test_preflight_all_targets_before_starting_server(launch, missing):
    result, _ = launch({"Server": True, "Client": "Gui", "Transcribe": True}, missing=missing)
    assert result["failure"] and result["calls"] == []


@pytest.mark.parametrize("options", [{"Client": "Gui"}, {"Transcribe": True}])
def test_gui_failure_restores_parent_environment(launch, options):
    result, _ = launch(options, fail=True)
    assert result["failure"] == "Synthetic launch failure"
    assert len(result["calls"]) == 1 and result["restored"]
