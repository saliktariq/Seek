<#
.SYNOPSIS
Installs or repairs SEEK for the current Windows user.

.DESCRIPTION
Installs Python and FFmpeg through winget when needed, copies SEEK to a stable
per-user folder, creates a private Python environment, adds Desktop and Start
Menu shortcuts, checks the installation, and launches the application.

.PARAMETER InstallDir
The destination folder. The default is %LOCALAPPDATA%\SEEK.

.PARAMETER SkipSystemDependencies
Do not use winget. The installer fails if Python 3.10+, Tk, FFmpeg, or FFprobe
is unavailable.

.PARAMETER NoShortcuts
Do not create Desktop or Start Menu shortcuts.

.PARAMETER NoLaunch
Do not launch SEEK after a successful installation.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1

.EXAMPLE
.\install-windows.ps1 -NoLaunch -NoShortcuts
#>

[CmdletBinding()]
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\SEEK",
    [switch]$SkipSystemDependencies,
    [switch]$NoShortcuts,
    [switch]$NoLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sourceDirectory = $PSScriptRoot
$requiredSourceFiles = @(
    "app.py",
    "downloader.py",
    "requirements.txt",
    "README.md",
    "install-windows.ps1",
    "launch-windows.ps1"
)

function Write-Step {
    param([string]$Message)

    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable(
        "Path",
        "Machine"
    )
    $userPath = [Environment]::GetEnvironmentVariable(
        "Path",
        "User"
    )
    $wingetLinks = if ($env:LOCALAPPDATA) {
        Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    }
    else {
        ""
    }

    $env:Path = @(
        $wingetLinks,
        $userPath,
        $machinePath,
        $env:Path
    ) -join [IO.Path]::PathSeparator
}

function Test-PythonCandidate {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    try {
        $output = & $FilePath @Arguments -c (
            "import sys, tkinter; " +
            "print('.'.join(map(str, sys.version_info[:3])))"
        ) 2>$null
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0 -or -not $output) {
            return $null
        }

        $versionText = [string]($output | Select-Object -Last 1)
        $version = [version]$versionText.Trim()
        if ($version -lt [version]"3.10") {
            return $null
        }

        return [pscustomobject]@{
            FilePath = $FilePath
            Arguments = @($Arguments)
            Version = $version
        }
    }
    catch {
        return $null
    }
}

function Find-SuitablePython {
    $candidates = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        [void]$candidates.Add(
            [pscustomobject]@{
                FilePath = $pyLauncher.Source
                Arguments = @("-3")
            }
        )
    }

    foreach ($commandName in @("python.exe", "python3.exe")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            [void]$candidates.Add(
                [pscustomobject]@{
                    FilePath = $command.Source
                    Arguments = @()
                }
            )
        }
    }

    if ($env:LOCALAPPDATA) {
        $pythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
        if (Test-Path -LiteralPath $pythonRoot -PathType Container) {
            $pythonFolders = Get-ChildItem `
                -LiteralPath $pythonRoot `
                -Directory `
                -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending

            foreach ($folder in $pythonFolders) {
                $pythonPath = Join-Path $folder.FullName "python.exe"
                if (Test-Path -LiteralPath $pythonPath -PathType Leaf) {
                    [void]$candidates.Add(
                        [pscustomobject]@{
                            FilePath = $pythonPath
                            Arguments = @()
                        }
                    )
                }
            }
        }
    }

    foreach ($candidate in $candidates) {
        $key = "$($candidate.FilePath)|$($candidate.Arguments -join ' ')"
        if (-not $seen.Add($key)) {
            continue
        }

        $result = Test-PythonCandidate `
            -FilePath $candidate.FilePath `
            -Arguments $candidate.Arguments
        if ($result) {
            return $result
        }
    }

    return $null
}

function Install-WingetPackage {
    param(
        [string]$PackageId,
        [string[]]$ExtraArguments = @()
    )

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw (
            "Windows Package Manager (winget) is required to install " +
            "$PackageId automatically. Install 'App Installer' from the " +
            "Microsoft Store, then run this script again."
        )
    }

    $arguments = @(
        "install",
        "--id", $PackageId,
        "--exact",
        "--source", "winget",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity"
    ) + $ExtraArguments

    & $winget.Source @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install $PackageId."
    }

    Refresh-ProcessPath
}

function Find-FfmpegDirectories {
    $directories = [System.Collections.Generic.List[string]]::new()
    $found = @{}

    foreach ($commandName in @("ffmpeg.exe", "ffprobe.exe")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            $found[$commandName] = $command.Source
            $directory = Split-Path -Parent $command.Source
            if (-not $directories.Contains($directory)) {
                [void]$directories.Add($directory)
            }
        }
    }

    if ($found.Count -eq 2) {
        return @($directories)
    }

    if ($env:LOCALAPPDATA) {
        $packageRoot = Join-Path (
            $env:LOCALAPPDATA
        ) "Microsoft\WinGet\Packages"

        if (Test-Path -LiteralPath $packageRoot -PathType Container) {
            $packageFolders = Get-ChildItem `
                -LiteralPath $packageRoot `
                -Directory `
                -Filter "Gyan.FFmpeg*" `
                -ErrorAction SilentlyContinue

            foreach ($folder in $packageFolders) {
                $ffmpeg = Get-ChildItem `
                    -LiteralPath $folder.FullName `
                    -File `
                    -Filter "ffmpeg.exe" `
                    -Recurse `
                    -ErrorAction SilentlyContinue |
                    Select-Object -First 1

                if ($ffmpeg) {
                    $directory = $ffmpeg.DirectoryName
                    $ffprobe = Join-Path $directory "ffprobe.exe"
                    if (
                        Test-Path -LiteralPath $ffprobe -PathType Leaf
                    ) {
                        if (-not $directories.Contains($directory)) {
                            [void]$directories.Add($directory)
                        }
                        break
                    }
                }
            }
        }
    }

    foreach ($directory in $directories) {
        $env:Path = (
            $directory +
            [IO.Path]::PathSeparator +
            $env:Path
        )
    }

    $ffmpegCommand = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    $ffprobeCommand = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if ($ffmpegCommand -and $ffprobeCommand) {
        return @($directories)
    }

    return @()
}

function New-SeekShortcut {
    param(
        [string]$ShortcutPath,
        [string]$LauncherPath,
        [string]$WorkingDirectory
    )

    $shortcutDirectory = Split-Path -Parent $ShortcutPath
    [void](New-Item `
        -ItemType Directory `
        -Path $shortcutDirectory `
        -Force)

    $powershell = (Get-Command powershell.exe).Source
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = (
        "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden " +
        "-File `"$LauncherPath`""
    )
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Description = "SEEK YouTube Audio Downloader"
    $shortcut.Save()
}

Write-Host ""
Write-Host "SEEK installer for Windows" -ForegroundColor Magenta
Write-Host "--------------------------"

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    throw "InstallDir cannot be empty."
}

$InstallDir = [IO.Path]::GetFullPath($InstallDir)
Refresh-ProcessPath

foreach ($file in $requiredSourceFiles) {
    $sourcePath = Join-Path $sourceDirectory $file
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Required application file is missing: $sourcePath"
    }
}

Write-Step "Checking Python 3.10+ and Tk"
$python = Find-SuitablePython
if (-not $python) {
    if ($SkipSystemDependencies) {
        throw (
            "Python 3.10+ with Tk is unavailable. Remove " +
            "-SkipSystemDependencies to install it automatically."
        )
    }

    Write-Step "Installing Python 3.12 for the current user"
    Install-WingetPackage `
        -PackageId "Python.Python.3.12" `
        -ExtraArguments @("--scope", "user")
    $python = Find-SuitablePython
    if (-not $python) {
        throw "Python installed, but Python 3.10+ with Tk was not found."
    }
}
Write-Host "Using Python $($python.Version): $($python.FilePath)"

Write-Step "Checking FFmpeg and FFprobe"
$ffmpegDirectories = @(Find-FfmpegDirectories)
if ($ffmpegDirectories.Count -eq 0) {
    if ($SkipSystemDependencies) {
        throw (
            "FFmpeg and FFprobe are unavailable. Remove " +
            "-SkipSystemDependencies to install them automatically."
        )
    }

    Write-Step "Installing FFmpeg"
    Install-WingetPackage -PackageId "Gyan.FFmpeg"
    $ffmpegDirectories = @(Find-FfmpegDirectories)
    if ($ffmpegDirectories.Count -eq 0) {
        throw (
            "FFmpeg was installed but its executables could not be found. " +
            "Open a new PowerShell window and run this installer again."
        )
    }
}
Write-Host "FFmpeg is ready."

Write-Step "Copying SEEK to $InstallDir"
[void](New-Item -ItemType Directory -Path $InstallDir -Force)
$sourceFullPath = [IO.Path]::GetFullPath($sourceDirectory).TrimEnd(
    [IO.Path]::DirectorySeparatorChar
)
$installFullPath = $InstallDir.TrimEnd(
    [IO.Path]::DirectorySeparatorChar
)

if (
    -not $sourceFullPath.Equals(
        $installFullPath,
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    foreach ($file in $requiredSourceFiles) {
        Copy-Item `
            -LiteralPath (Join-Path $sourceDirectory $file) `
            -Destination (Join-Path $InstallDir $file) `
            -Force
    }
}

$ffmpegPathsFile = Join-Path $InstallDir "ffmpeg-paths.txt"
$ffmpegDirectories |
    Sort-Object -Unique |
    Set-Content -LiteralPath $ffmpegPathsFile -Encoding UTF8

Write-Step "Creating the private Python environment"
$venvDirectory = Join-Path $InstallDir ".app-venv"
$pythonArguments = @($python.Arguments) + @(
    "-m",
    "venv",
    $venvDirectory
)
& $python.FilePath @pythonArguments
if ($LASTEXITCODE -ne 0) {
    throw "Could not create SEEK's Python environment."
}

$venvScripts = Join-Path $venvDirectory "Scripts"
$venvPython = Join-Path $venvScripts "python.exe"
$env:Path = $venvScripts + [IO.Path]::PathSeparator + $env:Path

Write-Step "Installing SEEK's Python dependencies"
& $venvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Could not update pip."
}

& $venvPython -m pip install `
    --disable-pip-version-check `
    -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Could not install SEEK's Python dependencies."
}

Write-Step "Running the post-install health check"
$healthCheck = (
    "from downloader import check_dependencies; " +
    "r = check_dependencies(); " +
    "assert not r.missing_required, ', '.join(r.missing_required); " +
    "assert r.javascript_runtime, 'No JavaScript runtime found'; " +
    "print('JavaScript runtime:', r.javascript_runtime)"
)

Push-Location $InstallDir
try {
    & $venvPython -c $healthCheck
    if ($LASTEXITCODE -ne 0) {
        throw "SEEK's post-install health check failed."
    }
}
finally {
    Pop-Location
}

$launcherPath = Join-Path $InstallDir "launch-windows.ps1"
if (-not $NoShortcuts) {
    Write-Step "Creating Desktop and Start Menu shortcuts"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $programs = [Environment]::GetFolderPath("Programs")

    if ($desktop) {
        New-SeekShortcut `
            -ShortcutPath (Join-Path $desktop "SEEK.lnk") `
            -LauncherPath $launcherPath `
            -WorkingDirectory $InstallDir
    }
    if ($programs) {
        New-SeekShortcut `
            -ShortcutPath (Join-Path $programs "SEEK.lnk") `
            -LauncherPath $launcherPath `
            -WorkingDirectory $InstallDir
    }
}

Write-Host ""
Write-Host "SEEK is installed." -ForegroundColor Green
Write-Host "Application: $InstallDir"
Write-Host "Launcher:    $launcherPath"
Write-Host "Run this installer again at any time to update or repair SEEK."

if (-not $NoLaunch) {
    Write-Step "Launching SEEK"
    & $launcherPath
}
