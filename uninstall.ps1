# Removes Token Usage Bar for the current user.
# EASIEST: double-click  Uninstall.cmd  (works even when PowerShell's execution
# policy would block this .ps1).
$ErrorActionPreference = 'SilentlyContinue'

Get-Process TokenUsageBar | Stop-Process -Force
for ($i = 0; $i -lt 20 -and (Get-Process TokenUsageBar -ErrorAction SilentlyContinue); $i++) {
    Start-Sleep -Milliseconds 250
}
# autostart entry (Run key) + any legacy Startup shortcut
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'TokenUsageBar'
Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'Token Usage Bar.lnk') -Force
# app + saved state
Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA 'TokenUsageBar')
Write-Host 'Token Usage Bar uninstalled (app, autostart entry, and saved position removed).'
Write-Host ''
try { Read-Host 'Press Enter to close' | Out-Null } catch {}
