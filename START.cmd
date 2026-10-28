@echo off
cd /d "%~dp0"
echo Kinopolis: http://127.0.0.1:8000
echo Press Ctrl+C to stop the server.
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 -m cinema
) else (
    python -m cinema
)
pause
