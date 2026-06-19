@echo off
cd /d "%~dp0"
echo Starting Persona Studio Web UI...
echo.
python web_server.py --open
pause