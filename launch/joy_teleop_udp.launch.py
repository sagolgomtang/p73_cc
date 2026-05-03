#!/usr/bin/env python3
"""
Joystick teleop where the dongle is plugged into a Windows machine and the
joystick state is delivered over UDP (Tailscale-friendly).

  Windows: joy_bridge_win.py (pygame)  -->  UDP packets  -->
  Linux:   joy_udp_receiver.py (--> /joy)  -->  p73_joy_teleop  -->  /p73/cmd_vel
                                                                     /p73/push_event

This launch is started by the systemd user/system service. The user does NOT
need to invoke this manually; existing simulation/realrobot launch commands
stay unchanged.
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
        description="YAML param file with p73_joy_teleop parameters.",
    )
    port_arg = DeclareLaunchArgument(
        "port",
        default_value="35731",
        description="UDP port to listen on for incoming joystick packets.",
    )
    bind_arg = DeclareLaunchArgument(
        "bind",
        default_value="0.0.0.0",
        description="Address to bind the UDP receiver to. 0.0.0.0 listens on all interfaces (incl. tailscale0).",
    )

    udp_recv = Node(
        package="p73_cc",
        executable="joy_udp_receiver.py",
        name="joy_udp_receiver",
        output="screen",
        parameters=[
            {
                "port": LaunchConfiguration("port"),
                "bind": LaunchConfiguration("bind"),
            }
        ],
    )

    teleop_node = Node(
        package="p73_cc",
        executable="p73_joy_teleop.py",
        name="p73_joy_teleop",
        output="screen",
        parameters=[LaunchConfiguration("config")],
    )

    return LaunchDescription([cfg_arg, port_arg, bind_arg, udp_recv, teleop_node])
