@echo off
REM Register joy_bridge_win.py to run on Windows logon.
REM
REM Usage (from this directory, in cmd.exe or PowerShell):
REM   install_autostart.bat 100.x.x.x         (workstation Tailscale IP)
REM   install_autostart.bat 100.x.x.x 35731   (custom port)
REM
REM Uninstall:
REM   schtasks /Delete /TN "P73JoyBridge" /F

setlocal

if "%~1"=="" (
    echo Usage: install_autostart.bat ^<receiver_ip^> [port]
    echo   receiver_ip = Tailscale IP of the Linux workstation/robot
    exit /b 1
)

set TARGET_IP=%~1
set TARGET_PORT=%~2
if "%TARGET_PORT%"=="" set TARGET_PORT=35731

REM Find Python (prefer py launcher; fall back to python in PATH).
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set PY=py -3
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set PY=python
    ) else (
        echo ERROR: Python is not installed or not on PATH.
        exit /b 2
    )
)

REM Resolve absolute path to joy_bridge_win.py (this script's directory).
set SCRIPT_DIR=%~dp0
set BRIDGE=%SCRIPT_DIR%joy_bridge_win.py

if not exist "%BRIDGE%" (
    echo ERROR: %BRIDGE% not found.
    exit /b 3
)

REM Make sure pygame is installed (best-effort, doesn't fail the script).
echo [install] Ensuring pygame is installed...
%PY% -m pip install --quiet --upgrade pygame

REM Register the scheduled task. Runs at logon, restarts if it crashes,
REM no console window (pythonw.exe).
set TASK_NAME=P73JoyBridge

REM Use pythonw to avoid a visible console at every logon.
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
    set PYW=pythonw
) else (
    set PYW=%PY%
)

set ACTION="%PYW% \"%BRIDGE%\" --target %TARGET_IP% --port %TARGET_PORT% --quiet"

echo [install] Creating scheduled task "%TASK_NAME%"...
schtasks /Create /TN "%TASK_NAME%" /SC ONLOGON /RL LIMITED /F ^
  /TR %ACTION%

if %ERRORLEVEL% NEQ 0 (
    echo ERROR: schtasks failed. Try running this .bat as Administrator.
    exit /b 4
)

echo [install] Done. The bridge will start automatically on next logon.
echo          To start it right now without rebooting, run:
echo            %PY% "%BRIDGE%" --target %TARGET_IP% --port %TARGET_PORT%
echo          To remove the auto-start:
echo            schtasks /Delete /TN "%TASK_NAME%" /F

endlocal
