@echo off
setlocal
cd /d "%~dp0"
netstat -ano | findstr LISTENING | findstr :8000 >nul
if %errorlevel% equ 0 (
    start "" "http://127.0.0.1:8000/"
    exit
)
start /B python -m uvicorn backend.main:app
:WAITLOOP
netstat -ano | findstr LISTENING | findstr :8000 >nul
if %errorlevel% neq 0 (
    timeout /t 1 /nobreak >nul
    goto WAITLOOP
)
start "" "http://127.0.0.1:8000/"
exit