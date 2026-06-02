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

# Package the distributable zip + checksum, the SAME layout the GitHub Action
# (release.yml) ships. Keep the file list in sync between the two. The .cmd
# launchers are REQUIRED — they are the only install path that survives the
# default RemoteSigned policy.
$rel = Join-Path $PSScriptRoot 'release\TokenUsageBar'
New-Item -ItemType Directory -Force -Path $rel | Out-Null
Get-ChildItem $rel -File -ErrorAction SilentlyContinue | Remove-Item -Force
$payload = 'dist\TokenUsageBar.exe','Install.cmd','Uninstall.cmd',
           'install.ps1','uninstall.ps1','README.md','LICENSE'
Copy-Item $payload $rel -Force
$zip = Join-Path $PSScriptRoot 'release\TokenUsageBar.zip'
Compress-Archive -Path (Join-Path $rel '*') -DestinationPath $zip -Force
$h = (Get-FileHash dist\TokenUsageBar.exe -Algorithm SHA256).Hash
$h | Out-File -Encoding ascii (Join-Path $PSScriptRoot 'release\TokenUsageBar.exe.sha256') -NoNewline

# Guard: never ship a zip missing a required file.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$names = [System.IO.Compression.ZipFile]::OpenRead($zip).Entries.FullName
foreach ($req in 'Install.cmd','Uninstall.cmd','install.ps1','uninstall.ps1','TokenUsageBar.exe','README.md','LICENSE') {
    if ($names -notcontains $req) { throw "Packaging error: '$req' missing from TokenUsageBar.zip" }
}
Write-Host "Packaged: $zip"
Write-Host "  contents: $($names -join ', ')"
Write-Host "  sha256  : $h"
