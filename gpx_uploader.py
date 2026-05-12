"""
GPX → Google My Maps  +  Time Bucketer
Two-tab utility app.
"""

import base64
import csv
import hashlib
import json
import os
import re
import secrets
import socket
import sys
import time
import threading
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from tkinter import font as tkfont, filedialog, ttk
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date

# ---------------------------------------------------------------------------
# Playwright import guard
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    import tkinter.messagebox as mb
    mb.showerror(
        "Missing dependency",
        "Playwright is not installed.\n"
        "Run:  pip install playwright  then  playwright install chromium"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
BG     = "#0f1117"
CARD   = "#1a1d27"
ACCENT = "#4f8ef7"
ACCENT2= "#2ecc8f"
TEXT   = "#e8eaf0"
MUTED  = "#abb3db"
BORDER = "#2a2d3a"
DANGER = "#e05c5c"


# ===========================================================================
# Strava OAuth + GPX helpers
# ===========================================================================

STRAVA_AUTH_URL   = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL  = "https://www.strava.com/oauth/token"
STRAVA_API_BASE   = "https://www.strava.com/api/v3"
STRAVA_REDIRECT   = "http://localhost:18642/callback"
STRAVA_SCOPE      = "activity:read_all"
STRAVA_CLIENT_ID  = "YOUR_CLIENT_ID"      # replace or load from config
STRAVA_CLIENT_SEC = "YOUR_CLIENT_SECRET"  # replace or load from config

_strava_token_store: dict = {}   # {access_token, refresh_token, expires_at}

_config_path = Path.home() / ".gpxtools_strava.json"


def _strava_load_config():
    global STRAVA_CLIENT_ID, STRAVA_CLIENT_SEC, _strava_token_store
    if _config_path.exists():
        # print(_config_path)
        try:
            data = json.loads(_config_path.read_text(encoding="utf-8"))
            STRAVA_CLIENT_ID  = data.get("client_id",  STRAVA_CLIENT_ID)
            STRAVA_CLIENT_SEC = data.get("client_secret", STRAVA_CLIENT_SEC)
            _strava_token_store = data.get("tokens", {})
        except Exception:
            pass


def _strava_save_config():
    try:
        data = {
            "client_id":     STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SEC,
            "tokens":        _strava_token_store,
        }
        _config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _strava_refresh_token_if_needed():
    """Refresh the access token if it's expired or about to expire."""
    tok = _strava_token_store
    if not tok:
        return False
    if time.time() < tok.get("expires_at", 0) - 60:
        return True  # still valid
    # Need refresh
    try:
        payload = urllib.parse.urlencode({
            "client_id":     STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SEC,
            "grant_type":    "refresh_token",
            "refresh_token": tok["refresh_token"],
        }).encode()
        req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        tok["access_token"]  = data["access_token"]
        tok["refresh_token"] = data.get("refresh_token", tok["refresh_token"])
        tok["expires_at"]    = data["expires_at"]
        _strava_save_config()
        return True
    except Exception as e:
        print("Token refresh failed:", e)


def _strava_oauth_flow(log=print):
    """
    Open browser for Strava OAuth, spin up a local server for the redirect,
    exchange the code for tokens, and store them.  Returns True on success.
    """
    code_holder = {}
    state = secrets.token_hex(8)

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args): pass
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("state", [""])[0] == state and "code" in params:
                code_holder["code"] = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h2>Connected to Strava! You can close this tab.</h2>")
            else:
                self.send_response(400)
                self.end_headers()

    server = HTTPServer(("localhost", 18642), _Handler)
    server.timeout = 120

    auth_params = urllib.parse.urlencode({
        "client_id":     STRAVA_CLIENT_ID,
        "redirect_uri":  STRAVA_REDIRECT,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope":         STRAVA_SCOPE,
        "state":         state,
    })
    url = f"{STRAVA_AUTH_URL}?{auth_params}"
    log(f"  🌐  Opening browser for Strava login…")
    webbrowser.open(url)

    server.handle_request()   # blocks until one request comes in
    server.server_close()

    if "code" not in code_holder:
        log("  ❌  Strava OAuth: no code received.")
        return False

    # Exchange code for tokens
    try:
        payload = urllib.parse.urlencode({
            "client_id":     STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SEC,
            "code":          code_holder["code"],
            "grant_type":    "authorization_code",
        }).encode()
        req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        _strava_token_store["access_token"]  = data["access_token"]
        _strava_token_store["refresh_token"] = data["refresh_token"]
        _strava_token_store["expires_at"]    = data["expires_at"]
        _strava_token_store["athlete_name"]  = data.get("athlete", {}).get("firstname", "")
        _strava_save_config()
        log(f"  ✅  Strava connected: {_strava_token_store.get('athlete_name', 'unknown')}")
        return True
    except Exception as e:
        log(f"  ❌  Strava token exchange failed: {e}")
        return False


def _strava_api_get(endpoint, params=None):
    if not _strava_refresh_token_if_needed():
        raise RuntimeError("Strava not connected or token refresh failed.")
    token = _strava_token_store["access_token"]
    url = f"{STRAVA_API_BASE}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _strava_fetch_activities_for_date(date_str: str, log=print):
    """
    Pull all activities from Strava whose name contains date_str (yyyymmdd format).
    Returns list of activity dicts.
    """
    log(f"  📡  Fetching Strava activities matching '{date_str}'…")
    matching = []
    page = 1
    while True:
        batch = _strava_api_get("/athlete/activities", {"per_page": 100, "page": page})
        if not batch:
            break
        for act in batch:
            if date_str in act.get("name", ""):
                matching.append(act)
        # Strava returns max 100; if we got fewer, we've hit the end
        if len(batch) < 100:
            break
        page += 1
    log(f"  ✅  Found {len(matching)} matching activit{'y' if len(matching)==1 else 'ies'}")
    return matching


def _strava_activity_to_gpx(activity: dict, output_dir: Path, log=print) -> Path | None:
    """
    Fetch the activity stream (latlng, altitude, time) and convert to a GPX file.
    Returns the Path to the written .gpx file.
    """
    act_id   = activity["id"]
    act_name = _safe_filename(activity.get("name", f"activity_{act_id}"))
    gpx_path = output_dir / f"{act_name}.gpx"

    try:
        streams = _strava_api_get(
            f"/activities/{act_id}/streams",
            {"keys": "latlng,altitude,time", "key_by_type": "true"}
        )
    except Exception as e:
        log(f"  ⚠️  Could not fetch streams for activity {act_id}: {e}")
        return None

    latlng   = streams.get("latlng",   {}).get("data", [])
    altitude = streams.get("altitude", {}).get("data", [])
    times    = streams.get("time",     {}).get("data", [])

    if not latlng:
        log(f"  ⚠️  No GPS data for activity '{activity.get('name')}'")
        return None

    # Build GPX XML
    start_dt = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="GPXTools/Strava" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <metadata><name>{act_name}</name></metadata>',
        '  <trk>',
        f'    <name>{act_name}</name>',
        '    <trkseg>',
    ]
    for i, (lat, lon) in enumerate(latlng):
        ele   = altitude[i] if i < len(altitude) else 0
        secs  = times[i]    if i < len(times)    else 0
        pt_dt = start_dt.timestamp() + secs
        pt_iso = datetime.utcfromtimestamp(pt_dt).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Ensure dot decimal separator regardless of locale
        lat_s = format(lat, ".7f")
        lon_s = format(lon, ".7f")
        ele_s = format(ele, ".1f")
        lines.append(
            f'      <trkpt lat="{lat_s}" lon="{lon_s}">'
            f'<ele>{ele_s}</ele><time>{pt_iso}</time></trkpt>'
        )
    lines += ['    </trkseg>', '  </trk>', '</gpx>']

    gpx_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"  💾  GPX written: {gpx_path.name}")
    return gpx_path


def strava_pull_gpx_files(date_str: str, output_dir: Path, log=print):
    """
    Full pipeline: fetch matching activities, convert to GPX, return list of Paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    activities = _strava_fetch_activities_for_date(date_str, log)
    gpx_paths = []
    for act in activities:
        p = _strava_activity_to_gpx(act, output_dir, log)
        if p:
            gpx_paths.append(p)
    return gpx_paths


# ===========================================================================
# TAB 1 — Google My Maps automation
# ===========================================================================

MYMAPS_HOME = "https://www.google.com/maps/d/"


def _find_chromium_exe():
    """
    Find the Chromium executable whether running from source or PyInstaller bundle.
    Returns the path string, or None to let Playwright use its default.
    """
    import os, sys, glob
    if getattr(sys, 'frozen', False):
        # Bundled exe: Chromium was copied next to the exe by build.bat
        base = os.path.dirname(sys.executable)
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = base
    else:
        # Running from source: standard ms-playwright user cache
        base = os.path.join(os.path.expanduser('~'), 'AppData', 'Local',
                            'ms-playwright')
    patterns = [
        os.path.join(base, 'chromium*', 'chrome-win64', 'chrome.exe'),
        os.path.join(base, 'chromium*', 'chrome-win', 'chrome.exe'),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def _frame_label(frame):
    return frame.url or "<main frame>"


def _app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _make_run_output_dir():
    base_out = _app_base_dir() / "out"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base_out / stamp
    suffix = 1
    while run_dir.exists():
        run_dir = base_out / f"{stamp}_{suffix}"
        suffix += 1
    return run_dir


def _safe_filename(value):
    value = re.sub(r'[<>:"/\\|?*]+', "_", value).strip()
    return value or "untitled"


def _parse_gpx_endpoints(gpx_path: Path):
    """
    Extract the first and last (lat, lon) trackpoints from a GPX file.
    Returns ((start_lat, start_lon), (end_lat, end_lon)) or (None, None) on failure.
    """
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(gpx_path))
        root = tree.getroot()
        # Handle namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        points = root.findall(f".//{ns}trkpt")
        if not points:
            return None, None
        def _pt(el):
            return (float(el.attrib["lat"]), float(el.attrib["lon"]))
        return _pt(points[0]), _pt(points[-1])
    except Exception:
        return None, None


def _google_maps_link(lat, lon):
    return f"https://www.google.com/maps?q={lat},{lon}"


def _plus_code(lat, lon, town=""):
    try:
        from openlocationcode import openlocationcode as olc
        full = olc.encode(lat, lon)
        # Short code: last 4+2 chars (e.g. "FFGQ+M7V") + town name
        short = full[-8:] if len(full) >= 8 else full
        return f"{short} {town}".strip() if town else short
    except Exception:
        return ""


def _reverse_geocode(lat, lon, log=print):
    """
    Reverse geocode a lat/lon pair using Nominatim (OpenStreetMap).
    Returns (formatted_address, town) tuple. Both may be empty strings on failure.
    """
    try:
        url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lon}&format=json&zoom=18"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "XDATools/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        addr = data.get("address", {})
        road        = addr.get("road", "")
        house_no    = addr.get("house_number", "")
        town        = (addr.get("city")
                    or addr.get("town")
                    or addr.get("village")
                    or addr.get("municipality")
                    or "")
        county      = (addr.get("county")
                    or addr.get("state")
                    or addr.get("region")
                    or "")
        country     = addr.get("country", "")

        if house_no:
            street_full = f"{road} {house_no}".strip()
        else:
            street_full = f"{road} ({_plus_code(lat, lon, town)})".strip()

        formatted   = ", ".join(filter(None, [street_full, town, county, country]))
        return formatted, town
    except Exception as e:
        log(f"  ⚠️  Reverse geocode failed ({lat},{lon}): {e}")
        return "", ""


def _get_route_geo_info(gpx_path: Path, log=print):
    """
    For a GPX file, return start/end position strings and city column value.
    Each position string is: google maps link + newline + address.
    City is "StartTown - EndTown" or just "Town" if both are the same.
    """
    start_pt, end_pt = _parse_gpx_endpoints(gpx_path)

    start_cell, end_cell, city = "", "", ""

    if start_pt:
        start_addr, start_town = _reverse_geocode(start_pt[0], start_pt[1], log)
        start_link = _google_maps_link(start_pt[0], start_pt[1])
        start_cell = f"{start_link} {start_addr}"
        time.sleep(1)  # Nominatim rate limit
    else:
        start_town = ""

    if end_pt:
        end_addr, end_town = _reverse_geocode(end_pt[0], end_pt[1], log)
        end_link = _google_maps_link(end_pt[0], end_pt[1])
        end_cell = f"{end_link} {end_addr}"
    else:
        end_town = ""

    if start_town and end_town:
        city = start_town if start_town == end_town else f"{start_town} - {end_town}"
    elif start_town:
        city = start_town
    elif end_town:
        city = end_town

    return start_cell, end_cell, city


def _init_share_output_file(output_dir: Path, export_format: str) -> Path:
    export_format = export_format.lower()
    share_output = output_dir / f"mymaps_share_links_{datetime.now():%Y%m%d_%H%M%S}.{export_format}"
    headers = ["Map Name", "Share URL", "Start Position", "End Position", "City"]
    if export_format == "csv":
        with share_output.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow(headers)
    else:
        share_output.write_text("\t".join(headers) + "\n", encoding="utf-8")
    return share_output

def _open_links_from_file(file_path: Path, log=print):
    try:
        if file_path.suffix.lower() == ".csv":
            with file_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                next(reader, None)  # skip header
                links = []
                for row in reader:
                    if len(row) >= 2:
                        url = row[1].strip()
                        if url.startswith("http"):
                            links.append(url)
        else:
            lines = file_path.read_text(encoding="utf-8").splitlines()
            links = []
            for line in lines[1:]:
                parts = line.split("\t")
                if len(parts) >= 2:
                    url = parts[1].strip()
                    if url.startswith("http"):
                        links.append(url)
    except Exception as e:
        log(f"⚠️  Could not read links file: {e}")
        return

    if not links:
        log("⚠️  No links found to open.")
        return

    log(f"🌐  Opening {len(links)} links in browser...")

    # First → new window
    webbrowser.open_new(links[0])

    # Rest → tabs
    for url in links[1:]:
        time.sleep(0.2)  # prevent browser overload
        webbrowser.open_new_tab(url)


def _append_share_output_row(output_file: Path, export_format: str, map_name: str, share_url: str,
                             start_pos: str = "", end_pos: str = "", city: str = ""):
    export_format = export_format.lower()
    if export_format == "csv":
        with output_file.open("a", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow([map_name, share_url, start_pos, end_pos, city])
    else:
        with output_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{map_name}\t{share_url}\t{start_pos}\t{end_pos}\t{city}\n")


def _find_first_visible_locator(page, selectors, timeout_ms=8_000):
    deadline = time.time() + (timeout_ms / 1000)
    last_error = None

    while time.time() < deadline:
        for frame in page.frames:
            for selector in selectors:
                try:
                    locator = frame.locator(selector).first
                    if locator.count() == 0:
                        continue
                    if locator.is_visible():
                        return frame, locator, selector
                except Exception as e:
                    last_error = e
        time.sleep(0.25)

    raise RuntimeError(f"No visible locator found for selectors {selectors}. Last error: {last_error}")


def _extract_visible_link(page):
    for frame in page.frames:
        try:
            input_loc = frame.locator("input[value^='http']").first
            if input_loc.count() > 0 and input_loc.is_visible():
                url = input_loc.input_value().strip()
                if url.startswith("http"):
                    return url
        except Exception:
            pass

        try:
            anchor_loc = frame.locator("a[href^='http']").first
            if anchor_loc.count() > 0 and anchor_loc.is_visible():
                url = (anchor_loc.get_attribute("href") or "").strip()
                if url.startswith("http"):
                    return url
        except Exception:
            pass
    return None


def _capture_map_screenshot(page, output_dir, map_name, log):
    screenshot_path = output_dir / f"{_safe_filename(map_name)}.png"
    
    # Attempt to hide the left sidebar and header UI to clear the view for the route
    try:
        page.evaluate("""() => {
            const selectors = [
                '.i4ewOd-m699re-j9v0ce', // Left sidebar
                '.fO9pBf-m699re-j9v0ce', // Search box / Header
                '.widget-scene-canvas-container' // Sometimes obscures canvas
            ];
            selectors.forEach(s => {
                const el = document.querySelector(s);
                if (el) el.style.display = 'none';
            });
        }""")
    except Exception:
        pass

    selectors = (
        "#scene",
        "#map",
        ".widget-scene",
        ".widget-scene-canvas",
        "canvas",
    )

    best_candidate = None
    best_area = 0
    for frame in page.frames:
        for selector in selectors:
            try:
                locator = frame.locator(selector)
                for i in range(min(locator.count(), 5)):
                    candidate = locator.nth(i)
                    if not candidate.is_visible():
                        continue
                    box = candidate.bounding_box()
                    if not box:
                        continue
                    area = box["width"] * box["height"]
                    if area >= 120_000 and area > best_area:
                        best_candidate = (candidate, selector, _frame_label(frame))
                        best_area = area
            except Exception:
                continue

    original_viewport = page.viewport_size
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.wait_for_timeout(600)

    if best_candidate:
        candidate, selector, frame_name = best_candidate
        candidate.screenshot(path=str(screenshot_path))
        log(f"  📸  Saved map screenshot: {screenshot_path.name} via {selector} in {frame_name}")
    else:
        page.screenshot(path=str(screenshot_path))
        log(f"  📸  Saved fallback page screenshot: {screenshot_path.name}")

    if original_viewport:
        page.set_viewport_size(original_viewport)

    return screenshot_path


def _save_share_link(page, output_file, map_name, log, export_format="csv", timeout_ms=20_000,
                     start_pos="", end_pos="", city=""):
    share_selectors = (
        "button:has-text('Share')",
        "button:has-text('Megosztás')",
        "[aria-label*='Share']",
        "[aria-label*='Megosztás']",
        "text=/^Share$|^Megosztás$/i",
    )
    _, share_btn, share_selector = _find_first_visible_locator(page, share_selectors, timeout_ms=timeout_ms)
    share_btn.click(force=True)
    log(f"  🔗  Opened share dialog via {share_selector}")
    dialog = page.locator("[role='dialog']").filter(
        has=page.locator("text=/Térkép megosztása|Map sharing|Share map/i")
    ).first
    dialog.wait_for(state="visible", timeout=timeout_ms)

    linksharing = dialog.locator("#linksharing").first
    linksharing.wait_for(state="attached", timeout=8_000)

    is_checked = linksharing.evaluate("el => el.checked")
    if not is_checked:
        linksharing.evaluate(
            """el => {
                if (!el.checked) {
                    el.click();
                }
                return el.checked;
            }"""
        )
        time.sleep(1)
        if not linksharing.evaluate("el => el.checked"):
            raise RuntimeError("Link sharing toggle did not switch on in time.")
        log("  🔓  Enabled 'anyone with the link can view'")
    else:
        log("  🔓  Link sharing was already enabled")

    share_input = dialog.locator(".tFr3cc-LS81yb input[readonly]").first
    share_input.wait_for(state="visible", timeout=8_000)
    time.sleep(1)
    share_url = ""
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            share_url = share_input.input_value().strip()
            if share_url.startswith("http"):
                break
        except Exception:
            pass

        time.sleep(0.25)

    if not share_url.startswith("http"):
        raise RuntimeError(f"Share dialog opened, but the link field was empty: {share_url!r}")

    _append_share_output_row(output_file, export_format, map_name, share_url,
                             start_pos=start_pos, end_pos=end_pos, city=city)
    log(f"  🔗  Saved share link to {output_file.name}")

    try:
        close_btn = dialog.locator("button[name='close']").first
        if close_btn.count() > 0:
            close_btn.click(force=True)
        else:
            page.keyboard.press("Escape")
    except Exception:
        pass

    return share_url


def _attach_file_from_import_dialog(page, file_path, log, timeout_ms=20_000):
    """
    My Maps sometimes renders the import dialog inside an iframe, and the
    visible "Tallozas / Browse" button is not always the most reliable target.
    Prefer setting the hidden file input directly, then fall back to clicking
    a visible chooser button and handling the file chooser event.
    """
    deadline = time.time() + (timeout_ms / 1000)
    last_error = None

    while time.time() < deadline:
        for frame in page.frames:
            try:
                file_input = frame.locator("input[type='file']").first
                if file_input.count() > 0:
                    file_input.set_input_files(str(file_path))
                    # log(f"  ✅  Attached file via file input in frame: {_frame_label(frame)}")
                    log(f"  ✅  Attached file via file input")
                    return
            except Exception as e:
                last_error = e
        time.sleep(0.25)

    log("  ℹ️  No usable file input found directly; trying chooser button fallback…")
    button_selectors = (
        ".UywwFc-vQzf8d",
        "button:has-text('Tallózás')",
        "button:has-text('Browse')",
        "button:has-text('Choose file')",
        "text=/Tall[oó]zás|Browse|Choose file|Select a file/i",
    )
    for frame in page.frames:
        for selector in button_selectors:
            try:
                button = frame.locator(selector).first
                if button.count() == 0:
                    continue
                button.wait_for(state="visible", timeout=500)
                with page.expect_file_chooser(timeout=timeout_ms) as fc_info:
                    button.click(force=True)
                fc_info.value.set_files(str(file_path))
                log(f"  ✅  Attached file via chooser button in frame: {_frame_label(frame)}")
                return
            except Exception as e:
                last_error = e

    frame_urls = ", ".join(_frame_label(frame) for frame in page.frames)
    raise RuntimeError(
        "Import dialog opened, but no usable file input or chooser button was found. "
        f"Frames seen: {frame_urls}. Last error: {last_error}"
    )


def _wait_for_imported_layer(page, gpx_path, log, timeout_ms=20_000):
    """
    Wait for the imported layer title to appear.  Google My Maps occasionally
    shows a transient "reversing last step" error toast — we detect it and retry
    the whole import once before giving up.
    """
    expected_full = gpx_path.name.lower()
    expected_stem = gpx_path.stem.lower()

    deadline = time.time() + timeout_ms / 1000

    while time.time() < deadline:
        for frame in page.frames:
            try:
                layers = frame.locator(".pbTTYe-r4nke")
                count = min(layers.count(), 10)

                for i in range(count):
                    el = layers.nth(i)
                    if not el.is_visible():
                        continue

                    text = el.inner_text().strip().lower()

                    if text in ("untitled", "névtelen", "Untitled", "Névtelen"):
                        continue

                    if (
                        text == expected_full or
                        text == expected_stem or
                        text.startswith(expected_stem[:20])
                    ):
                        log(f"  ✅  Imported layer matched: {text}")
                        
                        # stability check (important!)
                        time.sleep(1.5)
                        
                        # re-check it's still there
                        if el.is_visible():
                            return

            except Exception:
                pass

        time.sleep(0.4)

    raise RuntimeError("Import did not produce a valid layer name")


def upload_gpx_files(gpx_paths: list, output_dir: Path, log, done_callback,
                    share_export_format: str = "csv", open_links_after: bool = False):
    def _run():
        try:
            log("🧵  Thread running, importing Playwright…")
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
            log("✅  Playwright imported OK")
        except Exception as e:
            log(f"❌  Playwright import failed: {e}")
            done_callback(False)
            return
        chromium_exe = _find_chromium_exe()
        log(f"🔍  Chromium path: {chromium_exe or 'NOT FOUND - using Playwright default'}")
        launch_kwargs = dict(
            headless=False,
            slow_mo=160,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
            ignore_default_args=["--enable-automation"],
        )
        if chromium_exe:
            launch_kwargs['executable_path'] = chromium_exe
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            share_output = _init_share_output_file(output_dir, share_export_format)
            log(f"💾  Output folder: {output_dir}")
            log(f"🔗  Share links file: {share_output.name}")
            _tmp_root = tk.Tk()
            _tmp_root.withdraw()
            _screen_w = _tmp_root.winfo_screenwidth()
            _screen_h = _tmp_root.winfo_screenheight()
            _tmp_root.destroy()
            with sync_playwright() as p:
                log("🔧  Playwright context started, launching Chromium...")
                # Persistent context to keep you logged in to Google
                user_data_dir = Path.cwd() / ".google_profile"
                context = p.chromium.launch_persistent_context(
                    str(user_data_dir),
                    viewport={'width': _screen_w, 'height': _screen_h},
                    no_viewport=False,
                    accept_downloads=True,
                    **launch_kwargs
                )
                try:
                    context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://www.google.com")
                except Exception:
                    pass
                # launch_persistent_context creates a page automatically
                page = context.pages[0] if context.pages else context.new_page()

                # ── Step 1: Navigate to My Maps home and wait for sign-in ──
                page.goto(MYMAPS_HOME, wait_until="domcontentloaded")
                log("⏳  Waiting for My Maps home…")
                try:
                    page.wait_for_selector(".QT3Do-t0O6ic-LgbsSe-fmcmS", timeout=120_000)
                except PWTimeout:
                    log("❌  Timed out waiting for My Maps. Did you sign in?")
                    context.close()
                    done_callback(False)
                    return

                for idx, gpx_path in enumerate(reversed(gpx_paths), 1):
                    map_name = gpx_path.stem  # filename without .gpx — used for both layer and map
                    log(f"\n📍  [{idx}/{len(gpx_paths)}]  Processing: {map_name}")

                    # ── Step 2: If not first, go back to home ──────────────
                    if idx > 1:
                        time.sleep(2)   # let the previous map finish any pending saves
                        page.goto(MYMAPS_HOME, wait_until="domcontentloaded")
                        try:
                            page.wait_for_selector(".QT3Do-t0O6ic-LgbsSe-fmcmS", timeout=25_000)
                        except PWTimeout:
                            log(f"  ⚠️  My Maps home didn't reload, skipping {map_name}.")
                            continue

                    # ── Step 3: Click Create ──────────────────────────────
                    log("  🖱️  Clicking Create new map…")
                    try:
                        page.click(".QT3Do-t0O6ic-LgbsSe-fmcmS")
                    except Exception as e:
                        log(f"  ❌  Could not click Create: {e}")
                        continue

                    # ── Step 4: Confirm popup ──────────────────────────────
                    # The CREATE button in the popup has data-id="t0O6ic" — unique.
                    time.sleep(1.5)
                    try:
                        popup_btn = page.locator("[data-id='t0O6ic']").first
                        if popup_btn.count() > 0 and popup_btn.is_visible():
                            popup_btn.click(force=True)
                            log("  ✅  Confirmed popup")
                    except Exception:
                        pass

                    # ── Step 5: Wait for editor to load ───
                    log("  ⏳  Waiting for editor…")
                    try:
                        page.wait_for_selector("#ly0-layerview-import-link", timeout=25_000, state="visible")
                        log(f"  ✅  Editor loaded: {page.url}")
                    except Exception as e:
                        log(f"Editor did not load in current tab: {e}")
                        # Fallback: Google sometimes opens a new tab even if it usually doesn't
                        if len(context.pages) > 1:
                            page = context.pages[-1]
                            log(f"  🔄  Detected new tab after all, switching: {page.url}")
                            page.wait_for_load_state("domcontentloaded")
                        else:
                            log(f"Page stuck at: {page.url}")
                            continue

                    # ── Step 6: Click Import ───────────────────────────────
                    log("  📂  Clicking Import…")
                    try:
                        import_btn = page.wait_for_selector("#ly0-layerview-import-link", timeout=20_000, state="visible")
                        import_btn.click(force=True)
                    except Exception as e:
                        log(f"  ❌  Import button not found: {e}")
                        continue

                    # ── Step 7: Attach the file from the import dialog ─────
                    log(f"  📎  Selecting file: {gpx_path.name}")
                    import_ok = False
                    for attempt in range(1, 4):  # up to 3 attempts
                        if attempt > 1:
                            log(f"  🔄  Import retry {attempt}/3 for '{gpx_path.name}'…")
                            # Hard reload is the safest way to clear Google's "Hiba történt" state
                            # while staying on the same map ID.
                            page.reload(wait_until="domcontentloaded")
                            time.sleep(2) 
                            try:
                                import_btn = page.wait_for_selector("#ly0-layerview-import-link", timeout=20_000, state="visible")
                                import_btn.click(force=True)
                            except Exception as re_e:
                                log(f"  ⚠️  Retry setup failed: {re_e}")
                                continue

                        try:
                            _attach_file_from_import_dialog(page, gpx_path, log, timeout_ms=15_000)
                            # Tiny moment for Google to register the file
                            time.sleep(0.5)
                        except Exception as e:
                            log(f"  ❌  File selection failed: {e}")
                            continue

                        log("  ⏳  Waiting for imported layer to appear…")
                        try:
                            _wait_for_imported_layer(page, gpx_path, log, timeout_ms=4_000)
                            import_ok = True
                            break
                        except RuntimeError as e:
                            log(f"  ❌  Import did not finish cleanly: {e}")
                            continue

                    if not import_ok:
                        log(f"  ❌  All import attempts failed for '{gpx_path.name}', skipping.")
                        continue

                    # ── Step 8: Rename layer ───────────────────────────────
                    log(f"  ✏️   Renaming layer to '{map_name}'…")
                    try:
                        # Strategy: Match by stem, then filename, then first visible
                        layer_el = None
                        for candidate_text in (gpx_path.stem, gpx_path.name):
                            loc = page.locator(".pbTTYe-r4nke", has_text=candidate_text).first
                            if loc.count() > 0:
                                layer_el = loc
                                log(f"  🎯  Matched layer by '{candidate_text}'")
                                break
                        
                        if not layer_el:
                            log("  ℹ️  No exact text match; using first visible layer")
                            layer_el = page.locator(".pbTTYe-r4nke").first

                        layer_el.wait_for(state="visible", timeout=3_000)
                        layer_el.click(force=True)
                        page.wait_for_selector("#update-layer-name", timeout=8_000, state="visible")
                        inp = page.locator("#update-layer-name .Sx9Kwc-pbTTYe-r4nke-fmcmS").first
                        inp.click(force=True)
                        inp.fill(map_name)
                        page.click("#update-layer-name button[name='save']", force=True)
                        page.wait_for_selector("#update-layer-name", timeout=8_000, state="hidden")
                        log("  ✅  Layer renamed")
                    except Exception as e:
                        log(f"  ⚠️  Layer rename failed: {e}")

                    # ── Step 9: Rename map ─────────────────────────────────
                    log(f"  🗺️   Renaming map to '{map_name}'…")
                    try:
                        map_title_el = page.wait_for_selector(".i4ewOd-r4nke", timeout=10_000, state="visible")
                        map_title_el.click(force=True)
                        page.wait_for_selector("#update-map", timeout=8_000, state="visible")
                        inp = page.wait_for_selector("#update-map .Sx9Kwc-i4ewOd-r4nke-fmcmS", timeout=5_000, state="visible")
                        inp.click(force=True)
                        inp.fill(map_name)
                        save_btn = page.wait_for_selector("#update-map button[name='save']", timeout=5_000, state="visible")
                        save_btn.click(force=True)
                        page.wait_for_selector("#update-map", timeout=8_000, state="hidden")
                        log(f"  ✅  Map renamed to '{map_name}'")
                    except Exception as e:
                        log(f"  ⚠️  Map rename failed: {e}")

                    # ── Step 10: Save map screenshot ───────────────────────
                    log("  📸  Capturing route screenshot…")
                    try:
                        _capture_map_screenshot(page, output_dir, map_name, log)
                    except Exception as e:
                        log(f"  ⚠️  Screenshot capture failed: {e}")

                    # ── Step 10b: Reverse geocode start/end points ─────────
                    log("  🌍  Looking up start/end addresses…")
                    try:
                        start_pos, end_pos, city = _get_route_geo_info(gpx_path, log)
                        log(f"  📍  City: {city or '(unknown)'}")
                    except Exception as e:
                        log(f"  ⚠️  Geo lookup failed: {e}")
                        start_pos, end_pos, city = "", "", ""

                    # ── Step 11: Save share link ───────────────────────────
                    log("  🔗  Saving share link…")
                    try:
                        _save_share_link(page, share_output, map_name, log, share_export_format,
                                         start_pos=start_pos, end_pos=end_pos, city=city)
                    except Exception as e:
                        log(f"  ⚠️  Share link export failed: {e}")

                    log(f"  ✅  Done: {map_name}")

                log(f"\n🎉  All files processed! Check Google My Maps.")
                log(f"💾  Images and links saved in: {output_dir}")

                if open_links_after:
                    try:
                        _open_links_from_file(share_output, log)
                    except Exception as e:
                        log(f"⚠️  Failed to open links: {e}")

                context.close()
                done_callback(True)
        except Exception as e:
            log(f"❌  Fatal error: {e}")
            done_callback(False)

    threading.Thread(target=_run, daemon=True).start()


# ===========================================================================
# TAB 2 — Time Bucketer logic
# ===========================================================================

def _parse_seconds(ts):
    """Convert hh:mm:ss or hh-mm-ss to total seconds since midnight."""
    ts = ts.strip().replace("-", ":")
    h, m, s = (int(x) for x in ts.split(":"))
    return h * 3600 + m * 60 + s


def _parse_datetime_secs(date_part, time_part):
    """
    Convert mm/dd + hh:mm:ss to a sortable integer (seconds).
    Uses month*32*86400 + day*86400 + time_secs so cross-day and
    cross-month ordering is always correct without any wraparound guessing.
    """
    mo, dy = (int(x) for x in date_part.split("/"))
    return (mo * 32 + dy) * 86400 + _parse_seconds(time_part)


def _parse_entry_line(line):
    """
    Parse  mm/dd-hh:mm:ss: <value>
    Returns (sort_key_int, raw_timestamp_str, value_str) or None.
    """
    line = line.strip()
    if not line:
        return None
    m = re.match(r'^(\d{2}/\d{2})-(\d{2}:\d{2}:\d{2}):\s*(.+)$', line)
    if not m:
        return None
    date_part = m.group(1)
    time_part = m.group(2)
    value     = m.group(3).strip()
    raw_ts    = f"{date_part}-{time_part}"
    sort_key  = _parse_datetime_secs(date_part, time_part)
    return sort_key, raw_ts, value


def run_bucketer(bucket_ts_str, entry_files):
    """
    bucket_ts_str : list of 'hh-mm-ss' strings (chronological order)
    entry_files   : list of Path objects
    Returns formatted output string.

    Bucket boundaries are time-of-day only (hh-mm-ss).
    Entries have full date+time. Assignment uses time-of-day comparison only,
    so entries from different files are always bucketed correctly regardless
    of load order. Midnight wraparound on bucket boundaries is supported.
    """

    # Parse bucket boundaries, adjusting for midnight wrap
    bucket_secs_raw = [_parse_seconds(b) for b in bucket_ts_str]
    adj_bucket_secs = []
    day_off = 0
    for i, b in enumerate(bucket_secs_raw):
        if i > 0 and (b + day_off) < adj_bucket_secs[-1]:
            day_off += 86400
        adj_bucket_secs.append(b + day_off)

    # Read and merge all entry files
    raw_entries = []
    for fp in entry_files:
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            parsed = _parse_entry_line(line)
            if parsed:
                raw_entries.append(parsed)

    # Deduplicate (same raw_ts + value)
    seen = set()
    entries = []
    for sort_key, raw_ts, value in raw_entries:
        key = (raw_ts, value)
        if key not in seen:
            seen.add(key)
            entries.append((sort_key, raw_ts, value))

    # Sort by full date+time
    entries.sort(key=lambda x: x[0])

    # Assign each entry to a bucket by time-of-day comparison.
    # raw_ts format: mm/dd-hh:mm:ss
    first_bucket_tod = bucket_secs_raw[0]
    buckets = [[] for _ in bucket_secs_raw]

    for sort_key, raw_ts, value in entries:
        time_part = raw_ts[6:]  # skip "mm/dd-" to get hh:mm:ss
        entry_tod = _parse_seconds(time_part)
        # If buckets span midnight and this entry's time-of-day is before
        # the first bucket start, it belongs to the "next day" portion
        if day_off > 0 and entry_tod < first_bucket_tod:
            entry_tod += 86400
        # Find the last bucket whose boundary <= entry time-of-day
        assigned = None
        for i, bsec in enumerate(adj_bucket_secs):
            if entry_tod >= bsec:
                assigned = i
        if assigned is not None:
            buckets[assigned].append((sort_key, raw_ts, value))

    def _base_value(v):
        """Strip trailing digit(s)+Time suffix: 'Foo3Time' -> 'Foo'"""
        return re.sub(r'\d+Time$', '', v).strip()

    # Format output
    lines_out = []
    for i, (orig_ts_str, bucket_entries) in enumerate(zip(bucket_ts_str, buckets)):
        if i > 0:
            lines_out.append("----------------")
        lines_out.append(f"[{orig_ts_str}]")
        for _secs, raw_ts, value in bucket_entries:
            lines_out.append(f"{raw_ts}: {value}")
        lines_out.append("")
        counts = defaultdict(int)
        for _, _, value in bucket_entries:
            counts[_base_value(value)] += 1
        for value, count in sorted(counts.items()):
            lines_out.append(f"{value} → {count}")

    return "\n".join(lines_out)


# ===========================================================================
# GUI shared helpers
# ===========================================================================

def _styled_button(parent, text, command, color=ACCENT, fg="white"):
    f = tkfont.Font(family="Segoe UI", size=9, weight="bold")
    return tk.Button(
        parent, text=text, command=command,
        font=f, bg=color, fg=fg,
        activebackground=BORDER, activeforeground=TEXT,
        bd=0, padx=12, pady=6, cursor="hand2", relief="flat"
    )


def _scrolled_text(parent, height=8, fg=TEXT, state="normal"):
    f = tkfont.Font(family="Consolas", size=9)
    frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    sb = tk.Scrollbar(frame, bg=CARD, troughcolor=CARD, relief="flat", bd=0)
    sb.pack(side="right", fill="y")
    txt = tk.Text(frame, bg=CARD, fg=fg, font=f, bd=0, highlightthickness=0,
                  height=height, wrap="word", yscrollcommand=sb.set, state=state,
                  insertbackground=TEXT)
    txt.pack(fill="both", expand=True, padx=4, pady=4)
    sb.config(command=txt.yview)
    return frame, txt


def _make_listbox(parent):
    f_mono = tkfont.Font(family="Consolas", size=9)
    frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    sb = tk.Scrollbar(frame, bg=CARD, troughcolor=CARD, relief="flat", bd=0)
    sb.pack(side="right", fill="y")
    lb = tk.Listbox(frame, bg=CARD, fg=TEXT, font=f_mono,
                    selectbackground=ACCENT, activestyle="none",
                    bd=0, highlightthickness=0,
                    yscrollcommand=sb.set, selectforeground="white")
    lb.pack(fill="both", expand=True, padx=4, pady=4)
    sb.config(command=lb.yview)
    return frame, lb


def _small_label(parent, text):
    f = tkfont.Font(family="Segoe UI", size=8)
    return tk.Label(parent, text=text, font=f, bg=BG, fg=MUTED)


# ===========================================================================
# Tab 1: GPX Uploader
# ===========================================================================

class GpxTab(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=BG)
        self._gpx_files = []
        self._running = False
        self._build()

    def _build(self):
        f_sub = tkfont.Font(family="Segoe UI", size=9)

        # ── Date picker row (feeds Strava filter) ─────────────────────────
        date_row = tk.Frame(self, bg=BG)
        date_row.pack(fill="x", padx=20, pady=(14, 0))
        _small_label(date_row, "STRAVA DATE FILTER").pack(side="left")

        today = date.today()
        self._date_y = tk.StringVar(value=str(today.year))
        self._date_m = tk.StringVar(value=f"{today.month:02d}")
        self._date_d = tk.StringVar(value=f"{today.day:02d}")

        date_inner = tk.Frame(self, bg=BG)
        date_inner.pack(fill="x", padx=20, pady=(2, 8))

        # Setup custom style for Combobox to match dark theme
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=TEXT, arrowcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)], background=[("readonly", CARD)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")

        def _combo_entry(parent, var, values, w):
            cb = ttk.Combobox(parent, textvariable=var, values=values, width=w, state="readonly")
            cb.pack(side="left", padx=2)
            return cb

        tk.Label(date_inner, text="Year:", bg=BG, fg=MUTED,
                 font=tkfont.Font(family="Segoe UI", size=9)).pack(side="left")
        years = [str(y) for y in range(today.year, today.year - 15, -1)]
        _combo_entry(date_inner, self._date_y, years, 6)

        tk.Label(date_inner, text="Month:", bg=BG, fg=MUTED,
                 font=tkfont.Font(family="Segoe UI", size=9)).pack(side="left", padx=(6, 0))
        months = [f"{m:02d}" for m in range(1, 13)]
        _combo_entry(date_inner, self._date_m, months, 4)

        tk.Label(date_inner, text="Day:", bg=BG, fg=MUTED,
                 font=tkfont.Font(family="Segoe UI", size=9)).pack(side="left", padx=(6, 0))
        days = [f"{d:02d}" for d in range(1, 32)]
        _combo_entry(date_inner, self._date_d, days, 4)

        tk.Label(date_inner, text="→ yyyymmdd filter for Strava activity names", bg=BG, fg=MUTED,
                 font=tkfont.Font(family="Segoe UI", size=8)).pack(side="left", padx=(10, 0))

        # Drop zone
        self.drop_frame = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                                    highlightthickness=2, cursor="hand2")
        self.drop_frame.pack(fill="x", padx=20, pady=(16, 8))
        self.drop_label = tk.Label(
            self.drop_frame,
            text="⊕  Drop .gpx files here  /  click to browse",
            font=f_sub, bg=CARD, fg=MUTED, pady=22, padx=16, justify="center"
        )
        self.drop_label.pack(fill="x")
        self.drop_frame.bind("<Button-1>", self._browse)
        self.drop_label.bind("<Button-1>", self._browse)

        _small_label(self, "FILES").pack(anchor="w", padx=20)
        list_frame, self.file_list_widget = _make_listbox(self)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(2, 8))

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=20, pady=(0, 8))
        _styled_button(btn_row, "Clear", self._clear_files, color=CARD, fg=MUTED).pack(side="left")
        self.go_btn = _styled_button(btn_row, "▶  Upload to My Maps", self._start_upload)
        self.go_btn.pack(side="right")

        _small_label(self, "LOG").pack(anchor="w", padx=20)
        log_frame, self.log_text = _scrolled_text(self, height=7, fg=ACCENT2, state="disabled")
        log_frame.pack(fill="x", padx=20, pady=(2, 8))

        self.status_var = tk.StringVar(value="Drop GPX files to upload, or connect Strava in Settings and use the date filter.")
        tk.Label(self, textvariable=self.status_var,
                 font=tkfont.Font(family="Segoe UI", size=8),
                 bg=BG, fg=MUTED, anchor="w").pack(fill="x", padx=20, pady=(0, 8))

        self._setup_dnd()

    def _setup_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES
            for w in (self.drop_frame, self.drop_label):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            self.drop_label.config(text="⊕  Click here to browse for .gpx files")

    def _on_drop(self, event):
        paths = re.findall(r'\{([^}]+)\}|(\S+)', event.data)
        parsed = [p[0] or p[1] for p in paths]
        self._add_files([Path(p) for p in parsed if p.lower().endswith(".gpx")])

    def _browse(self, _=None):
        paths = filedialog.askopenfilenames(
            title="Select GPX files",
            filetypes=[("GPX files", "*.gpx"), ("All files", "*.*")]
        )
        self._add_files([Path(p) for p in paths])

    def _add_files(self, paths):
        existing = set(self._gpx_files)
        new = [p for p in paths if p not in existing and p.suffix.lower() == ".gpx"]
        self._gpx_files.extend(new)
        self._refresh_list()
        if new:
            self.status_var.set(f"{len(self._gpx_files)} file(s) queued.")

    def _clear_files(self):
        self._gpx_files.clear()
        self._refresh_list()
        self.status_var.set("File list cleared.")

    def _refresh_list(self):
        self.file_list_widget.delete(0, "end")
        for p in self._gpx_files:
            self.file_list_widget.insert("end", f"  {p.name}")
        if self._gpx_files:
            self.drop_label.config(
                text=f"⊕  {len(self._gpx_files)} file(s) loaded — drop more or click to add",
                fg=ACCENT2)
        else:
            self.drop_label.config(
                text="⊕  Drop .gpx files here  /  click to browse", fg=MUTED)

    def _start_upload(self):
        if self._running:
            return
        export_format = getattr(self.winfo_toplevel(), "share_export_format", tk.StringVar(value="csv")).get().lower()
        output_dir = _make_run_output_dir()

        # Build yyyymmdd filter string from the date picker
        try:
            date_str = (
                f"{int(self._date_y.get()):04d}"
                f"{int(self._date_m.get()):02d}"
                f"{int(self._date_d.get()):02d}"
            )
        except ValueError:
            self.status_var.set("⚠  Invalid date in Strava date filter.")
            return

        if self._gpx_files:
            # Manual files selected — use those directly
            self._run_upload(list(self._gpx_files), output_dir, export_format)
        else:
            # No manual files — pull from Strava
            if not _strava_token_store.get("access_token"):
                self.status_var.set("⚠  No GPX files and Strava not connected. Connect Strava in Settings.")
                return
            self._running = True
            self.go_btn.config(state="disabled", text="⏳  Pulling Strava…", bg=MUTED)
            self.status_var.set(f"Pulling Strava activities matching '{date_str}'…")
            self._log("─" * 48)
            self._log(f"📡  Pulling Strava activities for date filter: {date_str}")

            def _strava_worker():
                try:
                    gpx_paths = strava_pull_gpx_files(date_str, output_dir, self._log)
                    if not gpx_paths:
                        self._log(f"⚠  No Strava activities found matching '{date_str}'.")
                        self.after(0, lambda: self.go_btn.config(
                            state="normal", text="▶  Upload to My Maps", bg=ACCENT))
                        self.after(0, lambda: self.status_var.set("No matching Strava activities found."))
                        self._running = False
                        return
                    self._log(f"✅  {len(gpx_paths)} GPX file(s) ready from Strava.")
                    self.after(0, lambda: self._run_upload(gpx_paths, output_dir, export_format))
                except Exception as e:
                    self._log(f"❌  Strava pull failed: {e}")
                    self.after(0, lambda: self.go_btn.config(
                        state="normal", text="▶  Upload to My Maps", bg=ACCENT))
                    self.after(0, lambda: self.status_var.set("Strava pull failed — check log."))
                    self._running = False

            threading.Thread(target=_strava_worker, daemon=True).start()

    def _run_upload(self, gpx_paths, output_dir, export_format):
        self._running = True
        self.go_btn.config(state="disabled", text="⏳  Running…", bg=MUTED)
        self.status_var.set("Browser opening — sign into Google if prompted…")
        self._log("─" * 48)
        self._log(f"💾  Output folder: {output_dir}")
        self._log(f"🧾  Share export format: {export_format.upper()}")
        self._log("🧵  Starting upload thread…")
        open_links = getattr(self.winfo_toplevel(), "open_links_after", tk.BooleanVar(value=False)).get()
        upload_gpx_files(
            gpx_paths,
            output_dir,
            self._log,
            self._on_done,
            share_export_format=export_format,
            open_links_after=open_links,
        )
        self._log("🧵  Thread started.")

    def _on_done(self, success):
        self._running = False
        self.after(0, lambda: self.go_btn.config(
            state="normal", text="▶  Upload to My Maps", bg=ACCENT))
        msg = "✅  Upload complete!" if success else "⚠  Finished with errors — check log."
        self.after(0, lambda: self.status_var.set(msg))

    def _log(self, msg):
        def _a():
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, _a)


# ===========================================================================
# Tab 2: Time Bucketer
# ===========================================================================

class BucketTab(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=BG)
        self._entry_files = []
        self._build()

    def _build(self):
        # Left panel: inputs
        left = tk.Frame(self, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(20, 8), pady=16)

        # Base timestamps
        ts_hdr = tk.Frame(left, bg=BG)
        ts_hdr.pack(fill="x")
        _small_label(ts_hdr, "BASE TIMESTAMPS  (hh-mm-ss, one per line)").pack(side="left")
        _styled_button(ts_hdr, "Load from file", self._load_ts_file,
                       color=CARD, fg=MUTED).pack(side="right")

        ts_frame, self.ts_text = _scrolled_text(left, height=10, fg=ACCENT2)
        ts_frame.pack(fill="x", pady=(2, 12))

        # Entry files
        ef_hdr = tk.Frame(left, bg=BG)
        ef_hdr.pack(fill="x")
        _small_label(ef_hdr, "ENTRY FILES  (.txt)").pack(side="left")
        _styled_button(ef_hdr, "Add files…", self._add_entry_files).pack(side="right")

        ef_frame, self.entry_list = _make_listbox(left)
        ef_frame.pack(fill="both", expand=True, pady=(2, 8))

        clr_row = tk.Frame(left, bg=BG)
        clr_row.pack(fill="x")
        _styled_button(clr_row, "Clear files", self._clear_entry_files,
                       color=CARD, fg=MUTED).pack(side="left")
        self.run_btn = _styled_button(clr_row, "▶  Run bucketer", self._run)
        self.run_btn.pack(side="right")

        # Right panel: output
        right = tk.Frame(self, bg=BG)
        right.pack(side="right", fill="both", expand=True, padx=(8, 20), pady=16)

        out_hdr = tk.Frame(right, bg=BG)
        out_hdr.pack(fill="x")
        _small_label(out_hdr, "OUTPUT").pack(side="left")
        _styled_button(out_hdr, "Save .txt…", self._save_output,
                       color=CARD, fg=MUTED).pack(side="right", padx=(4, 0))
        _styled_button(out_hdr, "Copy all", self._copy_output,
                       color=CARD, fg=MUTED).pack(side="right")

        out_frame, self.out_text = _scrolled_text(right, height=30, fg=TEXT, state="disabled")
        out_frame.pack(fill="both", expand=True, pady=(2, 0))

        self.status_var = tk.StringVar(value="Add timestamps and entry files, then click Run.")
        tk.Label(right, textvariable=self.status_var,
                 font=tkfont.Font(family="Segoe UI", size=8),
                 bg=BG, fg=MUTED, anchor="w").pack(fill="x", pady=(4, 0))

    def _load_ts_file(self):
        path = filedialog.askopenfilename(
            title="Select timestamps file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            self.status_var.set(f"⚠  Could not read: {e}")
            return
        self.ts_text.delete("1.0", "end")
        self.ts_text.insert("end", content.strip())
        self.status_var.set(f"Timestamps loaded from {Path(path).name}")

    def _add_entry_files(self):
        paths = filedialog.askopenfilenames(
            title="Select entry .txt files",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        existing = set(self._entry_files)
        new = [Path(p) for p in paths if Path(p) not in existing]
        self._entry_files.extend(new)
        self._refresh_entry_list()
        if new:
            self.status_var.set(f"{len(self._entry_files)} entry file(s) loaded.")

    def _clear_entry_files(self):
        self._entry_files.clear()
        self._refresh_entry_list()
        self.status_var.set("Entry file list cleared.")

    def _refresh_entry_list(self):
        self.entry_list.delete(0, "end")
        for p in self._entry_files:
            self.entry_list.insert("end", f"  {p.name}")

    def _run(self):
        raw_ts = self.ts_text.get("1.0", "end").strip()
        if not raw_ts:
            self.status_var.set("⚠  No base timestamps entered.")
            return
        bucket_lines = [l.strip() for l in raw_ts.splitlines() if l.strip()]
        bad = [l for l in bucket_lines
               if not re.match(r'^\d{2}[-:]\d{2}[-:]\d{2}$', l)]
        if bad:
            self.status_var.set(f"⚠  Bad timestamp format: {bad[0]}  (expected hh-mm-ss)")
            return
        if not self._entry_files:
            self.status_var.set("⚠  No entry files loaded.")
            return

        self.run_btn.config(state="disabled", text="⏳  Running…", bg=MUTED)
        self.status_var.set("Processing…")

        def _worker():
            try:
                result = run_bucketer(bucket_lines, self._entry_files)
                self.after(0, lambda: self._show_output(result))
            except Exception as e:
                self.after(0, lambda: self.status_var.set(f"❌  Error: {e}"))
            finally:
                self.after(0, lambda: self.run_btn.config(
                    state="normal", text="▶  Run bucketer", bg=ACCENT))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_output(self, text):
        self.out_text.config(state="normal")
        self.out_text.delete("1.0", "end")
        self.out_text.insert("end", text)
        self.out_text.config(state="disabled")
        self.status_var.set(f"Done — {text.count(chr(10)) + 1} lines of output.")

    def _copy_output(self):
        content = self.out_text.get("1.0", "end").strip()
        if content:
            self.clipboard_clear()
            self.clipboard_append(content)
            self.status_var.set("Output copied to clipboard.")

    def _save_output(self):
        content = self.out_text.get("1.0", "end").strip()
        if not content:
            self.status_var.set("⚠  Nothing to save yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Save output",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            Path(path).write_text(content, encoding="utf-8")
            self.status_var.set(f"Saved to {Path(path).name}")


class SettingsTab(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=BG)
        
        # Create a canvas and scrollbar for the settings page
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview, bg=CARD, troughcolor=BG)
        self.scrollable_frame = tk.Frame(self.canvas, bg=BG)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # Support mouse wheel scrolling
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        _strava_load_config()
        self._build()
        self._init_strava_creds_from_ui()
        self._refresh_strava_status()

    def _on_canvas_configure(self, event):
        # Update the width of the scrollable frame to match the canvas
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if self.winfo_ismapped():
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def _refresh_strava_status(self):
        if _strava_token_store.get("access_token"):
            name = _strava_token_store.get("athlete_name", "Connected")
            self._strava_status_var.set(f"Connected ({name})")
        else:
            self._strava_status_var.set("Not connected")

    def _save_strava_creds(self):
        global STRAVA_CLIENT_ID, STRAVA_CLIENT_SEC
        STRAVA_CLIENT_ID = self._strava_cid_var.get().strip()
        STRAVA_CLIENT_SEC = self._strava_csec_var.get().strip()
        _strava_save_config()
        self._refresh_strava_status()

    def _connect_strava(self):
        self._init_strava_creds_from_ui()
        if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SEC:
            return
        def worker():
            ok = _strava_oauth_flow()
            if ok:
                self._save_strava_creds()
            self.after(0, self._refresh_strava_status)
        threading.Thread(target=worker, daemon=True).start()

    def _disconnect_strava(self):
        _strava_token_store.clear()
        _strava_save_config()
        self._refresh_strava_status()

    def _init_strava_creds_from_ui(self):
        global STRAVA_CLIENT_ID, STRAVA_CLIENT_SEC
        if not hasattr(self, "_strava_cid_var"): return
        cid = self._strava_cid_var.get().strip()
        sec = self._strava_csec_var.get().strip()
        if cid and sec:
            STRAVA_CLIENT_ID = cid
            STRAVA_CLIENT_SEC = sec

    def _build(self):
        # Build inside scrollable_frame instead of self
        parent = self.scrollable_frame
        export_var = self.winfo_toplevel().share_export_format
        f_title = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        f_sub   = tkfont.Font(family="Segoe UI", size=9)

        # ── Strava card ──────────────────────────────────────────────────────
        strava_card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        strava_card.pack(fill="x", padx=20, pady=(16, 0))

        tk.Label(strava_card, text="Strava Integration", font=f_title, bg=CARD, fg=TEXT).pack(
            anchor="w", padx=16, pady=(16, 4)
        )
        tk.Label(
            strava_card,
            text="Connect your Strava account to pull activities by date instead of uploading GPX files manually.\n"
                 "You must register an API Application at https://www.strava.com/settings/api and paste the\n"
                 "Client ID and Secret below, then click Connect.",
            bg=CARD, fg=MUTED, justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

        tk.Label(
            strava_card,
            text="⚠ Callback Domain must be set to: localhost",
            bg=CARD,
            fg="#ff4d4f",  # red
            anchor="w",
            justify="left",
            font=("Segoe UI", 9, "bold"),
        ).pack(fill="x", padx=16, pady=(0, 10))

        cred_frame = tk.Frame(strava_card, bg=CARD)
        cred_frame.pack(fill="x", padx=16, pady=(0, 8))

        for label, attr in (("Client ID:", "_strava_cid_var"), ("Client Secret:", "_strava_csec_var")):
            row = tk.Frame(cred_frame, bg=CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=14, anchor="w", bg=CARD, fg=TEXT, font=f_sub).pack(side="left")
            var = tk.StringVar()
            setattr(self, attr, var)
            show = "" if "Secret" not in label else "*"
            entry = tk.Entry(row, textvariable=var, show=show, width=38,
                             bg=BG, fg=TEXT, insertbackground=TEXT,
                             bd=0, highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=ACCENT,
                             font=tkfont.Font(family="Consolas", size=9))
            entry.pack(side="left", padx=4)

        self._strava_status_var = tk.StringVar(value="Not connected")
        self._refresh_strava_status()

        status_row = tk.Frame(strava_card, bg=CARD)
        status_row.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(status_row, text="Status:", bg=CARD, fg=MUTED, font=f_sub).pack(side="left")
        tk.Label(status_row, textvariable=self._strava_status_var, bg=CARD, fg=ACCENT2, font=f_sub).pack(side="left", padx=6)

        btn_row = tk.Frame(strava_card, bg=CARD)
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        _styled_button(btn_row, "💾  Save credentials", self._save_strava_creds, color=CARD, fg=MUTED).pack(side="left", padx=(0, 6))
        _styled_button(btn_row, "🔗  Connect Strava", self._connect_strava).pack(side="left")
        _styled_button(btn_row, "✖  Disconnect", self._disconnect_strava, color=CARD, fg=DANGER).pack(side="left", padx=(6, 0))

        # Load saved creds into entry fields
        if STRAVA_CLIENT_ID != "YOUR_CLIENT_ID":
            self._strava_cid_var.set(STRAVA_CLIENT_ID)
        if STRAVA_CLIENT_SEC != "YOUR_CLIENT_SECRET":
            self._strava_csec_var.set(STRAVA_CLIENT_SEC)

        # ── Share export card ────────────────────────────────────────────────
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(card, text="Share Link Export", font=f_title, bg=CARD, fg=TEXT).pack(
            anchor="w", padx=16, pady=(16, 6)
        )
        tk.Label(
            card,
            text="Choose whether each upload run saves share links as a TXT file or a CSV file.",
            bg=CARD,
            fg=MUTED,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16)

        for value, label, note in (
            ("csv", "CSV", "Recommended. Opens directly in Excel and keeps share links in columns."),
            ("txt", "TXT", "Tab-separated text file if you prefer the old plain-text format."),
        ):
            option = tk.Frame(card, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            option.pack(fill="x", padx=16, pady=(14 if value == "csv" else 0, 10))
            tk.Radiobutton(
                option,
                text=label,
                value=value,
                variable=export_var,
                bg=CARD,
                fg=TEXT,
                selectcolor=CARD,
                activebackground=CARD,
                activeforeground=TEXT,
                highlightthickness=0,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(10, 2))
            tk.Label(
                option,
                text=note,
                bg=CARD,
                fg=MUTED,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=34, pady=(0, 10))

        tk.Label(
            card,
            text="This setting affects future My Maps upload runs and the share-links file created in each timestamped output folder.",
            bg=CARD,
            fg=MUTED,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 16))

        open_links_var = self.winfo_toplevel().open_links_after

        chk = tk.Checkbutton(
            card,
            text="Open all map links after upload",
            variable=open_links_var,
            bg=CARD,
            fg=TEXT,
            selectcolor=CARD,
            activebackground=CARD,
            activeforeground=TEXT,
        )
        chk.pack(anchor="w", padx=16, pady=(0, 16))


# ===========================================================================
# Main App window with tabs
# ===========================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("XDA Tools")
        self.geometry("860x660")
        self.minsize(700, 520)
        self.configure(bg=BG)
        self._build()

    def _build(self):
        if not hasattr(self, "open_links_after"):
            self.open_links_after = tk.BooleanVar(value=True)
        f_head = tkfont.Font(family="Segoe UI", size=13, weight="bold")
        f_tab  = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        if not hasattr(self, "share_export_format"):
            self.share_export_format = tk.StringVar(value="csv")

        hdr = tk.Frame(self, bg=BG, pady=12)
        hdr.pack(fill="x", padx=24)
        tk.Label(hdr, text="XDA Tools", font=f_head, bg=BG, fg=TEXT).pack(side="left")

        tab_bar = tk.Frame(self, bg=BG)
        tab_bar.pack(fill="x", padx=20)

        self._tab_frames = {}
        self._tab_btns   = {}

        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True)

        tabs = [
            ("gpx",    "🗺  My Maps Uploader", GpxTab),
            ("bucket", "⏱  Time Bucketer",    BucketTab),
            ("settings", "⚙  Settings",      SettingsTab),
        ]

        for key, label, cls in tabs:
            frame = cls(content)
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._tab_frames[key] = frame

            btn = tk.Button(
                tab_bar, text=label, font=f_tab,
                bg=CARD, fg=MUTED,
                activebackground=ACCENT, activeforeground="white",
                bd=0, padx=18, pady=8, relief="flat", cursor="hand2",
                command=lambda k=key: self._switch_tab(k)
            )
            btn.pack(side="left", padx=(0, 2))
            self._tab_btns[key] = btn

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self._switch_tab("gpx")

    def _switch_tab(self, key):
        for k, frame in self._tab_frames.items():
            frame.lower() if k != key else frame.lift()
        for k, btn in self._tab_btns.items():
            btn.config(bg=ACCENT if k == key else CARD,
                       fg="white" if k == key else MUTED)


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    try:
        from tkinterdnd2 import TkinterDnD

        class AppDnD(App, TkinterDnD.Tk):
            def __init__(self):
                TkinterDnD.Tk.__init__(self)
                self.title("XDA Tools")
                self.geometry("860x660")
                self.minsize(700, 520)
                self.configure(bg=BG)
                self._build()

        app = AppDnD()
    except Exception:
        app = App()

    app.mainloop()

if __name__ == "__main__":
    main()
