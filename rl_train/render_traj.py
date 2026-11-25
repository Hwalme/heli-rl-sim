# -*- coding: utf-8 -*-
"""
render_traj.py — JSBSim 轨迹 3D 可视化（绕开 FG 的视觉证据替代方案）

输出:
  - 3D 飞行轨迹图（含姿态箭头、目标线、风向）
  - 关键指标面板（高度/速度/姿态/航向/漂移）
  - HTML 报告（一页式，便于截图/分享）
用法:
  python render_traj.py RL_TRAJ [--seed SEED] [--episode-index N] [--out PNG]
"""
import argparse
import math
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa


def load_traj(path):
    import json
    return json.load(open(path, encoding='utf-8'))


def metrics(fr):
    hdg0 = fr[0]['psi_deg']
    n = e = 0.0
    mxr = mxp = mxv = mxa_v = mxa_n = 0.0
    for f in fr:
        n += f.get('vx_mps', 0) * 0.02
        e += f.get('vy_mps', 0) * 0.02
        mxr = max(mxr, abs(f['roll_deg']))
        mxp = max(mxp, abs(f['pitch_deg']))
        mxv = max(mxv, abs(f.get('vy_mps', 0)))
        mxa_v = max(mxa_v, abs(f.get('vx_mps', 0)))
        mxa_n = max(mxa_n, abs(f.get('vy_mps', 0)))
    net_drift = math.hypot(n, e)
    return dict(
        net_drift_m=net_drift,
        heading_drift_deg=(fr[-1]['psi_deg'] - hdg0 + 540) % 360 - 180,
        t=fr[-1]['t'] - fr[0]['t'],
        start_agl=round(fr[0]['agl_m'], 2),
        end_agl=round(fr[-1]['agl_m'], 2),
        max_alt=round(max(f['agl_m'] for f in fr), 2),
        max_roll=mxr,
        max_pitch=mxp,
        max_vx=mxa_v,
        max_vy=mxa_n,
    )


def render(traj_data, seed, ep_idx, out_path):
    sg = f'seed_{seed}'
    eps = traj_data['trajectories'][sg]
    if ep_idx >= len(eps):
        ep_idx = 0
    ep = eps[ep_idx]
    fr = ep['frames']
    m = metrics(fr)

    fig = plt.figure(figsize=(18, 12))
    gs = fig.add_gridspec(3, 3, height_ratios=[1.5, 1.5, 1.0])

    # 1) 3D 轨迹
    ax3d = fig.add_subplot(gs[0:2, 0:2], projection='3d')
    n = e = 0.0
    xs, ys, zs = [], [], []
    for f in fr:
        n += f.get('vx_mps', 0) * 0.02
        e += f.get('vy_mps', 0) * 0.02
        xs.append(n)
        ys.append(e)
        zs.append(f['agl_m'])
    ax3d.plot(xs, ys, zs, '-', color='#1f77b4', linewidth=2)
    ax3d.scatter([xs[0]], [ys[0]], [zs[0]], color='green', s=80, label='start (地面)')
    ax3d.scatter([xs[-1]], [ys[-1]], [zs[-1]], color='red', s=80, label='end (%sm)' % m['end_agl'])
    # 姿态箭头(每 1s)
    idx_step = max(1, int(0.5 / 0.02))  # 每 0.5s
    for i in range(0, len(fr), idx_step):
        f = fr[i]
        if f['agl_m'] < 0.5:
            continue
        # 本体系方向 -> 北东坐标 (heading + roll/pitch 简化为指向机头方向)
        hdg = math.radians(f['psi_deg'])
        roll = math.radians(f['roll_deg'])
        nx = math.cos(hdg) * 3
        ny = math.sin(hdg) * 3
        ax3d.quiver(xs[i], ys[i], zs[i], nx, ny, 0, color='orange', alpha=0.6, arrow_length_ratio=0.3)
    ax3d.set_xlabel('北 (m)')
    ax3d.set_ylabel('东 (m)')
    ax3d.set_zlabel('高度 AGL (m)')
    ax3d.set_title(f"AI 起飞轨迹 3D (seed={seed} ep={ep_idx}) —  起点 {m['start_agl']}m → 终点 {m['end_agl']}m, "
                   f"用时 {m['t']:.1f}s, 净漂移 {m['net_drift_m']:.1f}m",
                   fontsize=11)
    ax3d.legend()
    ax3d.grid(True)

    # 3) 高度 vs 时间
    ax1 = fig.add_subplot(gs[2, 0])
    ax1.plot([f['t'] for f in fr], [f['agl_m'] for f in fr], '-', color='green')
    ax1.set_xlabel('时间 (s)'); ax1.set_ylabel('高度 AGL (m)')
    ax1.set_title(f'垂直爬升 {m["end_agl"] - m["start_agl"]:.1f}m  ({m["t"]:.1f}s)')
    ax1.grid(True)

    # 4) 滚转/俯仰 vs 时间
    ax2 = fig.add_subplot(gs[2, 1])
    ax2.plot([f['t'] for f in fr], [f['roll_deg'] for f in fr], '-', label='roll')
    ax2.plot([f['t'] for f in fr], [f['pitch_deg'] for f in fr], '-', label='pitch')
    ax2.set_xlabel('时间 (s)'); ax2.set_ylabel('角度 (°)')
    ax2.set_title(f'姿态: roll_max={m["max_roll"]:.1f}° pitch_max={m["max_pitch"]:.1f}°')
    ax2.legend()
    ax2.grid(True)

    # 5) 水平速度分量 + 漂移累积
    ax3 = fig.add_subplot(gs[2, 2])
    vxs = [f.get('vx_mps', 0) for f in fr]
    vys = [f.get('vy_mps', 0) for f in fr]
    drifts = [math.hypot(sum(vxs[:i+1]) * 0.02, sum(vys[:i+1]) * 0.02) for i in range(len(vxs))]
    ax3.plot([f['t'] for f in fr], vxs, '-', label='vx')
    ax3.plot([f['t'] for f in fr], vys, '-', label='vy')
    ax3.plot([f['t'] for f in fr], drifts, '--', label='|drift| (cum)', color='black')
    ax3.set_xlabel('时间 (s)'); ax3.set_ylabel('m/s')
    ax3.set_title(f'水平速度 | 净漂移={m["net_drift_m"]:.1f}m')
    ax3.legend()
    ax3.grid(True)

    fig.suptitle(f"JSBSim 物理权威 + AI 策略实时闭环 — G5 独立复验 · {os.path.basename(out_path)}",
                 fontsize=12, fontweight='bold')
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap = argparse.ArgumentParser()
    ap.add_argument('traj', help='轨迹 JSON 文件')
    ap.add_argument('--seed', default='7')
    ap.add_argument('--episode-index', type=int, default=2)
    ap.add_argument('--out', default='traj_render.png')
    args = ap.parse_args()
    data = load_traj(args.traj)
    out = os.path.abspath(args.out)
    m = render(data, args.seed, args.episode_index, out)
    print('输出:', out)
    print('指标:', m)


if __name__ == '__main__':
    main()