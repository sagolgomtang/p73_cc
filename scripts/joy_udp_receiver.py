#!/usr/bin/env python3
"""
UDP receiver that bridges Windows-side joystick state to ROS2 /joy.

Wire format (binary, little-endian) — versioned for forward compatibility:

  magic[4]    = b"P73J"
  version     = uint16 (current = 1)
  seq         = uint32 (monotonic; receiver detects drops, not strict)
  num_axes    = uint8
  num_buttons = uint8
  axes        = float32 * num_axes      (range [-1, 1])
  buttons     = uint8   * num_buttons   (0 / 1)

Total max packet: well under 1 KB. Sent at ~50 Hz from joy_bridge_win.py.

If no packet arrives for `timeout_s`, this node publishes a neutral /joy
message (all axes 0, all buttons 0) once, so downstream p73_joy_teleop
will go to zero and stop the robot.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy

from sensor_msgs.msg import Joy

MAGIC = b"P73J"
VERSION = 1
HEADER_FMT = "<4sHIBB"  # magic, version, seq, num_axes, num_buttons
HEADER_SIZE = struct.calcsize(HEADER_FMT)


class JoyUdpReceiver(Node):
    def __init__(self):
        super().__init__("joy_udp_receiver")
        self.declare_parameter("port", 35731)
        self.declare_parameter("bind", "0.0.0.0")
        self.declare_parameter("timeout_s", 0.5)
        self.declare_parameter("publish_rate_hz", 50.0)

        self.port = int(self.get_parameter("port").value)
        self.bind = str(self.get_parameter("bind").value)
        self.timeout_s = float(self.get_parameter("timeout_s").value)
        self.rate_hz = float(self.get_parameter("publish_rate_hz").value)

        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.pub = self.create_publisher(Joy, "/joy", qos)

        self._lock = threading.Lock()
        self._latest_axes: list[float] = []
        self._latest_buttons: list[int] = []
        self._latest_time: float = 0.0
        self._last_seq: int | None = None

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.bind, self.port))
        self._sock.settimeout(0.2)

        self._stop = threading.Event()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

        self.timer = self.create_timer(1.0 / self.rate_hz, self._on_timer)
        self.get_logger().info(
            f"joy_udp_receiver listening on {self.bind}:{self.port} (timeout={self.timeout_s}s)"
        )

    def _rx_loop(self):
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break

            if len(data) < HEADER_SIZE:
                continue
            try:
                magic, version, seq, num_axes, num_buttons = struct.unpack_from(HEADER_FMT, data, 0)
            except struct.error:
                continue
            if magic != MAGIC or version != VERSION:
                continue

            need = HEADER_SIZE + 4 * num_axes + 1 * num_buttons
            if len(data) < need:
                continue

            try:
                axes = list(struct.unpack_from(f"<{num_axes}f", data, HEADER_SIZE))
                buttons = list(struct.unpack_from(f"<{num_buttons}B", data, HEADER_SIZE + 4 * num_axes))
            except struct.error:
                continue

            with self._lock:
                self._latest_axes = axes
                self._latest_buttons = buttons
                self._latest_time = time.monotonic()
                if self._last_seq is not None and seq < self._last_seq:
                    # Sender restarted; just accept.
                    pass
                self._last_seq = seq

    def _on_timer(self):
        with self._lock:
            axes = list(self._latest_axes)
            buttons = list(self._latest_buttons)
            stamp = self._latest_time

        msg = Joy()
        msg.header.stamp = self.get_clock().now().to_msg()

        if stamp == 0.0 or (time.monotonic() - stamp) > self.timeout_s:
            # No fresh data: publish a neutral Joy so downstream goes to 0.
            n_axes = max(len(axes), 6)
            n_btns = max(len(buttons), 11)
            msg.axes = [0.0] * n_axes
            msg.buttons = [0] * n_btns
        else:
            msg.axes = [float(a) for a in axes]
            msg.buttons = [int(b) for b in buttons]

        self.pub.publish(msg)

    def destroy_node(self):
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass
        return super().destroy_node()


def main():
    rclpy.init()
    node = JoyUdpReceiver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
