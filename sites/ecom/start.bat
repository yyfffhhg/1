@echo off
cd /d "%~dp0"
"%~dp0..\..\.venv\Scripts\python.exe" backend\server.py
pause
