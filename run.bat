@echo off
cd /d "%~dp0"
echo Starting Persona Studio in the background...
echo No need to keep this window open.
echo.
python daemon.py start --open
echo.
echo You can close this window. Persona Studio keeps running.
echo To stop it: double-click stop.bat or use the tray icon - Quit
echo.
timeout /t 4 >nul