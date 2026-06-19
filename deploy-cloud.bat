@echo off
cd /d "%~dp0"
title Persona Studio — Cloud Deploy
echo.
echo ============================================================
echo   Persona Studio — Deploy to the Cloud (24/7)
echo   Your laptop can be OFF and the app still works.
echo ============================================================
echo.
python cloud_deploy.py
echo.
pause