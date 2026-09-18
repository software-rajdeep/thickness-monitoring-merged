@echo off
rem Run the OFFLINE local version from the backend source files (no exe, no tray).
rem Equivalent to: python backend\local_main.py
cd /d "%~dp0.."

echo [local-backend-app] Starting offline local server from source ...
echo [local-backend-app] Open http://localhost:5002 in your browser.
where python >nul 2>nul
if %errorlevel%==0 (
    python "backend\local_main.py"
) else (
    py -3 "backend\local_main.py"
)

endlocal
