<#
.SYNOPSIS
  Remove the joy / joyq / joy-stop functions from the user's PowerShell
  profile, and kill any running bridge process.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\uninstall_alias.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$BEGIN_TAG = "# === P73 walker joystick bridge (managed by install_alias.ps1) ==="
$END_TAG   = "# === end P73 walker joystick bridge ==="

if (Test-Path $PROFILE) {
    $content = Get-Content -Raw -Path $PROFILE -ErrorAction SilentlyContinue
    if ($content -and $content -match [regex]::Escape($BEGIN_TAG)) {
        $pattern = '(?ms)\s*' + [regex]::Escape($BEGIN_TAG) + '.*?' + [regex]::Escape($END_TAG) + '\s*'
        $cleaned = [regex]::Replace($content, $pattern, "`r`n")
        Set-Content -Path $PROFILE -Value $cleaned -NoNewline
        Write-Host "[uninstall_alias] Removed P73 block from $PROFILE"
    } else {
        Write-Host "[uninstall_alias] No P73 block found in $PROFILE"
    }
} else {
    Write-Host "[uninstall_alias] $PROFILE does not exist; nothing to do."
}

# Kill any currently running joy_bridge_win.py.
$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" `
         | Where-Object { $_.CommandLine -match 'joy_bridge_win\.py' }
if ($procs) {
    foreach ($p in $procs) {
        Write-Host "[uninstall_alias] Stopping bridge (PID $($p.ProcessId))..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[uninstall_alias] Done. Open a new PowerShell window for the change to take effect."
