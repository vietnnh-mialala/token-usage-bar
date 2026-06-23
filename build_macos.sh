#!/usr/bin/env bash
# Build a standalone Token Usage Bar.app on macOS with PyInstaller.
#
# Run this ON A MAC (it cannot be built from Windows). Prerequisites:
#   - Python 3 (python.org build or Homebrew): `brew install python-tk`
#     (tkinter must be available — test with `python3 -m tkinter`)
#   - pip install -r requirements.txt
#
# Output: dist/TokenUsageBar.app  (a normal .app bundle you can drag to /Applications)
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="TokenUsageBar"

# A .icns icon is optional; if you have one named icon.icns it gets embedded.
ICON_ARG=()
if [[ -f icon.icns ]]; then
  ICON_ARG=(--icon icon.icns)
fi

echo "==> Checking tkinter is available…"
python3 -c "import tkinter; print('tkinter', tkinter.TkVersion)" || {
  echo "ERROR: tkinter not available. On Homebrew run: brew install python-tk" >&2
  exit 1
}

echo "==> Installing dependencies…"
python3 -m pip install --upgrade -r requirements.txt

echo "==> Building ${APP_NAME}.app…"
python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "${APP_NAME}" \
  --osx-bundle-identifier "com.tokenusagebar" \
  "${ICON_ARG[@]}" \
  token_bar.py

echo
echo "==> Done. Bundle at: dist/${APP_NAME}.app"
echo "    Run it:   open dist/${APP_NAME}.app"
echo
echo "Note: the app is unsigned. On first launch macOS Gatekeeper may block it —"
echo "right-click the app → Open, or remove the quarantine flag:"
echo "    xattr -dr com.apple.quarantine dist/${APP_NAME}.app"
