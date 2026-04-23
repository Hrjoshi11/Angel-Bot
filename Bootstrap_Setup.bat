@echo off
setlocal
title Angel One Bot - Automated Setup
cd /d "%~dp0"

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python not found. Downloading Python 3.12...
    curl -L -o py_installer.exe https://www.python.org/ftp/python/3.12.2/python-3.12.2-amd64.exe
    echo [!] Installing Python... 
    echo [IMPORTANT] PLEASE CHECK 'Add Python to PATH' in the installer window!
    start /wait py_installer.exe /quiet InstallAllUsers=1 PrependPath=1
    del py_installer.exe
    echo [OK] Python installed. Please RESTART this script to continue.
    pause
    exit
)

:: 2. Install Dependencies
echo [*] Installing required libraries...
pip install fastapi uvicorn pydantic python-dotenv SmartApi pyotp requests
echo [OK] Libraries installed.

:: 3. Setup .env Credentials
echo.
echo ====================================================
echo      ANGEL ONE SMARTAPI CREDENTIALS SETUP
echo ====================================================
set /p API_KEY="Enter ANGEL_API_KEY: "
set /p CLIENT_CODE="Enter ANGEL_CLIENT_CODE: "
set /p PIN="Enter ANGEL_PIN (4 Digits): "
set /p TOTP="Enter ANGEL_TOTP_TOKEN: "

echo ANGEL_API_KEY="%API_KEY%" > .env
echo ANGEL_CLIENT_CODE="%CLIENT_CODE%" >> .env
echo ANGEL_PASSWORD="%PIN%" >> .env
echo ANGEL_TOTP_TOKEN="%TOTP%" >> .env
echo [OK] .env file created successfully.

:: 4. Create Desktop Shortcut
echo [*] Creating Desktop Shortcut...
set SCRIPT_PATH=%~dp0Launcher.vbs
set SHORTCUT_PATH=%USERPROFILE%\Desktop\Angel-Bot.lnk
powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%SHORTCUT_PATH%'); $Shortcut.TargetPath = '%SCRIPT_PATH%'; $Shortcut.IconLocation = 'shell32.dll, 12'; $Shortcut.Save()"

echo.
echo ====================================================
echo  SUCCESS! 
echo  1. An 'Angel-Bot' shortcut is now on your Desktop.
echo  2. Use the shortcut to start the bot.
echo ====================================================
pause