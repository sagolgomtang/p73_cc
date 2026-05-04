"""
Windows-side joystick bridge for the P73 walker.

  pygame.joystick (XInput) --> UDP packets --> Linux: joy_udp_receiver.py
                                                  --> /joy --> p73_joy_teleop --> /p73/cmd_vel

Two run modes:

  (a) Manual / always-on (default):
        python joy_bridge_win.py --target 100.x.x.x
      Stays alive forever; if no joystick is present, polls every 0.5 s and
      logs once. Plug/unplug freely. Quit with Ctrl+C.

  (b) USB-event-triggered (auto-start mode used by install_autostart.bat):
        python joy_bridge_win.py --target 100.x.x.x --exit-on-disconnect --connect-timeout 5
      The Windows Task Scheduler launches this on every USB device-started
      event. The script:
        1. Acquires a single-instance lock on 127.0.0.1:35730. If another
           instance is already running, exits 0 immediately (no spam launches).
        2. Tries to acquire the joystick. If none appears within
           --connect-timeout seconds, sends ZERO packet and exits 0
           (so memory drops to 0 when nothing is plugged in).
        3. Streams joystick state at --rate Hz.
        4. On unplug, sends ONE zero packet and exits 0 (Task Scheduler will
           re-launch it next time the dongle is plugged back in).
      This keeps memory at 0 while no dongle is plugged, and ~50 MB only
      while the dongle is connected.

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

# Loopback port used purely as a single-instance mutex.
SINGLE_INSTANCE_PORT = 35730


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
    p.add_argument(
        "--exit-on-disconnect",
        action="store_true",
        help="Exit cleanly when the joystick is unplugged (used by USB-event-triggered auto-start).",
    )
    p.add_argument(
        "--connect-timeout",
        type=float,
        default=0.0,
        help=(
            "If > 0, exit cleanly after this many seconds without finding a joystick. "
            "Used by USB-event-triggered auto-start so spurious launches release memory fast. "
            "0 = wait forever (default for manual use)."
        ),
    )
    return p.parse_args()


def acquire_joystick(idx: int):
    """Return a pygame.Joystick if one is present, else None. Re-init each call."""
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


def acquire_single_instance_lock() -> socket.socket | None:
    """Bind a UDP socket on 127.0.0.1 as a process-wide mutex.

    Returns the bound socket on success (caller must keep it alive), or None
    if another instance already owns the lock. The OS releases the port when
    this process exits, so it's a self-cleaning lock.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Don't set SO_REUSEADDR — we want bind() to fail if someone else holds it.
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        s.close()
        return None
    return s


def send_zero_packet(sock: socket.socket, target: tuple[str, int], seq: int) -> None:
    """Best-effort: tell the receiver to flatten cmd_vel before we exit."""
    try:
        sock.sendto(build_packet(seq, [0.0] * 6, [0] * 11), target)
    except OSError:
        pass


def main() -> int:
    args = parse_args()

    # Single-instance lock prevents Task-Scheduler-fired duplicates from stacking
    # when several USB plug events fire close together.
    lock = acquire_single_instance_lock()
    if lock is None:
        # Another instance is already running — that's fine, nothing to do.
        print("[joy_bridge_win] another instance is already running; exiting.", flush=True)
        return 0

    pygame.display.init()    # required on some Windows builds before joystick
    pygame.event.set_allowed(None)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.target, args.port)

    period = 1.0 / max(args.rate, 1.0)
    seq = 0
    last_print = 0.0
    js = None
    js_name = ""

    print(
        f"[joy_bridge_win] target={target[0]}:{target[1]} rate={args.rate}Hz "
        f"exit_on_disconnect={args.exit_on_disconnect} connect_timeout={args.connect_timeout}s",
        flush=True,
    )

    # Time we started looking for a joystick, used for --connect-timeout.
    waiting_since = time.monotonic()

    try:
        while True:
            if js is None:
                js = acquire_joystick(args.joystick_index)
                if js is None:
                    # Nothing plugged in (yet).
                    if args.connect_timeout > 0.0 and (time.monotonic() - waiting_since) >= args.connect_timeout:
                        print(
                            f"[joy_bridge_win] no joystick within {args.connect_timeout}s; exiting cleanly.",
                            flush=True,
                        )
                        send_zero_packet(sock, target, seq)
                        return 0
                    if time.time() - last_print > 2.0:
                        print("[joy_bridge_win] waiting for a joystick to be plugged in...", flush=True)
                        last_print = time.time()
                    time.sleep(0.5)
                    continue
                # Successfully acquired — reset the connect-timeout window.
                waiting_since = time.monotonic()
                js_name = js.get_name()
                print(f"[joy_bridge_win] joystick connected: {js_name} "
                      f"(axes={js.get_numaxes()}, buttons={js.get_numbuttons()})", flush=True)

            # Pump events so axis/button states refresh.
            try:
                pygame.event.pump()
            except pygame.error:
                # Joystick was likely yanked; the next get_axis call will raise.
                pass

            # If joystick became invalid (unplugged), send one zero packet and reacquire/exit.
            try:
                n_axes = js.get_numaxes()
                n_btns = js.get_numbuttons()
                axes = [float(js.get_axis(i)) for i in range(n_axes)]
                buttons = [int(js.get_button(i)) for i in range(n_btns)]
            except pygame.error:
                print("[joy_bridge_win] joystick disconnected; sending zero packet.", flush=True)
                send_zero_packet(sock, target, seq)
                seq += 1
                if args.exit_on_disconnect:
                    print("[joy_bridge_win] --exit-on-disconnect: exiting.", flush=True)
                    return 0
                js = None
                waiting_since = time.monotonic()
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
        send_zero_packet(sock, target, seq)
        try:
            sock.close()
        except OSError:
            pass
        try:
            lock.close()
        except OSError:
            pass
        pygame.quit()
        print("\n[joy_bridge_win] stopped.", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
