# -*- coding: utf-8 -*-
"""
JSBSim AH-1S 全数字仿真演示 + 可视化
加载训练好的 PPO 策略，运行起飞→悬停演示，生成：
  1. 3D 飞行轨迹图
  2. 高度/垂直速度曲线
  3. 姿态角曲线
  4. 控制输入曲线
  5. 综合仪表盘
"""
import sys
import os
import json
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.gridspec import GridSpec

# 把 rl_train 加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jsbsim_helo_env import JSBSimHeloEnv, DT, FT2M, FPS2MPS
from stable_baselines3 import PPO

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_output")
os.makedirs(RESULTS_DIR, exist_ok=True)

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "jsbsim_ppo_direct.zip")


def run_demo(seed=42, n_episodes=1):
    """运行演示，返回记录数据"""
    env = JSBSimHeloEnv(action_mode="direct", wind=True, target_range=(15.0, 25.0))
    model = PPO.load(MODEL_PATH, env=env)

    all_records = []
    for ep in range(n_episodes):
        obs, info = env.reset(seed=seed + ep)
        records = []
        total_reward = 0.0
        done = False
        step = 0

        while not done:
            action, _states = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            done = terminated or truncated

            # 记录原始状态
            s = env._get_raw_state()
            records.append({
                "step": step,
                "t": step * DT,
                "agl_m": s["agl"],
                "vz_mps": s["vz"],
                "vx_mps": s["vx"],
                "vy_mps": s["vy"],
                "pitch_deg": math.degrees(s["pitch"]),
                "roll_deg": math.degrees(s["roll"]),
                "roll_rate_dps": math.degrees(s["roll_rate"]),
                "pitch_rate_dps": math.degrees(s["pitch_rate"]),
                "yaw_rate_dps": math.degrees(s["yaw_rate"]),
                "action_coll": float(action[0]),
                "action_pitch": float(action[1]),
                "action_roll": float(action[2]),
                "action_yaw": float(action[3]),
                "reward": float(reward),
                "target_alt_m": env.target_alt,
                "crashed": env.crashed,
            })
            step += 1

        success = info.get("success", False)
        mean_alt_err = info.get("mean_alt_err", 0.0)
        all_records.append({
            "seed": seed + ep,
            "success": success,
            "crashed": env.crashed,
            "total_reward": total_reward,
            "mean_alt_err_m": mean_alt_err,
            "final_alt_m": records[-1]["agl_m"] if records else 0,
            "n_steps": len(records),
            "records": records,
        })
        print(f"  Episode {ep+1}/{n_episodes}: seed={seed+ep}, success={success}, "
              f"crash={env.crashed}, reward={total_reward:.1f}, "
              f"mean_alt_err={mean_alt_err:.2f}m, steps={len(records)}")

    env.close()
    return all_records


def plot_3d_trajectory(records, save_path):
    """3D 飞行轨迹图"""
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')

    for i, ep in enumerate(records):
        rec = ep["records"]
        t = np.array([r["t"] for r in rec])
        # 用 vx, vy 积分估算水平位置（简化）
        x = np.cumsum([r["vx_mps"] * DT for r in rec])
        y = np.cumsum([r["vy_mps"] * DT for r in rec])
        z = np.array([r["agl_m"] for r in rec])

        # 按高度着色
        scatter = ax.scatter(x, y, z, c=z, cmap='viridis', s=2, alpha=0.7, label=f'Ep{i+1}')
        ax.plot([x[0]], [y[0]], [z[0]], 'go', markersize=8, label='Start')
        ax.plot([x[-1]], [y[-1]], [z[-1]], 'ro', markersize=8, label='End')

    ax.set_xlabel('X (m)', fontsize=11)
    ax.set_ylabel('Y (m)', fontsize=11)
    ax.set_zlabel('Altitude AGL (m)', fontsize=11)
    ax.set_title('JSBSim AH-1S Flight Trajectory (PPO Policy)', fontsize=13, fontweight='bold')
    ax.legend(loc='upper left')
    plt.colorbar(scatter, ax=ax, label='Altitude (m)', shrink=0.6)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {save_path}")


def plot_altitude_and_vs(records, save_path):
    """高度和垂直速度曲线"""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    for i, ep in enumerate(records):
        rec = ep["records"]
        t = [r["t"] for r in rec]
        alt = [r["agl_m"] for r in rec]
        target = [r["target_alt_m"] for r in rec]
        vz = [r["vz_mps"] for r in rec]

        ax1.plot(t, alt, label=f'Ep{i+1} Altitude', linewidth=1.5)
        ax1.plot(t, target, '--', label=f'Ep{i+1} Target', alpha=0.7, linewidth=1)
        ax2.plot(t, vz, label=f'Ep{i+1} Vz', linewidth=1.5)

    ax1.set_ylabel('Altitude AGL (m)', fontsize=11)
    ax1.set_title('Altitude Tracking', fontsize=12, fontweight='bold')
    ax1.legend(loc='lower right')
    ax1.grid(True, alpha=0.3)
    ax1.axhline(y=0, color='k', linestyle='-', alpha=0.2)

    ax2.set_xlabel('Time (s)', fontsize=11)
    ax2.set_ylabel('Vertical Speed (m/s)', fontsize=11)
    ax2.set_title('Vertical Speed', fontsize=12, fontweight='bold')
    ax2.legend(loc='upper right')
    ax2.grid(True, alpha=0.3)
    ax2.axhline(y=0, color='k', linestyle='-', alpha=0.2)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {save_path}")


def plot_attitude(records, save_path):
    """姿态角曲线"""
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 9), sharex=True)

    for i, ep in enumerate(records):
        rec = ep["records"]
        t = [r["t"] for r in rec]
        roll = [r["roll_deg"] for r in rec]
        pitch = [r["pitch_deg"] for r in rec]
        roll_rate = [r["roll_rate_dps"] for r in rec]
        pitch_rate = [r["pitch_rate_dps"] for r in rec]
        yaw_rate = [r["yaw_rate_dps"] for r in rec]

        ax1.plot(t, roll, label=f'Ep{i+1} Roll', linewidth=1.2)
        ax2.plot(t, pitch, label=f'Ep{i+1} Pitch', linewidth=1.2, color='C1')
        ax3.plot(t, roll_rate, label=f'Ep{i+1} Roll rate', linewidth=1.0, alpha=0.8)
        ax3.plot(t, pitch_rate, label=f'Ep{i+1} Pitch rate', linewidth=1.0, alpha=0.8)
        ax3.plot(t, yaw_rate, label=f'Ep{i+1} Yaw rate', linewidth=1.0, alpha=0.8)

    ax1.set_ylabel('Roll (deg)', fontsize=11)
    ax1.set_title('Roll Angle', fontsize=12, fontweight='bold')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.3)
    ax1.axhline(y=0, color='k', linestyle='-', alpha=0.2)
    ax1.set_ylim([-30, 30])

    ax2.set_ylabel('Pitch (deg)', fontsize=11)
    ax2.set_title('Pitch Angle', fontsize=12, fontweight='bold')
    ax2.legend(loc='upper right')
    ax2.grid(True, alpha=0.3)
    ax2.axhline(y=0, color='k', linestyle='-', alpha=0.2)
    ax2.set_ylim([-20, 20])

    ax3.set_xlabel('Time (s)', fontsize=11)
    ax3.set_ylabel('Angular Rate (deg/s)', fontsize=11)
    ax3.set_title('Angular Rates (SAS Inner Loop)', fontsize=12, fontweight='bold')
    ax3.legend(loc='upper right', fontsize=8)
    ax3.grid(True, alpha=0.3)
    ax3.axhline(y=0, color='k', linestyle='-', alpha=0.2)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {save_path}")


def plot_controls(records, save_path):
    """控制输入曲线"""
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    labels = [
        ("Collective (normalized)", "action_coll"),
        ("Cyclic Pitch", "action_pitch"),
        ("Cyclic Roll", "action_roll"),
        ("Tail Rotor / Yaw", "action_yaw"),
    ]

    for idx, (title, key) in enumerate(labels):
        ax = axes[idx // 2][idx % 2]
        for i, ep in enumerate(records):
            rec = ep["records"]
            t = [r["t"] for r in rec]
            val = [r[key] for r in rec]
            ax.plot(t, val, label=f'Ep{i+1}', linewidth=1.2)
        ax.set_title(title, fontsize=11, fontweight='bold')
        ax.set_xlabel('Time (s)', fontsize=10)
        ax.set_ylabel('Action', fontsize=10)
        ax.legend(loc='upper right')
        ax.grid(True, alpha=0.3)
        ax.axhline(y=0, color='k', linestyle='-', alpha=0.2)

    plt.suptitle('PPO Policy Control Inputs (JSBSim AH-1S)', fontsize=13, fontweight='bold')
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {save_path}")


def plot_dashboard(records, save_path):
    """综合仪表盘"""
    fig = plt.figure(figsize=(16, 10))
    gs = GridSpec(3, 3, figure=fig, hspace=0.35, wspace=0.3)

    ep = records[0]  # 用第一回合
    rec = ep["records"]
    t = [r["t"] for r in rec]

    # 1. 高度
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(t, [r["agl_m"] for r in rec], 'b-', linewidth=1.5)
    ax1.plot(t, [r["target_alt_m"] for r in rec], 'r--', linewidth=1, label='Target')
    ax1.set_title('Altitude (m)', fontweight='bold')
    ax1.set_ylabel('m')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # 2. 垂直速度
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(t, [r["vz_mps"] for r in rec], 'g-', linewidth=1.5)
    ax2.set_title('Vertical Speed (m/s)', fontweight='bold')
    ax2.set_ylabel('m/s')
    ax2.grid(True, alpha=0.3)
    ax2.axhline(y=0, color='k', linestyle='-', alpha=0.2)

    # 3. 奖励累积
    ax3 = fig.add_subplot(gs[0, 2])
    cum_reward = np.cumsum([r["reward"] for r in rec])
    ax3.plot(t, cum_reward, 'm-', linewidth=1.5)
    ax3.set_title('Cumulative Reward', fontweight='bold')
    ax3.set_ylabel('Reward')
    ax3.grid(True, alpha=0.3)

    # 4. 滚转
    ax4 = fig.add_subplot(gs[1, 0])
    ax4.plot(t, [r["roll_deg"] for r in rec], 'b-', linewidth=1.2)
    ax4.set_title('Roll (deg)', fontweight='bold')
    ax4.set_ylabel('deg')
    ax4.grid(True, alpha=0.3)
    ax4.set_ylim([-25, 25])

    # 5. 俯仰
    ax5 = fig.add_subplot(gs[1, 1])
    ax5.plot(t, [r["pitch_deg"] for r in rec], 'r-', linewidth=1.2)
    ax5.set_title('Pitch (deg)', fontweight='bold')
    ax5.set_ylabel('deg')
    ax5.grid(True, alpha=0.3)
    ax5.set_ylim([-15, 15])

    # 6. 总距
    ax6 = fig.add_subplot(gs[1, 2])
    coll_actual = [0.70 + r["action_coll"] * 0.25 for r in rec]
    ax6.plot(t, coll_actual, 'k-', linewidth=1.2)
    ax6.set_title('Collective (actual)', fontweight='bold')
    ax6.set_ylabel('norm')
    ax6.grid(True, alpha=0.3)

    # 7. 水平速度
    ax7 = fig.add_subplot(gs[2, 0])
    ax7.plot(t, [r["vx_mps"] for r in rec], 'b-', label='Vx', linewidth=1.0)
    ax7.plot(t, [r["vy_mps"] for r in rec], 'r-', label='Vy', linewidth=1.0)
    ax7.set_title('Horizontal Velocity (m/s)', fontweight='bold')
    ax7.set_xlabel('Time (s)')
    ax7.set_ylabel('m/s')
    ax7.legend()
    ax7.grid(True, alpha=0.3)

    # 8. 角速度
    ax8 = fig.add_subplot(gs[2, 1])
    ax8.plot(t, [r["roll_rate_dps"] for r in rec], 'b-', label='p', linewidth=0.8, alpha=0.8)
    ax8.plot(t, [r["pitch_rate_dps"] for r in rec], 'r-', label='q', linewidth=0.8, alpha=0.8)
    ax8.plot(t, [r["yaw_rate_dps"] for r in rec], 'g-', label='r', linewidth=0.8, alpha=0.8)
    ax8.set_title('Angular Rates (deg/s)', fontweight='bold')
    ax8.set_xlabel('Time (s)')
    ax8.set_ylabel('deg/s')
    ax8.legend()
    ax8.grid(True, alpha=0.3)

    # 9. 状态文字
    ax9 = fig.add_subplot(gs[2, 2])
    ax9.axis('off')
    status_text = (
        f"JSBSim AH-1S Demo Dashboard\n"
        f"{'='*30}\n"
        f"Seed: {ep['seed']}\n"
        f"Success: {'YES' if ep['success'] else 'NO'}\n"
        f"Crashed: {'YES' if ep['crashed'] else 'NO'}\n"
        f"Total Reward: {ep['total_reward']:.1f}\n"
        f"Mean Alt Err: {ep['mean_alt_err_m']:.2f} m\n"
        f"Final Alt: {ep['final_alt_m']:.1f} m\n"
        f"Steps: {ep['n_steps']}\n"
        f"Duration: {ep['n_steps']*DT:.1f} s\n"
        f"\nPhysics: 6-DoF + BET rotor\n"
        f"Control: 50 Hz + SAS inner loop\n"
        f"Policy: PPO (direct mode)"
    )
    ax9.text(0.05, 0.95, status_text, transform=ax9.transAxes,
             fontsize=10, verticalalignment='top', fontfamily='monospace',
             bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    plt.suptitle(' Low-Altitude Embodied AI · Full Digital Simulation Demo',
                 fontsize=14, fontweight='bold', y=0.98)
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {save_path}")


def main():
    print("=" * 64)
    print("  JSBSim AH-1S 全数字仿真演示 + 可视化")
    print("=" * 64)

    # 运行 3 个不同种子的演示
    print("\n[1/3] Running demo episodes...")
    records = run_demo(seed=42, n_episodes=3)

    # 保存原始数据（转换 numpy 类型为 Python 原生类型）
    def to_native(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    data_path = os.path.join(RESULTS_DIR, "demo_data.json")
    with open(data_path, 'w', encoding='utf-8') as f:
        summary = []
        for ep in records:
            item = {k: to_native(v) for k, v in ep.items() if k != 'records'}
            summary.append(item)
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  Data saved: {data_path}")

    # 生成图表
    print("\n[2/3] Generating plots...")
    plot_3d_trajectory(records, os.path.join(RESULTS_DIR, "01_trajectory_3d.png"))
    plot_altitude_and_vs(records, os.path.join(RESULTS_DIR, "02_altitude_vs.png"))
    plot_attitude(records, os.path.join(RESULTS_DIR, "03_attitude.png"))
    plot_controls(records, os.path.join(RESULTS_DIR, "04_controls.png"))
    plot_dashboard(records, os.path.join(RESULTS_DIR, "05_dashboard.png"))

    # 汇总
    print("\n[3/3] Summary:")
    n_success = sum(1 for ep in records if ep["success"])
    print(f"  Episodes: {len(records)}, Success: {n_success}/{len(records)}")
    print(f"  Mean alt error: {np.mean([ep['mean_alt_err_m'] for ep in records]):.2f} m")
    print(f"  Mean reward: {np.mean([ep['total_reward'] for ep in records]):.1f}")
    print(f"\n  All outputs saved to: {RESULTS_DIR}")
    print("=" * 64)


if __name__ == "__main__":
    main()
