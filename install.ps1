# Installs Token Usage Bar for the current user (no admin required):
#   - copies TokenUsageBar.exe to %LOCALAPPDATA%\TokenUsageBar
#   - adds a Startup shortcut so it launches automatically at login
#   - starts it now
# Run by right-clicking -> "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'

# locate the exe next to this script, or in a dist\ subfolder
$src = $null
foreach ($p in @((Join-Path $PSScriptRoot 'TokenUsageBar.exe'),
                 (Join-Path $PSScriptRoot 'dist\TokenUsageBar.exe'))) {
    if (Test-Path $p) { $src = $p; break }
}
if (-not $src) { Write-Error 'TokenUsageBar.exe not found (next to this script or in dist\).'; exit 1 }

$dest = Join-Path $env:LOCALAPPDATA 'TokenUsageBar'
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# stop any running copy so the file isn't locked, then copy
Get-Process TokenUsageBar -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
Copy-Item $src $dest -Force
$exe = Join-Path $dest 'TokenUsageBar.exe'

# autostart at login via HKCU Run key (matches the in-app "Start with Windows"
# toggle; per-user, no admin). Toggle it off any time from the right-click menu.
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
New-ItemProperty -Path $run -Name 'TokenUsageBar' -Value ('"{0}"' -f $exe) `
    -PropertyType String -Force | Out-Null

Start-Process $exe
Write-Host "Installed to $dest"
Write-Host "Enabled start-at-login (HKCU Run -> TokenUsageBar)."
Write-Host "Token Usage Bar is now running (bottom-left of your taskbar)."
