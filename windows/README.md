# P73 Joystick Bridge — Windows side

Plug the 8BitDo Ultimate 2 dongle into your Windows laptop. This bridge reads
the joystick state with `pygame` and sends it as UDP packets to the Linux
workstation / robot, where `joy_udp_receiver.py` (in the same `p73_cc`
package) decodes them into a standard `/joy` topic.

> **Why no ROS2 on Windows?** ROS2 on Windows is heavy and brittle. A small
> Python script + UDP works on every Windows version with just `pygame`.
> Combined with Tailscale, this works whether you're at home or in the lab.

## One-time setup

1. Install Python 3 (already done if `py -3 --version` works).
2. Install [Tailscale for Windows](https://tailscale.com/download/windows) and
   log in with the **same account** as your Linux machines. Note the Tailscale
   IP of the workstation (`100.x.x.x`).
3. Get the bridge files. Easiest is to clone the whole repo your Linux side
   uses (so you stay in sync):
   ```powershell
   git clone <p73_cc-repo-url>
   cd p73_cc\windows
   ```
4. Install pygame:
   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

## Run it (manual, for testing)

```powershell
py -3 joy_bridge_win.py --target 100.x.x.x
```

Move the sticks. On the Linux side, `ros2 topic echo /joy` should now show
messages, and `/p73/cmd_vel` should follow.

## Auto-start at logon

```powershell
.\install_autostart.bat 100.x.x.x
```

This creates a scheduled task `P73JoyBridge` that runs `joy_bridge_win.py` at
every logon (no visible console). To remove:

```powershell
schtasks /Delete /TN "P73JoyBridge" /F
```

## Troubleshooting

- **No joystick detected.** Make sure the dongle is plugged in and Windows
  shows the controller in *Settings → Bluetooth & devices → Devices*. Then
  test with `joy.cpl` (Game Controllers control panel) to confirm Windows
  sees axes/buttons.
- **Linux side sees `/joy` but `/p73/cmd_vel` is always 0.** Check the
  mapping in `p73_cc/config/joy_8bitdo_ultimate2.yaml`. Some 8BitDo firmware
  versions use a slightly different axis order; only the YAML needs editing.
- **Packets not arriving.** From the Windows machine,
  `Test-NetConnection -ComputerName 100.x.x.x -Port 35731 -Udp` (best-effort
  for UDP). Make sure both machines are up in `tailscale status`.
- **Wire format reference.** See the docstring at the top of
  `joy_bridge_win.py` and `scripts/joy_udp_receiver.py` — they must match.
