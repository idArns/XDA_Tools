#!/bin/bash
# GPX to My Maps -- macOS build script (equivalent of build.bat)
# Run from the project folder:  chmod +x build.sh && ./build.sh

set -e

echo "============================================================"
echo " GPX to My Maps -- Build Script (macOS)"
echo "============================================================"
echo

APP_NAME="XDA_Tools"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 not found."
    echo "        Install from https://www.python.org/downloads/macos/"
    echo "        (the python.org installer includes Tk, which the GUI needs)."
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "[ERROR] This Python has no tkinter."
    echo "        Use the python.org installer, or with Homebrew: brew install python-tk"
    exit 1
fi

echo "[1/5] Creating virtualenv and installing Python dependencies..."
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt pyinstaller --quiet

echo "[2/5] Installing Playwright's Chromium browser..."
python -m playwright install chromium

echo "[3/5] Locating Playwright browser and package paths..."
PW_PKG=$(python -c "import playwright, os; print(os.path.dirname(playwright.__file__))")
DND_DIR=$(python -c "import tkinterdnd2, os; print(os.path.dirname(tkinterdnd2.__file__))")
# Walk up from the browser executable until we hit the "chromium-NNNN" folder
CHROMIUM_SRC=$(python -c "
from playwright.sync_api import sync_playwright
import os
p = sync_playwright().start()
exe = p.chromium.executable_path
p.stop()
d = os.path.dirname(exe)
while not os.path.basename(d).startswith('chromium-'):
    d = os.path.dirname(d)
print(d)
")
CHROMIUM_FOLDER=$(basename "$CHROMIUM_SRC")

echo "    Playwright package : $PW_PKG"
echo "    tkinterdnd2        : $DND_DIR"
echo "    Chromium source    : $CHROMIUM_SRC"
echo "    Chromium folder    : $CHROMIUM_FOLDER"

# App icon: macOS needs .icns. Use xda_icon.icns if present, otherwise build it from xda_icon.ico
ICON_ARG=()
if [ ! -f xda_icon.icns ] && [ -f xda_icon.ico ]; then
    echo "    Generating xda_icon.icns from xda_icon.ico..."
    rm -rf xda_icon.iconset && mkdir xda_icon.iconset
    if sips -s format png xda_icon.ico --out xda_icon.iconset/base.png >/dev/null 2>&1; then
        for s in 16 32 64 128 256 512; do
            sips -z $s $s xda_icon.iconset/base.png --out "xda_icon.iconset/icon_${s}x${s}.png" >/dev/null
            sips -z $((s*2)) $((s*2)) xda_icon.iconset/base.png --out "xda_icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
        done
        rm xda_icon.iconset/base.png
        iconutil -c icns xda_icon.iconset -o xda_icon.icns || echo "    [WARN] iconutil failed, building without icon"
    else
        echo "    [WARN] Could not convert .ico, building without icon"
    fi
    rm -rf xda_icon.iconset
fi
[ -f xda_icon.icns ] && ICON_ARG=(--icon "xda_icon.icns")

echo "[4/5] Bundling with PyInstaller..."
echo "      (This may take 2-5 minutes)"

pyinstaller \
  --onedir \
  --windowed \
  --name "$APP_NAME" \
  --noconfirm \
  "${ICON_ARG[@]}" \
  --add-data "$PW_PKG:playwright" \
  --add-data "$DND_DIR:tkinterdnd2" \
  --add-data "xda_icon.ico:." \
  --hidden-import tkinterdnd2 \
  --hidden-import playwright \
  --hidden-import playwright.sync_api \
  gpx_uploader.py

echo "[5/5] Copying Chromium browser into the app bundle..."
# Placed next to the executable, mirroring dist\XDA_Tools\<chromium-folder> on Windows
DEST="dist/$APP_NAME.app/Contents/MacOS/$CHROMIUM_FOLDER"
rm -rf "$DEST"
cp -R "$CHROMIUM_SRC" "$DEST"
echo "    Chromium copied to $DEST"

# Copying files in invalidates the bundle's signature; re-sign ad-hoc so it launches (required on Apple Silicon)
codesign --force --deep --sign - "dist/$APP_NAME.app" 2>/dev/null \
    && echo "    App re-signed (ad-hoc)." \
    || echo "    [WARN] codesign failed; app may need: xattr -cr dist/$APP_NAME.app"

echo
echo "Done!"
echo
echo "Your application is:"
echo "  dist/$APP_NAME.app"
echo
echo "You can move the .app anywhere. If you copy it to another Mac and macOS"
echo "says it is damaged/unverified, run:  xattr -cr /path/to/$APP_NAME.app"
echo
