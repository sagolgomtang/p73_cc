#!/usr/bin/env python3
"""Contact Force Comparison — 기존 CSV 로그 기반 분석

기존 ros2 launch로 수집한 logs/ 의 두 종류 CSV를 결합하여 분석:
  1. mujoco_*.csv  — cc.cpp가 로깅 (182 cols: IMU, joint, torque, obs, action)
  2. contact_force_*.csv — main.cpp가 로깅 (7 cols: time + L/R foot Fx,Fy,Fz)

두 CSV는 time 컬럼으로 자동 매칭(nearest merge)된다.
contact_force CSV가 없으면 joint torque proxy로 fallback.

Usage:
  # 기본: mujoco CSV 지정 → 같은 디렉토리에서 contact_force CSV 자동 매칭
  python3 analyze_results.py \\
    --csv logs/mujoco_cf_est.csv logs/mujoco_no_est.csv \\
    --labels "CF-Est (Ours)" "No-Est"

  # contact_force CSV 수동 지정
  python3 analyze_results.py \\
    --csv logs/mujoco_A.csv logs/mujoco_B.csv \\
    --force_csv logs/contact_force_A.csv logs/contact_force_B.csv \\
    --labels "A" "B"

  # 시간 구간 지정
  python3 analyze_results.py \\
    --csv logs/a.csv logs/b.csv \\
    --labels "A" "B" \\
    --t_start 5.0 --t_end 20.0
"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# --- Paper-quality style ---
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman"],
    "font.size": 10,
    "axes.labelsize": 10,
    "axes.titlesize": 11,
    "legend.fontsize": 8,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})

DEFAULT_COLORS = ["#2ca02c", "#ff7f0e", "#d62728", "#1f77b4", "#9467bd"]


# ==========================================================
# CSV Loading & Validation
# ==========================================================

def find_matching_force_csv(mujoco_csv_path: str) -> str | None:
    """mujoco_YYMMDD_HHMMSS.csv → contact_force_YYMMDD_HHMMSS.csv 자동 매칭

    같은 디렉토리에서 타임스탬프가 가장 가까운 contact_force CSV를 찾는다.
    (ros2 launch 시 main.cpp과 cc.cpp이 거의 동시에 시작하므로 ±2초 이내 매칭)
    """
    path = Path(mujoco_csv_path)
    log_dir = path.parent

    # 타임스탬프 추출: mujoco_260516_034304.csv → "260516_034304"
    name = path.stem  # "mujoco_260516_034304"
    parts = name.split("_", 1)
    if len(parts) < 2:
        return None
    mujoco_ts = parts[1]  # "260516_034304"

    # 같은 디렉토리에서 contact_force_*.csv 찾기
    candidates = sorted(log_dir.glob("contact_force_*.csv"))
    if not candidates:
        return None

    # 정확히 같은 타임스탬프 먼저 찾기
    for c in candidates:
        c_ts = c.stem.replace("contact_force_", "")
        if c_ts == mujoco_ts:
            return str(c)

    # 없으면 가장 가까운 것 (YYMMDD_HHMMSS 문자열 비교)
    # 동일 날짜(YYMMDD)인 것 중 시간 차이 최소
    mujoco_date = mujoco_ts[:6]
    same_day = [c for c in candidates if c.stem.replace("contact_force_", "")[:6] == mujoco_date]
    if same_day:
        # 시간 차이 최소
        best = min(same_day, key=lambda c: abs(
            int(c.stem.replace("contact_force_", "")[7:]) - int(mujoco_ts[7:])
        ))
        return str(best)

    return None


def load_csv(path: str, force_csv: str = None,
             t_start: float = None, t_end: float = None) -> pd.DataFrame:
    """mujoco CSV 로드 + contact_force CSV 자동 매칭 merge + 시간 필터링"""
    df = pd.read_csv(path)
    if "time" not in df.columns:
        print(f"ERROR: 'time' column not found in {path}")
        sys.exit(1)

    # contact_force CSV 매칭
    if force_csv is None:
        force_csv = find_matching_force_csv(path)

    if force_csv and Path(force_csv).exists():
        df_force = pd.read_csv(force_csv)
        if "time" in df_force.columns and len(df_force) > 0:
            # 두 CSV의 time overlap 구간 찾기
            t_overlap_start = max(df["time"].iloc[0], df_force["time"].iloc[0])
            t_overlap_end = min(df["time"].iloc[-1], df_force["time"].iloc[-1])

            # overlap 구간으로 양쪽 trim
            df = df[(df["time"] >= t_overlap_start) & (df["time"] <= t_overlap_end)]
            df_force = df_force[(df_force["time"] >= t_overlap_start) & (df_force["time"] <= t_overlap_end)]

            # time 기준 nearest merge
            df = pd.merge_asof(
                df.sort_values("time"),
                df_force.sort_values("time"),
                on="time",
                direction="nearest",
                tolerance=0.002  # 2ms 이내 매칭
            )
            print(f"    ✅ Contact force merged: {Path(force_csv).name} "
                  f"(overlap: {t_overlap_start:.2f}~{t_overlap_end:.2f}s)")

    # 시간 0 기준으로 정규화
    df["time"] = df["time"] - df["time"].iloc[0]

    if t_start is not None:
        df = df[df["time"] >= t_start]
    if t_end is not None:
        df = df[df["time"] <= t_end]

    return df.reset_index(drop=True)


def detect_force_columns(df: pd.DataFrame) -> dict:
    """CSV에 어떤 contact force 컬럼이 있는지 감지"""
    cols = df.columns.tolist()

    # main.cpp에서 로깅한 contact force (merge 후)
    if "foot_force_lz" in cols and "foot_force_rz" in cols:
        return {
            "type": "direct",
            "fz_left": "foot_force_lz",
            "fz_right": "foot_force_rz",
        }

    # 기존 cc.cpp CSV만 — contact force 없음 → joint torque proxy
    if "tau_joint_0" in cols:
        return {
            "type": "torque_proxy",
            "note": "Contact force CSV 미발견 — joint torque proxy 사용",
        }

    return {"type": "none"}


def compute_total_fz(df: pd.DataFrame, force_info: dict) -> np.ndarray:
    """Total vertical contact force 계산"""
    if force_info["type"] == "direct":
        fz_l = df[force_info["fz_left"]].values
        fz_r = df[force_info["fz_right"]].values
        return np.abs(fz_l) + np.abs(fz_r)

    elif force_info["type"] == "torque_proxy":
        # Knee + AnklePitch 토크의 절대합을 proxy로 사용
        # L_Knee=3, L_AnklePitch=4, R_Knee=9, R_AnklePitch=10
        proxy = np.zeros(len(df))
        for idx in [3, 4, 9, 10]:
            col = f"tau_joint_{idx}"
            if col in df.columns:
                proxy += np.abs(df[col].values)
        return proxy

    return np.zeros(len(df))


# ==========================================================
# Analysis Functions
# ==========================================================

def compute_step_peaks(fz: np.ndarray, dt: float, min_peak: float = 50.0) -> list[float]:
    """매 step의 peak F_z 검출 (local maxima)"""
    peaks = []
    for i in range(1, len(fz) - 1):
        if fz[i] > fz[i-1] and fz[i] > fz[i+1] and fz[i] > min_peak:
            peaks.append(fz[i])
    return peaks


def compute_statistics(fz: np.ndarray, dt: float) -> dict:
    """Contact force 통계"""
    peaks = compute_step_peaks(fz, dt)
    return {
        "mean_fz": np.nanmean(fz),
        "max_fz": np.nanmax(fz),
        "std_fz": np.nanstd(fz),
        "mean_peak": np.nanmean(peaks) if peaks else 0,
        "max_peak": np.nanmax(peaks) if peaks else 0,
        "std_peak": np.nanstd(peaks) if peaks else 0,
        "n_peaks": len(peaks),
        "p95_fz": np.nanpercentile(fz, 95),
        "impulse": np.trapezoid(fz, dx=dt),
    }


# ==========================================================
# Figure Generation
# ==========================================================

def plot_fz_timeseries(datasets: list[tuple[pd.DataFrame, np.ndarray, str, str]],
                       output_dir: Path):
    """Contact force 시계열 비교 (Fig. 4 스타일)"""
    fig, axes = plt.subplots(2, 1, figsize=(10, 5), sharex=True)

    # 상단: Total F_z
    for df, fz, label, color in datasets:
        t = df["time"].values
        axes[0].plot(t, fz, color=color, linewidth=0.8, label=label, alpha=0.85)

    axes[0].set_ylabel("Total $F_z$ (N)")
    axes[0].legend(loc="upper right")
    axes[0].grid(True, alpha=0.3)
    axes[0].set_ylim(bottom=0)

    # 하단: Base velocity X
    for df, fz, label, color in datasets:
        t = df["time"].values
        if "lin_vel_wx" in df.columns:
            vx = df["lin_vel_wx"].values
        elif "cmd_vx" in df.columns:
            vx = df["cmd_vx"].values
        else:
            continue
        axes[1].plot(t, vx, color=color, linewidth=0.8, label=label, alpha=0.85)

    axes[1].set_ylabel("Base Vel X (m/s)")
    axes[1].set_xlabel("Time (s)")
    axes[1].legend(loc="upper right")
    axes[1].grid(True, alpha=0.3)

    out_path = output_dir / "08_fz_timeseries.pdf"
    fig.savefig(out_path)
    fig.savefig(output_dir / "08_fz_timeseries.png")
    plt.close(fig)
    print(f"  Saved: {out_path}")


def plot_fz_distribution(datasets: list[tuple[pd.DataFrame, np.ndarray, str, str]],
                         output_dir: Path):
    """Contact force 분포 비교 (histogram)"""
    fig, ax = plt.subplots(figsize=(8, 4))

    for df, fz, label, color in datasets:
        p95 = np.percentile(fz, 95)
        ax.hist(fz, bins=100, alpha=0.45, color=color, density=True,
                histtype="stepfilled", linewidth=1.2, edgecolor=color,
                label=f"{label} (p95={p95:.0f}N)")

    ax.set_xlabel("Total $F_z$ (N)")
    ax.set_ylabel("Density")
    ax.legend()
    ax.grid(True, alpha=0.3)

    out_path = output_dir / "09_fz_distribution.pdf"
    fig.savefig(out_path)
    fig.savefig(output_dir / "09_fz_distribution.png")
    plt.close(fig)
    print(f"  Saved: {out_path}")


def plot_peak_comparison(datasets: list[tuple[pd.DataFrame, np.ndarray, str, str]],
                         output_dir: Path):
    """Step-wise peak F_z 비교 (box plot)"""
    fig, ax = plt.subplots(figsize=(6, 4))

    all_peaks = []
    labels = []
    colors = []

    for df, fz, label, color in datasets:
        dt = df["time"].diff().median()
        peaks = compute_step_peaks(fz, dt)
        if peaks:
            all_peaks.append(peaks)
            labels.append(label)
            colors.append(color)

    if not all_peaks:
        print("  No peaks detected, skipping box plot")
        plt.close(fig)
        return

    bp = ax.boxplot(all_peaks, labels=labels, patch_artist=True, widths=0.5)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)

    ax.set_ylabel("Peak $F_z$ per step (N)")
    ax.grid(axis="y", alpha=0.3)

    out_path = output_dir / "10_peak_fz_boxplot.pdf"
    fig.savefig(out_path)
    fig.savefig(output_dir / "10_peak_fz_boxplot.png")
    plt.close(fig)
    print(f"  Saved: {out_path}")


def plot_torque_comparison(datasets: list[tuple[pd.DataFrame, np.ndarray, str, str]],
                           output_dir: Path):
    """관절 토크 RMS 비교 (joint torque가 있을 때)"""
    joint_labels = [
        "L_HipRoll", "L_HipPitch", "L_HipYaw", "L_Knee", "L_AnklePitch", "L_AnkleRoll",
        "R_HipRoll", "R_HipPitch", "R_HipYaw", "R_Knee", "R_AnklePitch", "R_AnkleRoll",
    ]

    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(12)
    width = 0.8 / len(datasets)

    for i, (df, fz, label, color) in enumerate(datasets):
        rms_vals = []
        for j in range(12):
            col = f"tau_joint_{j}"
            if col in df.columns:
                rms_vals.append(np.sqrt(np.nanmean(df[col].values**2)))
            else:
                rms_vals.append(0)

        offset = (i - len(datasets)/2 + 0.5) * width
        ax.bar(x + offset, rms_vals, width, label=label, color=color, alpha=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels(joint_labels, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("Joint Torque RMS (Nm)")
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    out_path = output_dir / "11_torque_rms_comparison.pdf"
    fig.savefig(out_path)
    fig.savefig(output_dir / "11_torque_rms_comparison.png")
    plt.close(fig)
    print(f"  Saved: {out_path}")


# ==========================================================
# Summary Table
# ==========================================================

def print_summary(datasets: list[tuple[pd.DataFrame, np.ndarray, str, str]]):
    """통계 요약 출력"""
    print("\n" + "="*70)
    print("Contact Force Comparison Summary")
    print("="*70)

    rows = []
    for df, fz, label, color in datasets:
        dt = df["time"].diff().median()
        stats = compute_statistics(fz, dt)
        stats["label"] = label
        stats["duration"] = df["time"].iloc[-1] - df["time"].iloc[0]
        rows.append(stats)

    # Table
    header = f"{'Policy':<20} {'Mean Fz':>8} {'Max Fz':>8} {'P95 Fz':>8} {'Mean Peak':>10} {'Max Peak':>10} {'N Peaks':>8}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['label']:<20} {r['mean_fz']:>8.1f} {r['max_fz']:>8.1f} {r['p95_fz']:>8.1f} "
              f"{r['mean_peak']:>10.1f} {r['max_peak']:>10.1f} {r['n_peaks']:>8d}")

    # Improvement (마지막 정책 대비)
    if len(rows) >= 2:
        ours = rows[0]
        print(f"\n--- Improvement vs others ---")
        for r in rows[1:]:
            if r["mean_peak"] > 0:
                reduction = (1 - ours["mean_peak"] / r["mean_peak"]) * 100
                print(f"  vs {r['label']}: Mean Peak Fz {reduction:+.1f}%")


# ==========================================================
# Main
# ==========================================================

def main():
    parser = argparse.ArgumentParser(
        description="Contact Force Comparison from existing CSV logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 analyze_results.py --csv logs/a.csv logs/b.csv --labels "Ours" "Baseline"
  python3 analyze_results.py --csv logs/a.csv logs/b.csv --labels "A" "B" --t_start 5 --t_end 30
        """
    )
    parser.add_argument("--csv", nargs="+", required=True, help="mujoco CSV file paths (first = ours)")
    parser.add_argument("--labels", nargs="+", required=True, help="Labels for each CSV")
    parser.add_argument("--force_csv", nargs="+", default=None,
                        help="contact_force CSV paths (수동 지정, 없으면 자동 매칭)")
    parser.add_argument("--colors", nargs="+", default=None, help="Colors (hex)")
    parser.add_argument("--t_start", type=float, default=None, help="Analysis start time (s)")
    parser.add_argument("--t_end", type=float, default=None, help="Analysis end time (s)")
    parser.add_argument("--output", default=None, help="Output directory")
    args = parser.parse_args()

    if len(args.csv) != len(args.labels):
        print("ERROR: --csv and --labels must have same length")
        sys.exit(1)

    colors = args.colors or DEFAULT_COLORS[:len(args.csv)]

    # Output directory — plot_log.py와 동일 구조로 정렬
    if args.output:
        output_dir = Path(args.output)
    elif len(args.csv) == 1:
        # 단일 CSV → logs/plot/<csv_stem>/ (plot_log.py와 같은 디렉토리에 추가)
        csv_stem = Path(args.csv[0]).stem  # e.g., "mujoco_260517_193149"
        output_dir = Path(args.csv[0]).parent / "plot" / csv_stem
    else:
        # 비교 → logs/plot/compare_<label1>_vs_<label2>/
        safe_labels = [l.replace(" ", "_").replace("(", "").replace(")", "") for l in args.labels]
        compare_name = "compare_" + "_vs_".join(safe_labels)
        output_dir = Path(args.csv[0]).parent / "plot" / compare_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Prepare force_csv list
    force_csvs = args.force_csv or [None] * len(args.csv)
    if len(force_csvs) < len(args.csv):
        force_csvs += [None] * (len(args.csv) - len(force_csvs))

    # Load data
    print("Loading CSVs...")
    datasets = []
    for csv_path, fc_path, label, color in zip(args.csv, force_csvs, args.labels, colors):
        print(f"  {label}: {csv_path}")
        df = load_csv(csv_path, force_csv=fc_path, t_start=args.t_start, t_end=args.t_end)
        force_info = detect_force_columns(df)
        print(f"    → {len(df)} rows, {df['time'].iloc[-1]:.1f}s, force: {force_info['type']}")

        if force_info["type"] == "torque_proxy":
            print(f"    ⚠️ {force_info['note']}")

        fz = compute_total_fz(df, force_info)
        datasets.append((df, fz, label, color))

    # Summary
    print_summary(datasets)

    # Figures
    print(f"\nGenerating figures → {output_dir}/")

    plot_fz_timeseries(datasets, output_dir)
    plot_fz_distribution(datasets, output_dir)
    plot_peak_comparison(datasets, output_dir)
    plot_torque_comparison(datasets, output_dir)

    print(f"\nDone. All figures saved to: {output_dir}/")


if __name__ == "__main__":
    main()
