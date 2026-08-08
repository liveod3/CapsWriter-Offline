[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$ServerOnly,
    [switch]$ClientOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($ServerOnly -and $ClientOnly) {
    throw '-ServerOnly 与 -ClientOnly 不能同时使用。'
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

    throw @'
找不到 Conda 环境 capswriter。
请先创建该环境，或确保它已登记在 %USERPROFILE%\.conda\environments.txt 中。
'@
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
Write-Host 'Conda 环境: capswriter' -ForegroundColor DarkGray
& $pythonLiteral $entryLiteral
if (`$LASTEXITCODE -ne 0) {
    Write-Host "进程已退出，代码: `$LASTEXITCODE" -ForegroundColor Red
}
"@
}

$projectRoot = $PSScriptRoot
$pythonPath = Resolve-CapsWriterPython
$environmentPath = Split-Path -Parent $pythonPath

$terminalCommand = Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue
if (-not $terminalCommand) {
    $terminalCommand = Get-Command 'powershell.exe' -ErrorAction Stop
}
$terminalPath = $terminalCommand.Source

$launches = @(
    @{
        Title = 'CapsWriter Server'
        Entry = Join-Path $projectRoot 'start_server.py'
    },
    @{
        Title = 'CapsWriter Client'
        Entry = Join-Path $projectRoot 'start_client.py'
    }
)

if ($ServerOnly) {
    $launches = @($launches | Where-Object { $_.Title -eq 'CapsWriter Server' })
}
elseif ($ClientOnly) {
    $launches = @($launches | Where-Object { $_.Title -eq 'CapsWriter Client' })
}

Write-Host "项目目录: $projectRoot"
Write-Host "Python: $pythonPath"

foreach ($launch in $launches) {
    $title = $launch.Title
    $entryPath = $launch.Entry
    if (-not (Test-Path -LiteralPath $entryPath -PathType Leaf)) {
        throw "找不到启动脚本: $entryPath"
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
        throw "生成的子终端命令存在语法错误: $($parseErrors[0].Message)"
    }

    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($childCommand)
    )

    if ($PSCmdlet.ShouldProcess($title, "在新终端中运行 $entryPath")) {
        Start-Process `
            -FilePath $terminalPath `
            -ArgumentList @('-NoLogo', '-NoProfile', '-NoExit', '-EncodedCommand', $encodedCommand) `
            -WorkingDirectory $projectRoot `
            -WindowStyle Normal
    }
}
