<#
.SYNOPSIS
  Register `joy`, `joyq`, and `joy-stop` shortcut functions in the user's
  PowerShell profile, and remove the old (non-working) USB-trigger task
  if it's present.

.DESCRIPTION
  After running this once, every NEW PowerShell session exposes:

    joy        - run joy_bridge_win.py in the FOREGROUND. Logs to console.
                 Ctrl+C to stop. Use this when you want to see what's
                 happening (debugging, first-time setup, etc.).

    joyq       - run joy_bridge_win.py HIDDEN in the background with
                 --exit-on-disconnect --connect-timeout 5. The bridge
                 auto-exits when you unplug the dongle, so memory drops
                 back to 0. Use this for normal operation.

    joy-stop   - kill any running joy_bridge_win.py process.

  Memory profile:
    - When you do NOT run any of the above: 0 MB used.
    - While `joyq` is running with the dongle plugged: ~50 MB.
    - When you unplug the dongle: ~50 MB freed automatically (joyq's bridge
      detects unplug and self-exits).

  Why this instead of auto-start on USB plug? On this PC the Windows
  Task Scheduler USB-event trigger does not fire (the OS doesn't generate
  a Kernel-PnP event for the 8BitDo dongle). The next-best option is a
  PowerShell WMI watcher that stays resident (~30 MB), but per-user
  preference we keep memory at 0 by trading "automatic on plug" for
  "type one word".

.PARAMETER Target
  Tailscale (or LAN) IP of the Linux receiver. Default 100.121.81.113.

.PARAMETER Port
  UDP port the receiver listens on. Default 35731.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install_alias.ps1
  powershell -ExecutionPolicy Bypass -File .\install_alias.ps1 -Target 100.121.81.113
#>

[CmdletBinding()]
param(
    [string]$Target = "100.121.81.113",
    [int]$Port = 35731
)

$ErrorActionPreference = 'Stop'

# ---- Detect Python interpreters ------------------------------------------
$pythonExe  = $null
$pythonwExe = $null
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) {
    $pythonExe  = $pyCmd.Source
    $pywCmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($pywCmd) { $pythonwExe = $pywCmd.Source } else { $pythonwExe = $pythonExe }
} else {
    Write-Error @"
python.exe not found in PATH.
If you're using Anaconda, run this script from an Anaconda Prompt or activate
your env first ('conda activate base'), or pass the absolute path manually.
"@
}
Write-Host "[install_alias] python  = $pythonExe"
Write-Host "[install_alias] pythonw = $pythonwExe"

# ---- Resolve absolute path to joy_bridge_win.py --------------------------
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridge = Join-Path $scriptDir "joy_bridge_win.py"
if (-not (Test-Path $bridge)) {
    Write-Error "joy_bridge_win.py not found next to this script: $bridge"
}
Write-Host "[install_alias] bridge  = $bridge"

# ---- Remove the old USB-trigger task if it's present ---------------------
$old = Get-ScheduledTask -TaskName "P73JoyBridge" -ErrorAction SilentlyContinue
if ($old) {
    Write-Host "[install_alias] Removing old P73JoyBridge task (USB-trigger; was non-functional)..."
    Unregister-ScheduledTask -TaskName "P73JoyBridge" -Confirm:$false
}

# ---- Build the function block to inject into $PROFILE -------------------
# IMPORTANT: We construct this with a here-string and explicit single-quotes
# around interpolated paths so spaces/backslashes stay intact in the profile.
# Variables that should resolve at install time use $-interpolation; variables
# that should resolve at function-call time are escaped with backtick.
$BEGIN_TAG = "# === P73 walker joystick bridge (managed by install_alias.ps1) ==="
$END_TAG   = "# === end P73 walker joystick bridge ==="

$functionBlock = @"

$BEGIN_TAG
function joy {
    & '$pythonExe' '$bridge' --target $Target --port $Port @args
}
function joyq {
    Start-Process -WindowStyle Hidden -FilePath '$pythonwExe' ``
        -ArgumentList '$bridge','--target','$Target','--port','$Port','--quiet','--exit-on-disconnect','--connect-timeout','5'
    Write-Host "joyq: launched hidden bridge to ${Target}:${Port}. Use 'joy-stop' to kill."
}
function joy-stop {
    `$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
              Where-Object { `$_.CommandLine -match 'joy_bridge_win\.py' }
    if (`$procs) {
        foreach (`$p in `$procs) { Stop-Process -Id `$p.ProcessId -Force }
        Write-Host "joy-stop: killed `$(`$procs.Count) bridge process(es)."
    } else {
        Write-Host "joy-stop: no running bridge."
    }
}
$END_TAG

"@

# ---- Make sure $PROFILE exists -------------------------------------------
if (-not (Test-Path $PROFILE)) {
    New-Item -ItemType File -Path $PROFILE -Force | Out-Null
    Write-Host "[install_alias] Created $PROFILE"
}

# ---- Strip any prior block (idempotent) ----------------------------------
$existing = Get-Content -Raw -Path $PROFILE -ErrorAction SilentlyContinue
if ($existing -and $existing -match [regex]::Escape($BEGIN_TAG)) {
    $pattern = '(?ms)\s*' + [regex]::Escape($BEGIN_TAG) + '.*?' + [regex]::Escape($END_TAG) + '\s*'
    $cleaned = [regex]::Replace($existing, $pattern, "`r`n")
    Set-Content -Path $PROFILE -Value $cleaned -NoNewline
    Write-Host "[install_alias] Replaced previous P73 block in profile."
}

# ---- Append the new block ------------------------------------------------
Add-Content -Path $PROFILE -Value $functionBlock

Write-Host ""
Write-Host "[install_alias] DONE." -ForegroundColor Green
Write-Host "  Profile updated: $PROFILE"
Write-Host ""
Write-Host "Functions registered:"
Write-Host "  joy       - foreground bridge (Ctrl+C to stop, see logs)"
Write-Host "  joyq      - hidden background bridge (auto-exits on dongle unplug)"
Write-Host "  joy-stop  - kill any running bridge"
Write-Host ""
Write-Host "Activate now without opening a new shell:"
Write-Host "  . `$PROFILE"
Write-Host ""
Write-Host "Test:"
Write-Host "  joy           # foreground, see joystick logs"
Write-Host "  joyq          # hidden background"
Write-Host "  Get-Process pythonw    # confirm joyq is running"
Write-Host "  joy-stop      # stop"
