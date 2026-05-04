# P73 Joystick Bridge — Windows side

Plug the 8BitDo Ultimate 2 dongle into your Windows laptop. This bridge reads
the joystick with `pygame` and sends it as UDP packets to the Linux
workstation / robot, where `joy_udp_receiver.py` (in the same `p73_cc`
package) decodes them into a standard `/joy` topic.

> **Why no ROS2 on Windows?** ROS2 on Windows is heavy and brittle. A small
> Python script + UDP works on every Windows version with just `pygame`.
> Combined with Tailscale, this works whether you're at home or in the lab.

## One-time setup

1. Install Python 3 (e.g. Anaconda — `python --version` should work in PowerShell).
2. Install [Tailscale for Windows](https://tailscale.com/download/windows) and
   log in with the **same account** as your Linux machines. Note the Tailscale
   IP of the workstation (`100.x.x.x`).
3. Get the bridge files from the workstation:
   ```powershell
   # Pick any folder you like; this is just an example.
   mkdir $HOME\p73_joy_windows -ErrorAction SilentlyContinue | Out-Null
   cd $HOME\p73_joy_windows
   scp piene@<workstation-tailscale-ip>:/home/piene/ros2_ws/src/p73_cc/windows/* .
   ```
4. Install pygame:
   ```powershell
   python -m pip install -r requirements.txt
   ```
5. Register the `joy` / `joyq` shortcut commands (one-time):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install_alias.ps1 -Target <workstation-tailscale-ip>
   ```
   This writes three short functions into your PowerShell `$PROFILE` so every
   new shell knows them, and removes the old (non-working) USB-event task if
   it was ever installed.

## Daily use

After step 5, open any PowerShell window and use:

| Command | What it does | Memory while running |
|---|---|---|
| `joy` | Foreground bridge with console logs. Ctrl+C to stop. | ~50 MB |
| `joyq` | Hidden background bridge. **Auto-exits when you unplug the dongle.** | ~50 MB |
| `joy-stop` | Kill any running bridge process. | — |

Memory while idle (none of the above running) = **0 MB**.

The typical workflow is:
1. Plug the 8BitDo dongle into the laptop.
2. Make sure the controller is powered on, in **X mode** (XInput).
3. `joyq` (or `joy` if you want to see logs).
4. Drive the robot with the sticks.
5. Unplug the dongle when done. `joyq` notices and exits; memory back to 0.

## Why not auto-start on USB plug?

We tried. On this PC the Windows Task Scheduler USB-event trigger does not
fire (the OS doesn't write a `Microsoft-Windows-Kernel-PnP/Configuration`
event 410 when the 8BitDo dongle is plugged in — verified empirically). The
next best thing would be a PowerShell WMI watcher running on logon, but
that costs ~30 MB of resident memory all the time. Per project preference
we trade "automatic on plug" for "type one word" — `joyq`.

If you change your mind and want the WMI watcher version, ask and we can
add it.

## Troubleshooting

- **`python` is not recognized / runs Microsoft Store stub.** Open an
  Anaconda Prompt, or pass the absolute path:
  `C:\Users\<you>\anaconda3\python.exe joy_bridge_win.py --target 100.x.x.x`
- **`joy` opens but says "waiting for a joystick to be plugged in..." forever.**
  pygame can't see the controller. Most common causes:
    * Controller is powered off — hold HOME for ~3 s.
    * Controller is in the wrong mode — slide the side switch to **X** (XInput).
      `D` (DirectInput), `S` (Switch), `M` (Mac) won't be picked up by pygame.
    * Open `joy.cpl` (Win+R → joy.cpl) and confirm Windows lists
      "8BitDo Ultimate 2 Wireless Controller for PC".
- **`joy` works, but the workstation `/joy` topic has all zeros.** Either the
  receiver isn't running (start it on the workstation, see project root README),
  or the mapping in `p73_cc/config/joy_8bitdo_ultimate2.yaml` doesn't match
  this firmware's axis order. Only the YAML needs editing — no code change.
- **Packets not arriving on the workstation.** Smoke-test the network:
  ```powershell
  $u = New-Object Net.Sockets.UdpClient
  $b = [Text.Encoding]::ASCII.GetBytes("ping")
  $u.Connect("100.x.x.x", 35731); $u.Send($b, $b.Length) | Out-Null; $u.Close()
  ```
  If the workstation `joy_udp_receiver` doesn't see this, check Tailscale
  (`tailscale status` on both ends) and the Linux UFW rule
  `35731/udp on tailscale0 ALLOW`.

## File reference

| File | Purpose |
|---|---|
| `joy_bridge_win.py` | The actual bridge. pygame → UDP. |
| `install_alias.ps1` | Adds `joy` / `joyq` / `joy-stop` to your `$PROFILE`. |
| `uninstall_alias.ps1` | Removes them and kills any running bridge. |
| `uninstall_autostart.ps1` / `.bat` | Legacy: removes the old `P73JoyBridge` scheduled task. `install_alias.ps1` already does this automatically; these are kept only for users who registered the old task and never ran `install_alias.ps1`. |
| `requirements.txt` | `pygame`. |

## Wire format reference

See the docstring at the top of `joy_bridge_win.py` and
`scripts/joy_udp_receiver.py` on the Linux side. They must stay in sync:
magic `P73J`, uint16 version, uint32 seq, uint8 num_axes, uint8 num_buttons,
float32×N axes, uint8×M buttons (all little-endian).
