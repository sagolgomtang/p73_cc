#!/usr/bin/env bash
# Install joystick auto-start (udev + systemd) for p73_cc.
#
# Idempotent: safe to re-run after `colcon build`. Re-detects the 8BitDo
# VID/PID and rewrites the udev rule + service env file accordingly.
#
# Usage:
#   bash scripts/install_joy_autostart.sh                 # install local mode (Linux dongle)
#   bash scripts/install_joy_autostart.sh --mode udp      # install UDP mode (Windows source)
#   bash scripts/install_joy_autostart.sh --mode both     # both instances enabled
#   bash scripts/install_joy_autostart.sh --user piene    # override target user (default: $SUDO_USER or current user)
#
# What it does:
#   1. Detects 8BitDo VID/PID from `lsusb` (you must plug the dongle first
#      OR pass --vid/--pid manually). For UDP-only mode the dongle isn't
#      needed; --mode udp skips device detection.
#   2. Adds the target user to the `input` group (sudo not needed for /dev/input/jsN later).
#   3. Installs:
#         /etc/udev/rules.d/99-8bitdo-joystick.rules
#         /etc/systemd/system/p73-joy-teleop@.service
#         /etc/p73_joy/p73_joy.env  (ROS_SETUP, WS_SETUP, ROS_DOMAIN_ID, RMW)
#   4. Reloads udev and systemd; enables the requested instance(s).

set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RULES_SRC="${PKG_DIR}/config/99-8bitdo-joystick.rules"
SERVICE_SRC="${PKG_DIR}/systemd/p73-joy-teleop@.service"

MODE="local"           # local | udp | both
TARGET_USER="${SUDO_USER:-${USER}}"
VID=""                 # auto-detect by default
PID=""                 # not used for matching (kept for log only)
ROS_SETUP_OVERRIDE=""
WS_SETUP_OVERRIDE=""
DOMAIN_OVERRIDE=""
RMW_OVERRIDE=""
FASTRTPS_PROFILES_OVERRIDE=""

usage() {
  sed -n '1,40p' "$0"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)        MODE="$2"; shift 2 ;;
    --user)        TARGET_USER="$2"; shift 2 ;;
    --vid)         VID="$2"; shift 2 ;;
    --pid)         PID="$2"; shift 2 ;;
    --ros-setup)   ROS_SETUP_OVERRIDE="$2"; shift 2 ;;
    --ws-setup)    WS_SETUP_OVERRIDE="$2"; shift 2 ;;
    --domain)      DOMAIN_OVERRIDE="$2"; shift 2 ;;
    --rmw)         RMW_OVERRIDE="$2"; shift 2 ;;
    --fastrtps-profiles) FASTRTPS_PROFILES_OVERRIDE="$2"; shift 2 ;;
    -h|--help)     usage ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$MODE" in
  local|udp|both) ;;
  *) echo "ERROR: --mode must be one of: local, udp, both" >&2; exit 2 ;;
esac

if [[ "$EUID" -ne 0 ]]; then
  echo "[install] Re-executing with sudo..."
  exec sudo --preserve-env=ROS_DISTRO,USER bash "$0" \
    --mode "$MODE" --user "$TARGET_USER" \
    ${VID:+--vid "$VID"} ${PID:+--pid "$PID"} \
    ${ROS_SETUP_OVERRIDE:+--ros-setup "$ROS_SETUP_OVERRIDE"} \
    ${WS_SETUP_OVERRIDE:+--ws-setup "$WS_SETUP_OVERRIDE"} \
    ${DOMAIN_OVERRIDE:+--domain "$DOMAIN_OVERRIDE"} \
    ${RMW_OVERRIDE:+--rmw "$RMW_OVERRIDE"} \
    ${FASTRTPS_PROFILES_OVERRIDE:+--fastrtps-profiles "$FASTRTPS_PROFILES_OVERRIDE"}
fi

# ---------------------------------------------------------------------------
# Detect ROS + workspace setup paths
# ---------------------------------------------------------------------------
ROS_SETUP="${ROS_SETUP_OVERRIDE}"
if [[ -z "$ROS_SETUP" ]]; then
  for d in /opt/ros/jazzy /opt/ros/iron /opt/ros/humble /opt/ros/rolling; do
    if [[ -f "$d/setup.bash" ]]; then ROS_SETUP="$d/setup.bash"; break; fi
  done
fi
if [[ -z "$ROS_SETUP" || ! -f "$ROS_SETUP" ]]; then
  echo "ERROR: could not find ROS setup.bash. Pass --ros-setup /opt/ros/<distro>/setup.bash" >&2
  exit 1
fi

WS_SETUP="${WS_SETUP_OVERRIDE}"
if [[ -z "$WS_SETUP" ]]; then
  WS_GUESS="/home/${TARGET_USER}/ros2_ws/install/setup.bash"
  if [[ -f "$WS_GUESS" ]]; then WS_SETUP="$WS_GUESS"; fi
fi
if [[ -z "$WS_SETUP" || ! -f "$WS_SETUP" ]]; then
  echo "ERROR: could not find workspace setup.bash. Did you 'colcon build'?" >&2
  echo "       Pass --ws-setup /path/to/install/setup.bash" >&2
  exit 1
fi

# Only set ROS_DOMAIN_ID / RMW in the env file when the user explicitly asked
# for them (via --domain, --rmw, or by exporting the env var before running
# this script). Otherwise leave them UNSET so that the systemd service inherits
# the same defaults as the rest of the robot (cc.cpp etc.) — typically domain=0
# and the system default RMW. Forcing them here was a footgun: it silently
# made the joy node unreachable from other ROS2 nodes on the same machine.
ROS_DOMAIN_ID_VAL="${DOMAIN_OVERRIDE:-${ROS_DOMAIN_ID:-}}"
RMW_VAL="${RMW_OVERRIDE:-${RMW_IMPLEMENTATION:-}}"
FASTRTPS_VAL="${FASTRTPS_PROFILES_OVERRIDE:-${FASTRTPS_DEFAULT_PROFILES_FILE:-}}"

echo "[install] mode=${MODE} user=${TARGET_USER}"
echo "[install] ROS_SETUP=${ROS_SETUP}"
echo "[install] WS_SETUP=${WS_SETUP}"
echo "[install] ROS_DOMAIN_ID=${ROS_DOMAIN_ID_VAL:-<inherit>}  RMW=${RMW_VAL:-<inherit>}"

# ---------------------------------------------------------------------------
# Detect 8BitDo VID (skipped for udp-only)
# ---------------------------------------------------------------------------
DETECTED_VID=""
DETECTED_PID=""
if [[ "$MODE" != "udp" ]]; then
  if [[ -z "$VID" ]]; then
    # Look for "8Bit" in lsusb. Falls back to hint and exits if nothing found.
    if line=$(lsusb 2>/dev/null | grep -i "8bit\|2dc8" | head -1); then
      if [[ -n "$line" ]]; then
        # line example: Bus 003 Device 005: ID 2dc8:3013 8BitDo 8BitDo Ultimate 2 Wireless
        token=$(echo "$line" | awk '{for(i=1;i<=NF;i++) if($i=="ID") print $(i+1)}')
        DETECTED_VID="${token%:*}"
        DETECTED_PID="${token#*:}"
      fi
    fi
    if [[ -z "$DETECTED_VID" ]]; then
      echo "WARN: 8BitDo dongle not found via lsusb. Plug it in and re-run, OR pass --vid <hex>." >&2
      echo "      Falling back to default VID=2dc8 (8BitDo)." >&2
      DETECTED_VID="2dc8"
    fi
  else
    DETECTED_VID="$VID"
    DETECTED_PID="${PID:-}"
  fi
  echo "[install] Using VID=${DETECTED_VID}${DETECTED_PID:+ PID=${DETECTED_PID}}"
fi

# ---------------------------------------------------------------------------
# input group membership
# ---------------------------------------------------------------------------
if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx input; then
  echo "[install] user '${TARGET_USER}' already in 'input' group"
else
  usermod -a -G input "$TARGET_USER"
  echo "[install] added '${TARGET_USER}' to 'input' group (re-login required for it to take effect)"
fi

# ---------------------------------------------------------------------------
# udev rule (only when local mode is wanted)
# ---------------------------------------------------------------------------
if [[ "$MODE" == "local" || "$MODE" == "both" ]]; then
  RULE_DST="/etc/udev/rules.d/99-8bitdo-joystick.rules"
  install -m 0644 "${RULES_SRC}" "${RULE_DST}"
  # Substitute detected VID into the installed rule (replace any '2dc8')
  sed -i "s/2dc8/${DETECTED_VID}/g" "${RULE_DST}"
  echo "[install] wrote ${RULE_DST}"
  udevadm control --reload-rules
  udevadm trigger --subsystem-match=input || true
fi

# ---------------------------------------------------------------------------
# /etc/p73_joy/p73_joy.env  (used by the systemd unit)
# ---------------------------------------------------------------------------
ENV_DIR="/etc/p73_joy"
ENV_FILE="${ENV_DIR}/p73_joy.env"
mkdir -p "$ENV_DIR"
{
  echo "ROS_SETUP=${ROS_SETUP}"
  echo "WS_SETUP=${WS_SETUP}"
  if [[ -n "$ROS_DOMAIN_ID_VAL" ]]; then
    echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID_VAL}"
  fi
  if [[ -n "$RMW_VAL" ]]; then
    echo "RMW_IMPLEMENTATION=${RMW_VAL}"
  fi
  if [[ -n "$FASTRTPS_VAL" ]]; then
    echo "FASTRTPS_DEFAULT_PROFILES_FILE=${FASTRTPS_VAL}"
  fi
} > "$ENV_FILE"
chmod 0644 "$ENV_FILE"
echo "[install] wrote ${ENV_FILE}"

# ---------------------------------------------------------------------------
# systemd template service
# ---------------------------------------------------------------------------
SERVICE_DST="/etc/systemd/system/p73-joy-teleop@.service"
install -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
sed -i "s/__P73_USER__/${TARGET_USER}/g" "${SERVICE_DST}"
echo "[install] wrote ${SERVICE_DST}"

systemctl daemon-reload

# ---------------------------------------------------------------------------
# Enable the requested instances
# ---------------------------------------------------------------------------
case "$MODE" in
  local)
    # local instance is started by udev, no need to enable persistently.
    # But starting once (if dongle is currently plugged) is helpful.
    if [[ -e /dev/input/p73_joystick || -e /dev/input/js0 ]]; then
      systemctl restart p73-joy-teleop@local.service || true
      echo "[install] started p73-joy-teleop@local.service (dongle present)"
    else
      echo "[install] dongle not present yet; will auto-start when you plug it in"
    fi
    ;;
  udp)
    systemctl enable --now p73-joy-teleop@udp.service
    echo "[install] enabled p73-joy-teleop@udp.service (always running)"
    ;;
  both)
    systemctl enable --now p73-joy-teleop@udp.service
    echo "[install] enabled p73-joy-teleop@udp.service (always running)"
    if [[ -e /dev/input/p73_joystick || -e /dev/input/js0 ]]; then
      systemctl restart p73-joy-teleop@local.service || true
      echo "[install] started p73-joy-teleop@local.service (dongle present)"
    else
      echo "[install] dongle not present yet; will auto-start when you plug it in"
    fi
    ;;
esac

cat <<EOF

[install] DONE.

Verify:
  systemctl status 'p73-joy-teleop@*.service'
  ros2 topic echo /p73/cmd_vel    # move sticks; should see Twist messages
  ros2 topic echo /p73/push_event # press 'A' button; should see std_msgs/Empty

If you were just added to the 'input' group, log out and back in once.
Re-run this script anytime after rebuilding the workspace; it is idempotent.
EOF
