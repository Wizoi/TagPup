@echo off
REM run.bat
REM Wrapper to run run.ps1 in PowerShell bypass mode, passing all arguments
powershell -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
