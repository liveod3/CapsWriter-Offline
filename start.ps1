[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Server,
    [ValidateSet('Gui', 'Console')]
    [ValidateNotNullOrEmpty()]
    [string]$Client,
    [switch]$Transcribe,
    [Alias('h')]
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Help must work before Python/environment discovery or local configuration loading.
$catalog = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'core\i18n\launcher.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
$messages = $catalog.en
if ((Get-UICulture).Name -like 'zh*') {
    $messages = $catalog.'zh-CN'
}
if ($Help -or (-not $Server -and -not $Client -and -not $Transcribe)) {
    $messages.help -join [Environment]::NewLine
    return
}

function Resolve-CapsWriterPython {
    $environmentPaths = @()

    if ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -ieq 'capswriter') {
        $environmentPaths += $env:CONDA_PREFIX
    }

    if ($env:USERPROFILE) {
        $registryPath = Join-Path $env:USERPROFILE '.conda\environments.txt'
        if (Test-Path -LiteralPath $registryPath -PathType Leaf) {
            foreach ($line in Get-Content -LiteralPath $registryPath) {
                $environmentPath = $line.Trim()
                if ($environmentPath -and (Split-Path -Leaf $environmentPath) -ieq 'capswriter') {
                    $environmentPaths += $environmentPath
                }
            }
        }
    }

    foreach ($environmentPath in ($environmentPaths | Select-Object -Unique)) {
        $pythonPath = Join-Path $environmentPath 'python.exe'
        if (Test-Path -LiteralPath $pythonPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $pythonPath).Path
        }
    }

    throw $messages.environment_missing
}

function ConvertTo-PowerShellLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)

    return "'" + $Value.Replace("'", "''") + "'"
}

function New-ChildCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$EnvironmentPath,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$EntryPath
    )

    $titleLiteral = ConvertTo-PowerShellLiteral $Title
    $rootLiteral = ConvertTo-PowerShellLiteral $ProjectRoot
    $environmentLiteral = ConvertTo-PowerShellLiteral $EnvironmentPath
    $pythonLiteral = ConvertTo-PowerShellLiteral $PythonPath
    $entryLiteral = ConvertTo-PowerShellLiteral $EntryPath
    $environmentMessage = ConvertTo-PowerShellLiteral $messages.environment
    $exitMessage = ConvertTo-PowerShellLiteral $messages.exit_code

    return @"
`$Host.UI.RawUI.WindowTitle = $titleLiteral
Set-Location -LiteralPath $rootLiteral
`$environmentPath = $environmentLiteral
`$environmentBins = @(
    `$environmentPath
    (Join-Path `$environmentPath 'Library\mingw-w64\bin')
    (Join-Path `$environmentPath 'Library\usr\bin')
    (Join-Path `$environmentPath 'Library\bin')
    (Join-Path `$environmentPath 'Scripts')
    (Join-Path `$environmentPath 'bin')
) | Where-Object { Test-Path -LiteralPath `$_ }
`$env:CONDA_DEFAULT_ENV = 'capswriter'
`$env:CONDA_PREFIX = `$environmentPath
`$env:CONDA_SHLVL = '1'
`$env:PATH = (`$environmentBins -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + `$env:PATH
Write-Host $environmentMessage -ForegroundColor DarkGray
& $pythonLiteral $entryLiteral
if (`$LASTEXITCODE -ne 0) {
    Write-Host ($exitMessage -f `$LASTEXITCODE) -ForegroundColor Red
}
"@
}

$projectRoot = $PSScriptRoot
$pythonPath = Resolve-CapsWriterPython
$environmentPath = Split-Path -Parent $pythonPath

$launches = @()
if ($Server) {
    $launches += @{
        Title = $messages.server_title
        Entry = Join-Path $projectRoot 'start_server.py'
        Gui = $false
    }
}
if ($Client -eq 'Console') {
    $launches += @{
        Title = $messages.console_title
        Entry = Join-Path $projectRoot 'start_client.py'
        Gui = $false
    }
}
if ($Client -eq 'Gui') {
    $launches += @{
        Title = $messages.gui_title
        Entry = Join-Path $projectRoot 'start_desktop.pyw'
        Gui = $true
        ActionFormat = $messages.gui_action
    }
}
if ($Transcribe) {
    $launches += @{
        Title = $messages.transcribe_title
        Entry = Join-Path $projectRoot 'start_transcribe.pyw'
        Gui = $true
        ActionFormat = $messages.transcribe_action
    }
}

Write-Host ($messages.project -f $projectRoot)
Write-Host ($messages.python -f $pythonPath)

# Validate and prepare every selected launch before starting any process.
foreach ($launch in $launches) {
    $title = $launch.Title
    $entryPath = $launch.Entry
    if (-not (Test-Path -LiteralPath $entryPath -PathType Leaf)) {
        throw ($messages.entry_missing -f $entryPath)
    }
    if ($launch.Gui) {
        $windowedPython = Join-Path $environmentPath 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $windowedPython -PathType Leaf)) {
            throw $messages.pythonw_missing
        }
        $launch.Executable = $windowedPython
        $launch.Arguments = @('"' + $entryPath + '"')
        $launch.Action = $launch.ActionFormat -f $entryPath
        continue
    }
    $terminalCommand = Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue
    if (-not $terminalCommand) {
        $terminalCommand = Get-Command 'powershell.exe' -ErrorAction Stop
    }
    $childCommand = New-ChildCommand `
        -Title $title `
        -ProjectRoot $projectRoot `
        -EnvironmentPath $environmentPath `
        -PythonPath $pythonPath `
        -EntryPath $entryPath

    $parseTokens = $null
    $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseInput(
        $childCommand,
        [ref]$parseTokens,
        [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) {
        throw ($messages.syntax_error -f $parseErrors[0].Message)
    }

    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($childCommand)
    )

    $launch.Executable = $terminalCommand.Source
    $launch.Arguments = @('-NoLogo', '-NoProfile', '-NoExit', '-EncodedCommand', $encodedCommand)
    $launch.Action = $messages.terminal_action -f $entryPath
}

foreach ($launch in $launches) {
    if ($PSCmdlet.ShouldProcess($launch.Title, $launch.Action)) {
        $previousPath = $env:PATH
        try {
            if ($launch.Gui) {
                $env:PATH = "$environmentPath;$environmentPath\Library\bin;$environmentPath\Scripts;$previousPath"
            }
            Start-Process -FilePath $launch.Executable -ArgumentList $launch.Arguments `
                -WorkingDirectory $projectRoot -WindowStyle Normal
        }
        finally {
            $env:PATH = $previousPath
        }
    }
}
