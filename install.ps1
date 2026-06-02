# Installs Token Usage Bar for the current user (no admin required):
#   - copies TokenUsageBar.exe to %LOCALAPPDATA%\TokenUsageBar
#   - adds an HKCU Run entry so it launches automatically at login
#   - starts it now
#
# EASIEST: double-click  Install.cmd  (works even when PowerShell's execution
# policy would block this .ps1). Or run:
#   powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'

function Close-WithPause([int]$code) {
    Write-Host ''
    try { Read-Host 'Press Enter to close' | Out-Null } catch {}
    exit $code
}

try {
    # Files unzipped from a downloaded archive carry a "Mark of the Web" that makes
    # Windows treat them as blocked. Clear it so this install is smooth.
    Get-ChildItem -Path $PSScriptRoot -Recurse -ErrorAction SilentlyContinue |
        Unblock-File -ErrorAction SilentlyContinue

    # Token Usage Bar shows your usage FROM Claude Code, so it needs Claude Code
    # (the CLI) on this PC. Warn early if it isn't here, rather than letting the
    # widget sit on "needs Claude Code" after install.
    $hasCli = [bool](Get-Command claude -ErrorAction SilentlyContinue) `
              -or (Test-Path (Join-Path $env:USERPROFILE '.local\bin\claude.exe'))
    $hasCreds = Test-Path (Join-Path $env:USERPROFILE '.claude\.credentials.json')
    if (-not ($hasCli -or $hasCreds)) {
        Write-Host ''
        Write-Host 'NOTE: Claude Code was not detected on this PC.' -ForegroundColor Yellow
        Write-Host 'Token Usage Bar mirrors Claude Code''s usage, so it will not show'
        Write-Host 'any data until Claude Code (the CLI) is installed and signed in:'
        Write-Host '    irm https://claude.ai/install.ps1 | iex     then:   claude  ->  /login'
        $ans = Read-Host 'Install the widget anyway? [Y/n]'
        if ($ans -and $ans.Trim() -notmatch '^(y|yes)$') {
            Write-Host 'Cancelled - nothing was installed.'
            Close-WithPause 0
        }
    }

    # locate the exe next to this script, or in a dist\ subfolder
    $src = $null
    foreach ($p in @((Join-Path $PSScriptRoot 'TokenUsageBar.exe'),
                     (Join-Path $PSScriptRoot 'dist\TokenUsageBar.exe'))) {
        if (Test-Path $p) { $src = $p; break }
    }
    if (-not $src) { throw "TokenUsageBar.exe not found next to this script (or in dist\)." }

    $dest = Join-Path $env:LOCALAPPDATA 'TokenUsageBar'
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $exe = Join-Path $dest 'TokenUsageBar.exe'

    # stop any running copy so the file isn't locked, then WAIT until it's really
    # gone (a fixed Sleep can be too short and make Copy-Item fail with a lock).
    Get-Process TokenUsageBar -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 20 -and (Get-Process TokenUsageBar -ErrorAction SilentlyContinue); $i++) {
        Start-Sleep -Milliseconds 250
    }

    Copy-Item $src $exe -Force

    # autostart at login via HKCU Run key (matches the in-app "Start with Windows"
    # toggle; per-user, no admin). Toggle it off any time from the right-click menu.
    $run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    New-ItemProperty -Path $run -Name 'TokenUsageBar' -Value ('"{0}"' -f $exe) `
        -PropertyType String -Force | Out-Null

    Start-Process $exe

    $ver = (Get-Item $exe).VersionInfo.FileVersion
    Write-Host "Installed v$ver to $dest" -ForegroundColor Green
    Write-Host 'Enabled start-at-login (HKCU Run -> TokenUsageBar).'
    Write-Host 'Token Usage Bar is now running (bottom-left of your taskbar).'
}
catch {
    Write-Host "Install FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Close-WithPause 1
}
Close-WithPause 0
