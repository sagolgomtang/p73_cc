@echo off
REM Remove the P73JoyBridge auto-start task installed by install_autostart.bat.

setlocal

set "TASK_NAME=P73JoyBridge"

schtasks /Query /TN "%TASK_NAME%" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [uninstall] Task "%TASK_NAME%" is not registered. Nothing to do.
    exit /b 0
)

schtasks /Delete /TN "%TASK_NAME%" /F
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: failed to delete task "%TASK_NAME%". Try running from an elevated cmd.
    exit /b 1
)

echo [uninstall] Removed scheduled task "%TASK_NAME%".

REM Also kill any currently running joy_bridge_win.py so memory is freed now.
REM (taskkill /F is best-effort; we don't fail if there's nothing to kill.)
for /f "tokens=2" %%P in ('tasklist /V /FO CSV ^| findstr /I "joy_bridge_win.py"') do (
    taskkill /F /PID %%~P >nul 2>nul
)
echo [uninstall] Done.

endlocal
