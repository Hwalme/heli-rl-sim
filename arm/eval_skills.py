#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arm/eval_skills.py — D4 原子技能库正式评测（统计口径）

目的
----
为验收指标 4"操控映射准确率 ≥90%"提供**统计测试口径**的证据
（此前仅有契约一致性自检口径，E 复验报告 §指标4 已注明口径需升级）。
对应 website/data-requests.md 工单 #3（K4，目标方 D+A）的 D 侧部分。

方法
----
- 覆盖：8 种原子技能 × 参数网格（含通道边界）× 5 个随机初始条件种子；
- 成功判定（与 skills.py 执行器一致）：
    settled     — 到位 = 目标容差带(±0.05 unit)内稳定保持 0.3 s
    duration_ok — 实际用时 ≤ 期望用时×1.5 + 0.5 s
  单回合成功 = 序列内全部关键帧 settled 且 duration_ok；
- 初始条件随机化：7 关节初始角在 ±0.25 unit 内均匀扰动（固定种子，可复现）；
- 执行链路：与技能库自检完全一致（契约主链路：软限位/速率钳位/L1 延迟生效）。

范围声明
--------
本评测为 **SIL 参考实现口径**（PyBullet kuka_iiwa）。高保真 JSBSim 端的
映射评测待轨道 A 完成集成后另行执行（K4 完整闭环需要 D+A 双侧）。

运行：python arm/eval_skills.py   （确定性种子，结果可复现）
输出：arm/results/arm_skill_eval_results.json
"""

import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pybullet as pb

from kuka_iiwa_model import KukaIiwaArm, N_JOINTS, SOFT_LIMIT, norm_to_q
from skills import (ALL_SKILLS, CenterChannel, HoldPose, PedalStep,
                    PressSwitch, PushPullCyclic, RaiseLowerCollective,
                    SidePushCyclic, TurnKnob)

SEED = 20260828
N_SEEDS = 5
INIT_JITTER = 0.25          # 初始关节角扰动幅值（unit）
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# 参数网格：每技能多个参数点（含通道线性有效区边界）
GRID = {
    "push_pull_cyclic":       [dict(amount=a) for a in (-0.8, -0.4, 0.4, 0.8)],
    "raise_lower_collective": [dict(amount=a) for a in (-1.5, -0.75, 0.75, 1.5)],
    "side_push_cyclic":       [dict(amount=a) for a in (-0.8, -0.3, 0.3, 0.8)],
    "pedal_step":             [dict(amount=a) for a in (-0.8, -0.4, 0.4, 0.8)],
    "center_channel":         [dict(channels="all")],
    "hold_pose":              [dict(duration=d) for d in (0.5, 2.0)],
    "press_switch":           [dict(depth=d) for d in (0.05, 0.3, 0.6)],
    "turn_knob":              [dict(angle_unit=a) for a in (-0.5, 0.5, 1.0)],
}


def make_skill(name, params):
    if name == "center_channel":
        return CenterChannel(**params)
    if name == "hold_pose":
        return HoldPose(**params)
    return ALL_SKILLS[name](**params)


def randomized_init(rng):
    """契约软限位内的随机初始关节角（小幅扰动，围绕中位）。"""
    j = rng.uniform(-INIT_JITTER, INIT_JITTER, size=N_JOINTS)
    return np.clip(j, -SOFT_LIMIT, SOFT_LIMIT)


def main():
    rng = np.random.default_rng(SEED)
    arm = KukaIiwaArm(control_dt=0.02)
    per_skill = {}
    n_ep_total, n_ok_total = 0, 0
    t0 = time.time()

    for name, param_list in GRID.items():
        rows = []
        for params in param_list:
            for seed_i in range(N_SEEDS):
                arm.reset()
                j_init = randomized_init(rng)
                # 同步设置契约层与 URDF 层初始关节角（构造随机初条件，两层一致）
                arm.joints = j_init.copy()
                q_init = norm_to_q(j_init, arm.q_lower, arm.q_upper)
                for k, jid in enumerate(arm.joint_ids):
                    pb.resetJointState(arm.body_id, jid, float(q_init[k]),
                                       physicsClientId=arm.client)
                skill = make_skill(name, params)
                from skills import SkillSequence
                rep = SkillSequence([skill], label=f"{name}:{seed_i}").run(arm)
                step = rep["steps"][0]
                ok = (step.get("settled") is True) and (step.get("duration_ok") is True)
                rows.append({"params": params, "seed": seed_i,
                             "settled": step.get("settled") is True,
                             "duration_ok": step.get("duration_ok") is True,
                             "status": step["status"], "success": ok})
                n_ep_total += 1
                n_ok_total += int(ok)
        n_ok = sum(r["success"] for r in rows)
        per_skill[name] = {
            "n_episodes": len(rows),
            "n_success": int(n_ok),
            "success_rate": round(n_ok / len(rows), 4),
            "param_points": len(param_list),
            "episodes": rows,
        }
        print(f"  {name:<24s} {n_ok}/{len(rows)}  ({per_skill[name]['success_rate']:.1%})")

    arm.close()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    result = {
        "task": "arm_skill_eval",
        "track": "D",
        "date": datetime.now().isoformat(timespec="seconds"),
        "metric": "操控映射准确率 ≥90%（ 指标4，K4）",
        "method": ("8 skills x param grid x 5 seeds; randomized init (|dj|<=0.25 unit, seed=20260828); "
                   "success = settled (tol 0.05 unit held 0.3 s) AND duration_ok (<=1.5x plan + 0.5 s); "
                   "executed via contract path (soft-limit, rate clamp 1.5 unit/s, L1 delay active)"),
        "scope": "SIL reference implementation (PyBullet kuka_iiwa, DIRECT). "
                 "JSBSim high-fidelity mapping eval pending track A integration.",
        "seed": SEED, "n_seeds": N_SEEDS,
        "n_configs": sum(len(v) for v in GRID.values()),
        "n_episodes": n_ep_total,
        "overall_success_rate": round(n_ok_total / n_ep_total, 4),
        "target": 0.90,
        "target_pass": (n_ok_total / n_ep_total) >= 0.90,
        "wall_time_s": round(time.time() - t0, 1),
        "code": ["arm/kuka_iiwa_model.py", "arm/skills.py", "arm/eval_skills.py"],
        "repro": "python arm/eval_skills.py",
        "per_skill": per_skill,
    }
    out = os.path.join(RESULTS_DIR, "arm_skill_eval_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("-" * 62)
    print(f"  总回合 {n_ep_total}，成功 {n_ok_total}，成功率 {result['overall_success_rate']:.1%}"
          f"（目标 ≥90%：{'PASS' if result['target_pass'] else 'FAIL'}）")
    print(f"  结果已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
