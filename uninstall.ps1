# Removes Token Usage Bar for the current user.
$ErrorActionPreference = 'SilentlyContinue'
Get-Process TokenUsageBar | Stop-Process -Force
Start-Sleep -Milliseconds 500
# autostart entry (Run key) + any legacy Startup shortcut
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'TokenUsageBar'
Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'Token Usage Bar.lnk') -Force
# app + saved state
Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA 'TokenUsageBar')
Write-Host 'Token Usage Bar uninstalled (app, autostart entry, and saved position removed).'
