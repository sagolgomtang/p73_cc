#!/usr/bin/env python3
"""
Joystick teleop for P73 walker.

Subscribes:  /joy           (sensor_msgs/Joy)              from joy_node OR joy_udp_receiver
Publishes:   /p73/cmd_vel   (geometry_msgs/Twist)          consumed by cc.cpp
             /p73/push_event (std_msgs/Empty)              consumed by sim push handler

Default mapping (8BitDo Ultimate 2, XInput mode):
  Left stick X  -> -vy   (left = +vy)
  Left stick Y  ->  vx   (up   = +vx)
  Right stick X -> -wz   (left = +wz)
  A button       -> push_event (random push, sim only; ignored on real robot)
  Y button       -> reset cmd_vel to 0
  Start          -> emergency stop (publish 0 forever until released)
  LB (deadman)   -> if `require_deadman` is true, cmd_vel is only published when LB held

All mapping/scaling is loaded from a YAML param file so a different controller
can be supported without code changes.
"""

from __future__ import annotations

import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy

from sensor_msgs.msg import Joy
from geometry_msgs.msg import Twist
from std_msgs.msg import Empty


def _apply_deadzone(v: float, dz: float) -> float:
    if abs(v) < dz:
        return 0.0
    s = 1.0 if v > 0.0 else -1.0
    return s * (abs(v) - dz) / max(1.0 - dz, 1e-6)


def _expo(v: float, expo: float) -> float:
    if expo <= 0.0:
        return v
    return (1.0 - expo) * v + expo * (v * v * v)


class JoyTeleop(Node):
    def __init__(self):
        super().__init__("p73_joy_teleop")

        # Axes
        self.declare_parameter("axis_vx", 1)
        self.declare_parameter("axis_vy", 0)
        self.declare_parameter("axis_wz", 3)
        self.declare_parameter("invert_vx", False)
        self.declare_parameter("invert_vy", True)
        self.declare_parameter("invert_wz", True)

        # Buttons
        self.declare_parameter("button_push", 0)         # A
        self.declare_parameter("button_viz_toggle", 2)   # X — mujoco contact-force viz
        self.declare_parameter("button_reset", 3)        # Y
        self.declare_parameter("button_estop", 7)        # Start
        self.declare_parameter("button_deadman", 4)      # LB
        self.declare_parameter("button_scale_up", 5)     # RB
        self.declare_parameter("button_scale_down", 6)   # Back/Select

        # Scales / safety
        self.declare_parameter("max_vx", 1.0)
        self.declare_parameter("max_vy", 0.5)
        self.declare_parameter("max_wz", 1.0)
        self.declare_parameter("deadzone", 0.10)
        self.declare_parameter("expo", 0.30)
        self.declare_parameter("require_deadman", False)
        self.declare_parameter("scale_step", 0.10)
        self.declare_parameter("scale_min", 0.20)
        self.declare_parameter("scale_max", 1.50)
        self.declare_parameter("publish_rate_hz", 50.0)
        self.declare_parameter("joy_timeout_s", 0.5)
        self.declare_parameter("push_repeat_block_s", 0.30)

        gp = self.get_parameter
        self.axis_vx = int(gp("axis_vx").value)
        self.axis_vy = int(gp("axis_vy").value)
        self.axis_wz = int(gp("axis_wz").value)
        self.inv_vx = bool(gp("invert_vx").value)
        self.inv_vy = bool(gp("invert_vy").value)
        self.inv_wz = bool(gp("invert_wz").value)

        self.btn_push = int(gp("button_push").value)
        self.btn_viz_toggle = int(gp("button_viz_toggle").value)
        self.btn_reset = int(gp("button_reset").value)
        self.btn_estop = int(gp("button_estop").value)
        self.btn_deadman = int(gp("button_deadman").value)
        self.btn_scale_up = int(gp("button_scale_up").value)
        self.btn_scale_down = int(gp("button_scale_down").value)

        self.max_vx = float(gp("max_vx").value)
        self.max_vy = float(gp("max_vy").value)
        self.max_wz = float(gp("max_wz").value)
        self.deadzone = float(gp("deadzone").value)
        self.expo = float(gp("expo").value)
        self.require_deadman = bool(gp("require_deadman").value)
        self.scale_step = float(gp("scale_step").value)
        self.scale_min = float(gp("scale_min").value)
        self.scale_max = float(gp("scale_max").value)
        self.publish_rate_hz = float(gp("publish_rate_hz").value)
        self.joy_timeout_s = float(gp("joy_timeout_s").value)
        self.push_repeat_block_s = float(gp("push_repeat_block_s").value)

        self.scale = 1.0
        self.estop_active = False

        self._last_joy_time = 0.0
        self._last_push_time = 0.0
        self._last_btn_push = 0
        self._last_btn_viz_toggle = 0
        self._last_btn_reset = 0
        self._last_btn_estop = 0
        self._last_btn_scale_up = 0
        self._last_btn_scale_down = 0

        self._vx = 0.0
        self._vy = 0.0
        self._wz = 0.0
        self._deadman_held = not self.require_deadman

        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )

        self.cmd_pub = self.create_publisher(Twist, "/p73/cmd_vel", qos)
        self.push_pub = self.create_publisher(Empty, "/p73/push_event", qos)
        self.viz_toggle_pub = self.create_publisher(Empty, "/p73/viz_toggle", qos)

        self.sub = self.create_subscription(Joy, "/joy", self._on_joy, qos)

        self.timer = self.create_timer(1.0 / self.publish_rate_hz, self._on_timer)

        self.get_logger().info(
            f"p73_joy_teleop ready. max=({self.max_vx},{self.max_vy},{self.max_wz}) "
            f"deadzone={self.deadzone} expo={self.expo} require_deadman={self.require_deadman}"
        )

    def _on_joy(self, msg: Joy):
        self._last_joy_time = time.monotonic()

        def axis(i: int, default: float = 0.0) -> float:
            return float(msg.axes[i]) if 0 <= i < len(msg.axes) else default

        def btn(i: int, default: int = 0) -> int:
            return int(msg.buttons[i]) if 0 <= i < len(msg.buttons) else default

        ax_vx = axis(self.axis_vx) * (-1.0 if self.inv_vx else 1.0)
        ax_vy = axis(self.axis_vy) * (-1.0 if self.inv_vy else 1.0)
        ax_wz = axis(self.axis_wz) * (-1.0 if self.inv_wz else 1.0)

        ax_vx = _expo(_apply_deadzone(ax_vx, self.deadzone), self.expo)
        ax_vy = _expo(_apply_deadzone(ax_vy, self.deadzone), self.expo)
        ax_wz = _expo(_apply_deadzone(ax_wz, self.deadzone), self.expo)

        self._vx = ax_vx * self.max_vx * self.scale
        self._vy = ax_vy * self.max_vy * self.scale
        self._wz = ax_wz * self.max_wz * self.scale

        b_push = btn(self.btn_push)
        b_viz = btn(self.btn_viz_toggle)
        b_reset = btn(self.btn_reset)
        b_estop = btn(self.btn_estop)
        b_su = btn(self.btn_scale_up)
        b_sd = btn(self.btn_scale_down)
        b_dead = btn(self.btn_deadman, default=1)

        self._deadman_held = (b_dead == 1) or (not self.require_deadman)

        if b_push == 1 and self._last_btn_push == 0:
            now = time.monotonic()
            if now - self._last_push_time >= self.push_repeat_block_s:
                self.push_pub.publish(Empty())
                self._last_push_time = now
                self.get_logger().info("push_event sent")

        if b_viz == 1 and self._last_btn_viz_toggle == 0:
            self.viz_toggle_pub.publish(Empty())
            self.get_logger().info("viz_toggle sent (mujoco contact-force)")

        if b_reset == 1 and self._last_btn_reset == 0:
            self.scale = 1.0
            self.estop_active = False
            self.get_logger().info("reset (scale=1.0, estop cleared)")

        if b_estop == 1 and self._last_btn_estop == 0:
            self.estop_active = not self.estop_active
            self.get_logger().info(f"estop {'ENGAGED' if self.estop_active else 'released'}")

        if b_su == 1 and self._last_btn_scale_up == 0:
            self.scale = min(self.scale_max, self.scale + self.scale_step)
            self.get_logger().info(f"scale = {self.scale:.2f}")
        if b_sd == 1 and self._last_btn_scale_down == 0:
            self.scale = max(self.scale_min, self.scale - self.scale_step)
            self.get_logger().info(f"scale = {self.scale:.2f}")

        self._last_btn_push = b_push
        self._last_btn_viz_toggle = b_viz
        self._last_btn_reset = b_reset
        self._last_btn_estop = b_estop
        self._last_btn_scale_up = b_su
        self._last_btn_scale_down = b_sd

    def _on_timer(self):
        now = time.monotonic()
        joy_alive = (now - self._last_joy_time) <= self.joy_timeout_s and self._last_joy_time > 0.0

        if self.estop_active:
            # Estop must override anything else: actively flatten the command
            # to make sure no other publisher's value lingers in the bus.
            self.cmd_pub.publish(Twist())
            return

        if not joy_alive or not self._deadman_held:
            # Idle: nobody is actively driving. DO NOT publish a 0 every tick —
            # other publishers (GUI sliders, automated commands, etc.) might be
            # owning cmd_vel and we'd race with them. Stay silent.
            return

        msg = Twist()
        msg.linear.x = float(self._vx)
        msg.linear.y = float(self._vy)
        msg.angular.z = float(self._wz)
        self.cmd_pub.publish(msg)


def main():
    rclpy.init()
    node = JoyTeleop()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.cmd_pub.publish(Twist())
        except Exception:
            pass
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
