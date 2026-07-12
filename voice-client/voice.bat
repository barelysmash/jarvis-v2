@echo off
cd /d "%~dp0"
python voice_client.py
echo.
echo ---- exited with code %errorlevel% ----
pause