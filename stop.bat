@echo off
cd /d "%~dp0"
echo Stopping Persona Studio...
python daemon.py stop
echo.
pause