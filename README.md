# XDA_Tools

A small GUI utility that uploads one or more GPX files to Google My Maps by
automating a Chromium browser with Playwright. Each GPX becomes its own map,
named after the file. There is also a timebucketing feature to group tags to their corresponding test.

Table of contents
- About
- Features
- Requirements
- One-time build (Windows)
- Usage
- Strava integration
- Troubleshooting
- Files

---

About

This app uses a simple Tkinter GUI for drag-and-drop and Playwright for
browser automation. Because Google My Maps has no public API, the tool drives
Chromium to create maps and import GPX files in the web UI.

Features

- Drag & drop GPX files (or browse) and upload them in batch
- Creates one My Maps map per GPX file and renames maps automatically
- Built-in logging panel to monitor progress
- Optional Strava helpers / time-bucketing utilities included in the code

Requirements

- Windows (packaged as an .exe in dist/)
- Python 3.11+ (only needed to build; not required to run the packaged exe)
- Python packages: playwright, tkinterdnd2, pyinstaller (see requirements.txt)

One-time build (Windows)

1. Install Python 3.11+ and enable "Add Python to PATH".
2. Run `build.bat` (double-click or from PowerShell).
   - Installs Python deps and Playwright browsers (Chromium).
   - Packages the app with PyInstaller into `dist\XDA_Tools\`.
3. The packaged application is `dist\XDA_Tools\XDA_Tools.exe`.
   Note: A prebuilt release may be available in the repository Releases — you
   do not have to build locally if you prefer the release bundle.

Usage (Maps upload, Strava, and Time Buckets)

Modes

- Local-file mode: choose one or more .gpx files (drag & drop or Browse) and
  upload them; one My Maps map is created per file.
- Date-top mode: set the date control at the top of the Maps Upload tab and
  click Upload without selecting files. The app will create maps/activities
  for that date (useful when timestamps or logs are provided separately).

Quick steps — Local-file mode

1. Launch `XDA_Tools.exe` (or run `gpx_uploader.py`).
2. Add GPX files (drag & drop or Browse).
3. Click "Upload to My Maps" and sign into Google in the opened Chromium window
   if prompted.
4. Watch the log panel for progress; the browser stays open when done.

Quick steps — Date-top mode (no local files)

1. Launch the app and open the Maps Upload tab.
2. Set the date control at the top to the desired date.
3. Click "Upload to My Maps" — the app will act using the configured date.

Time bucketing (timestamps)

- Manual entry: type timestamps directly into the time-bucketing textbox. The
  field accepts one timestamp per line or comma-separated values.
- Import from file: load a timestamp file (plain text or CSV with one value per
  line) to populate the time buckets.

Notes on formats: accept common ISO-like timestamps (e.g. `2026-04-30T08:15:00`) 
or simple time-only entries (e.g. `08:15`) when a date is provided on the
Maps Upload tab. If unsure, import timestamps from a file to avoid parsing
ambiguities.

Logs and imports

- Logs can only be imported from a file via the Logs Import control — they
  cannot be pasted or typed into the logs import UI.
- Timestamp files and logs are separate: timestamp files populate time buckets;
  imported logs are used for diagnostic or recovery purposes.

Strava integration

- The app includes Strava OAuth/token helpers and token refresh support.
- To enable Strava features, provide your Strava client ID and secret or save
  them to `~/.gpxtools_strava.json` (the app will read and persist tokens there).
- See `gpx_uploader.py` for the OAuth flow, token refresh behavior, and the
  functions that integrate Strava data with time-bucketing.

If you want, add an example timestamp file or a sample log file to the repo to
clarify expected formats.Troubleshooting & notes

- This tool performs UI automation. If Google My Maps changes, imports may
  fail. Search for `_rename_map` and `_import_gpx` in `gpx_uploader.py` to
  update selectors.
- The script waits up to ~60 seconds per file for the import; large files may
  take longer.
- The output folder is next to the exe and is named `out` when packaging
  with the provided build script.
- Your Google credentials are used only in the local browser session —
  nothing is sent to external servers by this script.

Files

- `gpx_uploader.py` — Main application (GUI, Playwright automation, Strava helpers)
- `requirements.txt` — Python dependencies (playwright, tkinterdnd2, pyinstaller)
- `build.bat` — One-click build script to install deps and create the EXE
- `XDA_Tools.spec` — PyInstaller spec used to package the application