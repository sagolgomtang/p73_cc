<#
.SYNOPSIS
  Remove the P73JoyBridge auto-start task and stop any running bridge.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\uninstall_autostart.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$taskName = "P73JoyBridge"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "[uninstall] Task '$taskName' is not registered. Nothing to do."
} else {
    Write-Host "[uninstall] Removing task '$taskName'..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[uninstall] Task removed."
}

# Stop any currently running joy_bridge_win.py so memory is freed immediately.
# Match by command line (CommandLine column from CIM_Process / Win32_Process).
$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" `
         | Where-Object { $_.CommandLine -match 'joy_bridge_win\.py' }
if ($procs) {
    foreach ($p in $procs) {
        Write-Host "[uninstall] Stopping running bridge (PID $($p.ProcessId))..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "[uninstall] No running joy_bridge_win.py processes found."
}

Write-Host "[uninstall] Done."
