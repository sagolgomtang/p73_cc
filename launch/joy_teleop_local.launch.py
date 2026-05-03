#!/usr/bin/env python3
"""
Joystick teleop with the dongle plugged DIRECTLY into THIS Linux machine.

  /dev/input/jsN  -->  joy_node (/joy)  -->  p73_joy_teleop  -->  /p73/cmd_vel
                                                                   /p73/push_event

This is launched automatically by the systemd service installed via
`scripts/install_joy_autostart.sh`. The user does NOT need to invoke this
manually; the existing simulation/realrobot launch commands stay unchanged.
"""

from __future__ import annotations

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    pkg_share = get_package_share_directory("p73_cc")
    default_cfg = os.path.join(pkg_share, "config", "joy_8bitdo_ultimate2.yaml")

    cfg_arg = DeclareLaunchArgument(
        "config",
        default_value=default_cfg,
        description="YAML param file with joy_node + p73_joy_teleop parameters.",
    )
    dev_arg = DeclareLaunchArgument(
        "dev",
        default_value="/dev/input/p73_joystick",
        description="Joystick device path. Symlink created by the udev rule.",
    )

    joy_node = Node(
        package="joy",
        executable="joy_node",
        name="joy_node",
        output="screen",
        parameters=[
            LaunchConfiguration("config"),
            {"device_path": LaunchConfiguration("dev")},
        ],
    )

    teleop_node = Node(
        package="p73_cc",
        executable="p73_joy_teleop.py",
        name="p73_joy_teleop",
        output="screen",
        parameters=[LaunchConfiguration("config")],
    )

    return LaunchDescription([cfg_arg, dev_arg, joy_node, teleop_node])
