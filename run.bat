@echo off
rem Launch JARVIS on Windows (double-click). Runs run.ps1 without changing your PowerShell policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
if errorlevel 1 pause
