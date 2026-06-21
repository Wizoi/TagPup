@echo off
REM setup.bat
REM Wrapper to run setup.ps1 in PowerShell bypass mode
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
