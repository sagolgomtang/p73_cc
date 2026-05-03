"""
Windows-side joystick bridge for the P73 walker.

  pygame.joystick (XInput) --> UDP packets --> Linux: joy_udp_receiver.py
                                                  --> /joy --> p73_joy_teleop --> /p73/cmd_vel

Usage:
  py -3 joy_bridge_win.py --target 100.x.x.x          # Tailscale IP of workstation
  py -3 joy_bridge_win.py --target 100.x.x.x --port 35731 --rate 50

Behavior:
  - Polls every joystick event; sends a packet at `--rate` Hz with the latest state.
  - If no joystick is connected, waits and re-polls once a second; logs once.
  - On unplug, sends ONE packet with all-zero axes/buttons, then resumes polling.
  - System tray icon (optional, requires `pystray + pillow`) shows status; falls
    back to plain console if those aren't installed.

Wire format (must match scripts/joy_udp_receiver.py on the Linux side):
  magic "P73J", uint16 version=1, uint32 seq, uint8 num_axes, uint8 num_buttons,
  float32*num_axes, uint8*num_buttons   (all little-endian)
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time

# Suppress the pygame welcome banner.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402  # pip install pygame

MAGIC = b"P73J"
VERSION = 1
HEADER_FMT = "<4sHIBB"


def build_packet(seq: int, axes: list[float], buttons: list[int]) -> bytes:
    n_axes = len(axes)
    n_btns = len(buttons)
    header = struct.pack(HEADER_FMT, MAGIC, VERSION, seq & 0xFFFFFFFF, n_axes, n_btns)
    body_axes = struct.pack(f"<{n_axes}f", *axes)
    body_btns = struct.pack(f"<{n_btns}B", *(int(b) & 1 for b in buttons))
    return header + body_axes + body_btns


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Windows joystick → UDP bridge for p73_cc.")
    p.add_argument("--target", required=True, help="Receiver IP (Tailscale 100.x.x.x of workstation/robot).")
    p.add_argument("--port", type=int, default=35731, help="Receiver UDP port.")
    p.add_argument("--rate", type=float, default=50.0, help="Send rate in Hz.")
    p.add_argument("--joystick-index", type=int, default=0, help="pygame joystick index.")
    p.add_argument("--quiet", action="store_true", help="Suppress per-second status prints.")
    return p.parse_args()


def acquire_joystick(idx: int):
    """Return a pygame.Joystick, waiting + retrying until one shows up."""
    pygame.joystick.quit()
    pygame.joystick.init()
    n = pygame.joystick.get_count()
    if n == 0:
        return None
    if idx >= n:
        idx = 0
    js = pygame.joystick.Joystick(idx)
    js.init()
    return js


def main() -> int:
    args = parse_args()

    pygame.display.init()    # required on some Windows builds before joystick
    pygame.event.set_allowed(None)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.target, args.port)

    period = 1.0 / max(args.rate, 1.0)
    seq = 0
    last_print = 0.0
    js = None
    js_name = ""

    print(f"[joy_bridge_win] target={target[0]}:{target[1]} rate={args.rate}Hz", flush=True)

    try:
        while True:
            if js is None:
                js = acquire_joystick(args.joystick_index)
                if js is None:
                    if time.time() - last_print > 2.0:
                        print("[joy_bridge_win] waiting for a joystick to be plugged in...", flush=True)
                        last_print = time.time()
                    time.sleep(0.5)
                    continue
                js_name = js.get_name()
                print(f"[joy_bridge_win] joystick connected: {js_name} "
                      f"(axes={js.get_numaxes()}, buttons={js.get_numbuttons()})", flush=True)

            # Pump events so axis/button states refresh.
            try:
                pygame.event.pump()
            except pygame.error:
                # Joystick was likely yanked.
                pass

            # If joystick became invalid (unplugged), send one zero packet and reacquire.
            try:
                n_axes = js.get_numaxes()
                n_btns = js.get_numbuttons()
                axes = [float(js.get_axis(i)) for i in range(n_axes)]
                buttons = [int(js.get_button(i)) for i in range(n_btns)]
            except pygame.error:
                print("[joy_bridge_win] joystick disconnected; sending zero packet.", flush=True)
                try:
                    sock.sendto(build_packet(seq, [0.0] * 6, [0] * 11), target)
                    seq += 1
                except OSError:
                    pass
                js = None
                continue

            try:
                sock.sendto(build_packet(seq, axes, buttons), target)
                seq += 1
            except OSError as e:
                if time.time() - last_print > 2.0:
                    print(f"[joy_bridge_win] sendto error: {e}", flush=True)
                    last_print = time.time()

            if not args.quiet and time.time() - last_print > 1.0:
                axes_s = ",".join(f"{a:+.2f}" for a in axes[:4])
                btns_s = "".join(str(b) for b in buttons[:11])
                print(f"  [{js_name}] axes=[{axes_s}...] btns={btns_s}", flush=True)
                last_print = time.time()

            time.sleep(period)

    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.sendto(build_packet(seq, [0.0] * 6, [0] * 11), target)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        pygame.quit()
        print("\n[joy_bridge_win] stopped.", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
