#!/usr/bin/env bash
# Remove joystick auto-start installed by install_joy_autostart.sh.
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  exec sudo bash "$0" "$@"
fi

systemctl disable --now p73-joy-teleop@local.service 2>/dev/null || true
systemctl disable --now p73-joy-teleop@udp.service   2>/dev/null || true
rm -f /etc/systemd/system/p73-joy-teleop@.service
rm -f /etc/udev/rules.d/99-8bitdo-joystick.rules
rm -rf /etc/p73_joy
systemctl daemon-reload
udevadm control --reload-rules
echo "[uninstall] removed udev rule, systemd unit, and env file."
