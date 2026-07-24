[CmdletBinding()]
param(
    [switch]$Wait
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$appDirectory = $PSScriptRoot
$venvScripts = Join-Path $appDirectory ".app-venv\Scripts"
$appPath = Join-Path $appDirectory "app.py"
$pathEntries = [System.Collections.Generic.List[string]]::new()

function Add-PathEntries {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }

    foreach ($entry in $Value.Split([IO.Path]::PathSeparator)) {
        $trimmed = $entry.Trim()
        if ($trimmed -and -not $pathEntries.Contains($trimmed)) {
            [void]$pathEntries.Add($trimmed)
        }
    }
}

Add-PathEntries $venvScripts

$ffmpegPathsFile = Join-Path $appDirectory "ffmpeg-paths.txt"
if (Test-Path -LiteralPath $ffmpegPathsFile -PathType Leaf) {
    foreach ($entry in Get-Content -LiteralPath $ffmpegPathsFile) {
        Add-PathEntries $entry
    }
}

if ($env:LOCALAPPDATA) {
    Add-PathEntries (
        Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    )
}

Add-PathEntries (
    [Environment]::GetEnvironmentVariable("Path", "User")
)
Add-PathEntries (
    [Environment]::GetEnvironmentVariable("Path", "Machine")
)
Add-PathEntries $env:Path
$env:Path = $pathEntries -join [IO.Path]::PathSeparator

$pythonName = if ($Wait) { "python.exe" } else { "pythonw.exe" }
$pythonPath = Join-Path $venvScripts $pythonName

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "SEEK is not installed. Run install-windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
    throw "SEEK could not find app.py in $appDirectory."
}

if ($Wait) {
    Push-Location $appDirectory
    try {
        & $pythonPath $appPath
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

$quotedAppPath = '"{0}"' -f $appPath
Start-Process `
    -FilePath $pythonPath `
    -ArgumentList $quotedAppPath `
    -WorkingDirectory $appDirectory
