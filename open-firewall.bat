@echo off
echo Allowing Persona Studio through Windows Firewall (port 7860)...
echo Other devices on your Wi-Fi can then use the LAN link.
netsh advfirewall firewall add rule name="Persona Studio" dir=in action=allow protocol=TCP localport=7860
echo Done.
pause