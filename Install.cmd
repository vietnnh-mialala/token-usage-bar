@echo off
rem Double-click this to install Token Usage Bar. It launches install.ps1 with an
rem execution policy that bypasses the "downloaded scripts are blocked" rule, so
rem it works even when right-clicking the .ps1 silently does nothing.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
