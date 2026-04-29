XDA_Tools.EXE IS LOCATED IN "dist/XDA_Tools". IT IS ALREADY BUILT, YOU DO NOT HAVE TO BUILD IT AGAIN!!!

# GPX → Google My Maps  —  Batch Uploader

Drag GPX files onto the window and upload them all to Google My Maps in one go.
Each file becomes its own map, named after the filename.

---

## One-time build  (needs Python installed once, then never again)

1. Install Python 3.11+ from https://www.python.org/downloads/
   ✔  Tick **"Add Python to PATH"** during install.

2. Double-click **build.bat**
   - Installs dependencies automatically
   - Downloads Playwright's Chromium (~150 MB, once)
   - Packages everything into `dist\XDA_Tools\`

3. The result is `dist\XDA_Tools\XDA_Tools.exe`
   You can zip that whole folder and share/move it freely.
   No Python needed on the target machine.

---

## Usage

1. Launch `XDA_Tools.exe`
2. Drag `.gpx` files onto the drop zone, or click to browse
3. Click **▶ Upload to My Maps**
4. A Chromium browser opens — **sign into Google** if prompted (first run)
5. The script creates one new My Maps map per file, named after the filename
6. Watch the log panel for progress; browser stays open when done

---

## Notes

- Google My Maps has no official API, so this uses browser automation.
  The UI is driven by Playwright controlling a real Chromium window.
- You stay in control — your Google credentials never leave your machine.
- If My Maps changes its UI, the selectors in `gpx_uploader.py` may need
  updating (look for the `_rename_map` and `_import_gpx` functions).
- Large GPX files may take longer to import; the script waits up to 60 s
  per file before moving on.

---

## Files

| File | Purpose |
|------|---------|
| `gpx_uploader.py` | Main application (GUI + automation) |
| `requirements.txt` | Python dependencies |
| `build.bat` | One-click build script |
