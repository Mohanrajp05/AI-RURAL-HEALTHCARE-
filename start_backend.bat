@echo off

REM Start the backend server
cd /d "%~dp0backend"
venv\Scripts\python app.py
