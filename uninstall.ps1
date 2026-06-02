# Removes Token Usage Bar for the current user, and reports exactly what it did.
# EASIEST: double-click  Uninstall.cmd  (works even when PowerShell's execution
# policy would block this .ps1 — right-clicking the .ps1 just flashes and fails).
$ErrorActionPreference = 'Stop'

function Close-WithPause([int]$code) {
    Write-Host ''
    try { Read-Host 'Press Enter to close' | Out-Null } catch {}
    exit $code
}

$removed = @(); $missing = @(); $failed = @()

# 1) stop the running app, then WAIT until it is really gone
if (Get-Process TokenUsageBar -ErrorAction SilentlyContinue) {
    Get-Process TokenUsageBar -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 20 -and (Get-Process TokenUsageBar -ErrorAction SilentlyContinue); $i++) {
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process TokenUsageBar -ErrorAction SilentlyContinue) {
        $failed += 'could not stop the running app (close it from the tray, or reboot, then re-run)'
    } else { $removed += 'stopped the running app' }
} else { $missing += 'app was not running' }

# 2) autostart Run key
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if ((Get-Item $run).Property -contains 'TokenUsageBar') {
    try { Remove-ItemProperty $run -Name 'TokenUsageBar' -ErrorAction Stop; $removed += 'autostart (HKCU Run key)' }
    catch { $failed += "autostart Run key: $($_.Exception.Message)" }
} else { $missing += 'autostart Run key (already absent)' }

# 3) legacy Startup shortcut
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Token Usage Bar.lnk'
if (Test-Path $lnk) {
    try { Remove-Item $lnk -Force -ErrorAction Stop; $removed += 'Startup shortcut' }
    catch { $failed += "Startup shortcut: $($_.Exception.Message)" }
}

# 4) install folder (exe + saved window position)
$dest = Join-Path $env:LOCALAPPDATA 'TokenUsageBar'
if (Test-Path $dest) {
    try { Remove-Item -Recurse -Force $dest -ErrorAction Stop; $removed += "install folder ($dest)" }
    catch { $failed += "install folder still locked ($dest): $($_.Exception.Message)" }
} else { $missing += 'install folder (already absent)' }

Write-Host 'Token Usage Bar - uninstall'
Write-Host '==========================='
if ($removed) { Write-Host 'Removed:' -ForegroundColor Green; $removed | ForEach-Object { Write-Host "  - $_" } }
if ($missing) { Write-Host 'Already gone:'; $missing | ForEach-Object { Write-Host "  - $_" } }
if ($failed) {
    Write-Host 'PROBLEMS - not fully removed:' -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "  - $_" }
    Close-WithPause 1
}
Write-Host ''
Write-Host 'Done - nothing related to Token Usage Bar remains.' -ForegroundColor Green
Close-WithPause 0
