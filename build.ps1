<#
.SYNOPSIS
    QwenType tasks. Everything goes through uv (no direct pip/python calls).
.EXAMPLE
    .\build.ps1 run       # uv run qwentype
    .\build.ps1 build     # uv run pyinstaller qwentype.spec  ->  dist\QwenType.exe
    .\build.ps1 install   # copy to %LOCALAPPDATA%\Programs\QwenType + Start Menu shortcut
    .\build.ps1 clean     # remove build\, dist\ and __pycache__
    .\build.ps1 check     # ruff, pyright and the unit tests (what CI runs)
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('run', 'build', 'install', 'clean', 'check')]
    [string]$Task = 'run'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$AppName = 'QwenType'
$DistExe = Join-Path $PSScriptRoot "dist\$AppName.exe"

function Invoke-Uv {
    & uv @args
    if ($LASTEXITCODE -ne 0) { throw "uv $($args -join ' ') failed with exit code $LASTEXITCODE" }
}

switch ($Task) {
    'run' {
        Invoke-Uv run qwentype
    }

    'build' {
        Invoke-Uv sync
        Invoke-Uv run pyinstaller --noconfirm --clean qwentype.spec
        if (-not (Test-Path -LiteralPath $DistExe)) { throw "Build failed: $DistExe not found" }
        $size = (Get-Item -LiteralPath $DistExe).Length
        Write-Host ("Built {0}  ({1:N1} MiB)" -f $DistExe, ($size / 1MB)) -ForegroundColor Green
    }

    'install' {
        if (-not (Test-Path -LiteralPath $DistExe)) { throw "dist\$AppName.exe not found. Run '.\build.ps1 build' first." }
        $dest = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
        $target = Join-Path $dest "$AppName.exe"

        $running = Get-Process -Name $AppName -ErrorAction SilentlyContinue
        if ($running) {
            Write-Host "Stopping the running $AppName instance..."
            $running | Stop-Process -Force
            Start-Sleep -Milliseconds 500
        }

        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Copy-Item -LiteralPath $DistExe -Destination $target -Force

        $programs = [Environment]::GetFolderPath('Programs')  # Start Menu\Programs (current user)
        $lnkPath = Join-Path $programs "$AppName.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($lnkPath)
        $lnk.TargetPath = $target
        $lnk.WorkingDirectory = $dest
        $lnk.IconLocation = "$target,0"
        $lnk.Description = 'Hold Right Ctrl, speak, release - local Qwen3-ASR voice typing'
        $lnk.Save()

        Write-Host "Installed to $target" -ForegroundColor Green
        Write-Host "Start Menu shortcut: $lnkPath"
    }

    'check' {
        Invoke-Uv run ruff check
        Invoke-Uv run ruff format --check
        Invoke-Uv run pyright
        Invoke-Uv run -m unittest discover -s tests -t .
    }

    'clean' {
        foreach ($dir in 'build', 'dist') {
            if (Test-Path -LiteralPath $dir) {
                Remove-Item -LiteralPath $dir -Recurse -Force
                Write-Host "Removed $dir\"
            }
        }
        Get-ChildItem -Path $PSScriptRoot -Recurse -Directory -Filter '__pycache__' -Force |
            Where-Object { $_.FullName -notlike '*\.venv\*' } |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force
                Write-Host "Removed $($_.FullName)"
            }
    }
}
