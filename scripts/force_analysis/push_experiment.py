#!/usr/bin/env python3
"""Push Experiment for Contact Force Analysis

MuJoCo 시뮬레이션이 실행 중일 때, /tmp/p73_push.txt를 통해
자동으로 push를 인가하고 결과를 기록한다.

사전 조건:
  - ros2 launch p73_controller simulation.launch.py 실행 중
  - mode 7 (RL 제어) 활성화 상태
  - walker-teleop으로 보행 명령 (0.3 m/s) 인가 중

Usage:
  # 단발 push (테스트)
  python3 push_experiment.py --single 60 +x

  # 자동 실험: 4방향 × 2강도 × 50회
  python3 push_experiment.py --auto

  # 커스텀 설정
  python3 push_experiment.py --auto --forces 30 60 90 --trials 20 --interval 8
"""

import argparse
import math
import random
import time
from pathlib import Path


PUSH_FILE = Path("/tmp/p73_push.txt")

DIRECTIONS = {
    "+x": (1, 0, 0),
    "-x": (-1, 0, 0),
    "+y": (0, 1, 0),
    "-y": (0, -1, 0),
}


def send_push(fx: float, fy: float, fz: float, duration: float = 1.0):
    """Push 명령을 /tmp/p73_push.txt에 쓴다."""
    PUSH_FILE.write_text(f"{fx} {fy} {fz} {duration}\n")


def wait_push_consumed(timeout: float = 2.0) -> bool:
    """main.cpp가 push 파일을 읽고 삭제할 때까지 대기."""
    t0 = time.time()
    while PUSH_FILE.exists():
        if time.time() - t0 > timeout:
            print("  WARNING: push file not consumed (MuJoCo running?)")
            PUSH_FILE.unlink(missing_ok=True)
            return False
        time.sleep(0.01)
    return True


def single_push(magnitude: float, direction: str, duration: float = 1.0):
    """단발 push."""
    if direction not in DIRECTIONS:
        print(f"ERROR: direction must be one of {list(DIRECTIONS.keys())}")
        return

    dx, dy, dz = DIRECTIONS[direction]
    fx, fy, fz = magnitude * dx, magnitude * dy, magnitude * dz

    print(f"Push: [{fx:.0f}, {fy:.0f}, {fz:.0f}] N, duration={duration}s")
    send_push(fx, fy, fz, duration)
    ok = wait_push_consumed()
    if ok:
        print(f"  → Sent. Waiting {duration + 0.5:.1f}s for push to complete...")
        time.sleep(duration + 0.5)
        print("  → Done.")


def auto_experiment(forces: list[int], directions: list[str],
                    trials: int, duration: float, interval: float):
    """자동 push 실험.

    모든 (force, direction) 조합에 대해 trials회 반복.
    각 push 사이에 interval초 대기 (보행 안정화).
    """
    combos = [(f, d) for f in forces for d in directions]
    total = len(combos) * trials

    print(f"{'='*60}")
    print(f"Push Experiment")
    print(f"  Forces:     {forces} N")
    print(f"  Directions: {directions}")
    print(f"  Trials:     {trials} per condition")
    print(f"  Duration:   {duration}s")
    print(f"  Interval:   {interval}s")
    print(f"  Total:      {total} pushes")
    print(f"  Est. time:  {total * (duration + interval) / 60:.1f} min")
    print(f"{'='*60}")

    input("\n로봇이 0.3 m/s로 보행 중인지 확인하고 Enter를 눌러 시작...")
    print()

    count = 0
    # 랜덤 순서로 섞기 (같은 조건이 연속되지 않도록)
    schedule = []
    for trial in range(trials):
        random.shuffle(combos)
        for force, direction in combos:
            schedule.append((force, direction, trial))

    for force, direction, trial in schedule:
        count += 1
        dx, dy, dz = DIRECTIONS[direction]
        fx, fy, fz = force * dx, force * dy, force * dz

        print(f"[{count}/{total}] {force}N {direction} (trial {trial+1}/{trials})")

        send_push(fx, fy, fz, duration)
        ok = wait_push_consumed()

        if not ok:
            print("  SKIPPED (MuJoCo not responding)")
            continue

        # Push duration + recovery
        time.sleep(duration + interval)

    print(f"\n{'='*60}")
    print(f"Experiment complete. {count} pushes applied.")
    print(f"CSV data is in: ~/ros2_ws/src/p73_cc/logs/")
    print(f"Run: python3 plot_log.py --csv <latest_mujoco_csv>")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Push Experiment for Contact Force Analysis")
    sub = parser.add_subparsers(dest="mode")

    # Single push
    sp = sub.add_parser("single", help="Single push test")
    sp.add_argument("magnitude", type=float, help="Force magnitude (N)")
    sp.add_argument("direction", choices=["+x", "-x", "+y", "-y"], help="Push direction")
    sp.add_argument("--duration", type=float, default=0.5, help="Push duration (s)")

    # Auto experiment
    ap = sub.add_parser("auto", help="Automated push experiment")
    ap.add_argument("--forces", type=int, nargs="+", default=[30, 60],
                    help="Push magnitudes (N)")
    ap.add_argument("--directions", nargs="+", default=["+x", "-x", "+y", "-y"],
                    help="Push directions")
    ap.add_argument("--trials", type=int, default=10, help="Trials per condition")
    ap.add_argument("--duration", type=float, default=0.5, help="Push duration (s)")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="Seconds between pushes (recovery time)")

    # Quick push (no subcommand)
    parser.add_argument("--quick", type=float, default=None,
                        help="Quick push: magnitude in +x direction")

    args = parser.parse_args()

    if args.quick is not None:
        single_push(args.quick, "+x")
    elif args.mode == "single":
        single_push(args.magnitude, args.direction, args.duration)
    elif args.mode == "auto":
        auto_experiment(args.forces, args.directions, args.trials,
                       args.duration, args.interval)
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python3 push_experiment.py single 60 +x")
        print("  python3 push_experiment.py auto")
        print("  python3 push_experiment.py auto --forces 30 60 90 --trials 20")
        print("  python3 push_experiment.py --quick 60")


if __name__ == "__main__":
    main()
