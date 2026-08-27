@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo 启动 电商生图 Studio  http://127.0.0.1:8000
python backend\main.py
pause
