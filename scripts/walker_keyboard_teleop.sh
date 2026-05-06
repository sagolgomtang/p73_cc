#!/usr/bin/env bash
# Run walker_teleop.py (keyboard teleop) with safe defaults:
#   * Forces the SYSTEM python3 (3.12) so rclpy resolves; conda envs broken.
#   * Pauses the joystick systemd service while running, then restores it on
#     exit, so the two teleops never fight over /p73/cmd_vel.
#
# Usage:
#   bash ~/ros2_ws/src/p73_cc/scripts/walker_keyboard_teleop.sh
#   # or, recommended, add an alias once:
#   #   echo "alias walker-key='bash ~/ros2_ws/src/p73_cc/scripts/walker_keyboard_teleop.sh'" >> ~/.bashrc
#   #   source ~/.bashrc
#   #   walker-key
#
# sudo on systemctl: this script tries `sudo -n` first (no password). If you
# don't want to type your sudo password each time, add this single line to
# `sudo visudo`:
#   piene ALL=(ALL) NOPASSWD: /usr/bin/systemctl start p73-joy-teleop@udp.service, /usr/bin/systemctl stop p73-joy-teleop@udp.service
# (replace `piene` with your username)

set -e

SERVICE="p73-joy-teleop@udp.service"
TELEOP_PY="${HOME}/ros2_ws/src/p73_cc/scripts/walker_teleop.py"

# --- env hygiene: kick out conda, force system PATH so python3 = 3.12 -----
unset CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PROMPT_MODIFIER PYTHONPATH
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# --- ROS env (auto-detect distro) ------------------------------------------
ROS_FOUND=0
for d in /opt/ros/jazzy /opt/ros/humble /opt/ros/iron /opt/ros/rolling; do
    if [[ -f "$d/setup.bash" ]]; then
        # shellcheck disable=SC1091
        source "$d/setup.bash"
        ROS_FOUND=1
        break
    fi
done
if [[ "$ROS_FOUND" -eq 0 ]]; then
    echo "[walker-key] ERROR: no ROS2 found in /opt/ros/. Install ROS2 first."
    exit 1
fi
# shellcheck disable=SC1091
source "${HOME}/ros2_ws/install/setup.bash"

# --- pause joy systemd if active so two publishers don't race -------------
WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE"; then
    WAS_ACTIVE=1
    echo "[walker-key] pausing $SERVICE to avoid cmd_vel race..."
    if ! sudo -n systemctl stop "$SERVICE" 2>/dev/null; then
        sudo systemctl stop "$SERVICE"
    fi
fi

# --- restore on exit (Ctrl+C, normal exit, or crash) ----------------------
cleanup() {
    if [[ "$WAS_ACTIVE" -eq 1 ]]; then
        echo
        echo "[walker-key] restarting $SERVICE..."
        if ! sudo -n systemctl start "$SERVICE" 2>/dev/null; then
            sudo systemctl start "$SERVICE"
        fi
    fi
}
trap cleanup EXIT INT TERM

# --- show identity for debugging ------------------------------------------
echo "[walker-key] python = $(command -v python3)  ($(python3 --version 2>&1))"
echo "[walker-key] script = $TELEOP_PY"
echo "[walker-key] joy systemd was active: $WAS_ACTIVE"
echo

# --- run -------------------------------------------------------------------
exec python3 "$TELEOP_PY"
