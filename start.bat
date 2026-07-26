@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting backend...
start "" python backend\app.py
echo Done. http://localhost:8765
timeout /t 3

