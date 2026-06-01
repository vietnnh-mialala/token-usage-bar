# Rebuilds dist\TokenUsageBar.exe from source. Needs Python 3 + pip on PATH.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m pip install --quiet --upgrade pyinstaller pystray pillow
python generate_icon.py
python generate_version.py
# clean old outputs manually (PyInstaller's --clean can fail under OneDrive locks)
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --onefile --noconsole `
    --name TokenUsageBar --icon icon.ico --version-file version_info.txt `
    --noconfirm token_bar.py
Write-Host "Built: $(Join-Path $PSScriptRoot 'dist\TokenUsageBar.exe')"
