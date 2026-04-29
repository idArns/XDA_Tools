@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  GPX to My Maps -- Build Script
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo         Download from https://www.python.org/downloads/
    echo         Make sure "Add Python to PATH" is ticked during install.
    pause
    exit /b 1
)

echo [1/5] Installing Python dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo [2/5] Installing Playwright's Chromium browser...
python -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Playwright browser install failed.
    pause
    exit /b 1
)

echo [3/5] Locating Playwright browser and package paths...
for /f "delims=" %%i in ('python -c "import playwright, os; print(os.path.dirname(playwright.__file__))"') do set PW_PKG=%%i
for /f "delims=" %%i in ('python -c "import tkinterdnd2, os; print(os.path.dirname(tkinterdnd2.__file__))"') do set DND_DIR=%%i
for /f "delims=" %%i in ('python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); exe=p.chromium.executable_path; p.stop(); import os; print(os.path.dirname(os.path.dirname(exe)))"') do set CHROMIUM_SRC=%%i
for /f "delims=" %%i in ('python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); exe=p.chromium.executable_path; p.stop(); import os; print(os.path.basename(os.path.dirname(os.path.dirname(exe))))"') do set CHROMIUM_FOLDER=%%i

echo     Playwright package : %PW_PKG%
echo     tkinterdnd2        : %DND_DIR%
echo     Chromium source    : %CHROMIUM_SRC%
echo     Chromium folder    : %CHROMIUM_FOLDER%

echo [4/5] Bundling with PyInstaller...
echo       (This may take 2-5 minutes)

pyinstaller ^
  --onedir ^
  --windowed ^
  --name "XDA_Tools" ^
  --noconfirm ^
  --add-data "%PW_PKG%;playwright" ^
  --add-data "%DND_DIR%;tkinterdnd2" ^
  --hidden-import tkinterdnd2 ^
  --hidden-import playwright ^
  --hidden-import playwright.sync_api ^
  gpx_uploader.py

if errorlevel 1 (
    echo [ERROR] PyInstaller failed.
    pause
    exit /b 1
)

echo [5/5] Copying Chromium browser into dist folder...
if not defined CHROMIUM_SRC (
    echo [ERROR] Could not find Chromium. Run: python -m playwright install chromium
    pause
    exit /b 1
)

xcopy /E /I /Y "%CHROMIUM_SRC%" "dist\XDA_Tools\%CHROMIUM_FOLDER%"
if errorlevel 1 (
    echo [ERROR] Failed to copy Chromium.
    pause
    exit /b 1
)
echo     Chromium copied to dist\XDA_Tools\%CHROMIUM_FOLDER%

echo.
echo [6/6] Done!
echo.
echo Your application is in:
echo   dist\XDA_Tools\XDA_Tools.exe
echo.
echo You can move the entire dist\XDA_Tools\ folder anywhere.
echo.
pause
