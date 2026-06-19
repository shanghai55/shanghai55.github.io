@echo off
cd /d "%~dp0"
echo Installing Persona Studio as a background app...
echo This will:
echo   - Start Persona Studio now (no CMD window needed)
echo   - Auto-start every time you log into Windows
echo   - Add a desktop shortcut to open it in your browser
echo   - Show a tray icon (purple) for Open / Quit
echo   - Enable LAN + public links for phone/other devices
echo.
netsh advfirewall firewall add rule name="Persona Studio" dir=in action=allow protocol=TCP localport=7860 >nul 2>&1
python daemon.py install
echo.
pause