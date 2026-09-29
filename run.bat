@echo off
REM Starts the AJIO Feed Verify local server. Double-click this file, or run it from any terminal -
REM it always operates from its own folder, so it doesn't matter where you launch it from.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Could not find .venv\Scripts\python.exe under %~dp0.venv
    echo Make sure this .bat is sitting in the ajio-feed-verify folder and the virtual environment is set up.
    pause
    exit /b 1
)

echo Starting AJIO Feed Verify server...
echo Once you see "Uvicorn running on http://127.0.0.1:8000", your browser tab will open on its own.
echo Press Ctrl+C in this window to stop the server.
echo.

start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"
.venv\Scripts\python.exe -m uvicorn web.api:app --port 8000

pause
