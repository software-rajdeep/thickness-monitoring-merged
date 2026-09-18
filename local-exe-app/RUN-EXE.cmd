@echo off
rem Launch the Thickness Monitoring Windows appliance.
rem 1) If the built exe exists, run it (fully self-contained, no Python needed).
rem 2) Otherwise fall back to running the same code from source.
cd /d "%~dp0.."

if exist "local\dist\Thickness Monitoring.exe" (
    echo [local-exe-app] Starting Thickness Monitoring.exe ...
    start "" "local\dist\Thickness Monitoring.exe"
    goto :done
)

echo [local-exe-app] Built exe not found (local\dist\Thickness Monitoring.exe).
echo [local-exe-app] Falling back to running from source: python backend\local_gui.py
echo [local-exe-app] Requires Python and local\requirements-local.txt installed.
where python >nul 2>nul
if %errorlevel%==0 (
    python "backend\local_gui.py"
) else (
    py -3 "backend\local_gui.py"
)

:done
endlocal
