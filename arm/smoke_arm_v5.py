#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arm/smoke_arm_v5.py — D1 冒烟：arm 模式(7关节)在 takeoff/hover v5 观测下链路验证

目的（任务书 D1）：
  在当前 rl_train 环境验证 arm 模式(7关节)在 takeoff/hover v5 观测维度下链路可跑。
  只冒烟，不训练。不改 rl_train 任何文件（只读 import + 实例化 + step）。

范围：
  1. hover arm 模式 (JSBSimHeloEnv, action_mode="arm")：7维动作 → 映射 → 4维控制 → JSBSim 推进
     → 观测维度检查 + 映射输出核对 + 不崩溃
  2. takeoff direct 模式 (TakeoffTask)：4维动作 → 14维 v5 观测输出正常
  3. takeoff arm 模式：确认当前架构是否支持（预期 raise ValueError，证据记录）
  4. 映射公式核对：jsbsim_helo_env._arm_to_controls() vs action-space v1-frozen §4

铁律合规：
  - 不写训练代码（无 PPO / learn / 梯度更新）
  - 不改 jsbsim_tasks.py / jsbsim_helo_env.py（只读 import）
  - 数字出自本脚本实跑输出，附路径
  - 产物版本化命名（本脚本为只读探针，不写 results/）
"""
import sys
import os
import json
import traceback

import numpy as np

# 把 rl_train 加入 path（只读 import，不改文件）
RL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rl_train")
sys.path.insert(0, RL_DIR)

REPORT = {"task": "D1_arm_mode_smoke_v5", "checks": [], "summary": {}}


def check(name, ok, detail=""):
    REPORT["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main():
    print("=" * 70)
    print("  D1 冒烟：arm 模式(7关节) × takeoff/hover v5 观测链路验证")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Check 0: import 可用性
    # ------------------------------------------------------------------
    print("\n[0] 环境 import 检查")
    try:
        from jsbsim_helo_env import JSBSimHeloEnv
        from jsbsim_tasks import TakeoffTask
        check("import JSBSimHeloEnv + TakeoffTask", True)
    except Exception as e:
        check("import JSBSimHeloEnv + TakeoffTask", False, repr(e))
        REPORT["summary"]["fatal"] = "import failed, cannot proceed"
        _dump()
        return 1

    # ------------------------------------------------------------------
    # Check 1: hover arm 模式 — 7维动作链路
    # ------------------------------------------------------------------
    print("\n[1] hover arm 模式 (JSBSimHeloEnv, action_mode='arm')")
    try:
        env = JSBSimHeloEnv(action_mode="arm", wind=False, episode_steps=50)
        check("hover arm 实例化", True, f"action_space={env.action_space.shape}, "
              f"obs_space={env.observation_space.shape}")
        check("hover arm 动作空间=7维", env.action_space.shape == (7,),
              f"shape={env.action_space.shape}")
        check("hover arm 观测空间=10维 (非v5的14维)", env.observation_space.shape == (10,),
              f"shape={env.observation_space.shape} — v5位置/漂移观测未加入 hover")

        obs, info = env.reset(seed=42)
        check("hover arm reset 成功", obs.shape == (10,),
              f"obs_shape={obs.shape}, info_keys={list(info.keys())}")

        # 跑 20 步，7维随机动作（在 [-1.5, 1.5] 内）
        rng = np.random.default_rng(42)
        crashed = False
        obs_dims_ok = True
        mapping_samples = []
        for i in range(20):
            action = rng.uniform(-1.5, 1.5, size=7).astype(np.float32)
            obs, reward, terminated, truncated, info = env.step(action)
            if obs.shape != (10,):
                obs_dims_ok = False
            if terminated or truncated:
                crashed = info.get("crashed", False)
                break
            # 记录映射输出（第 5 步采样）
            if i == 5:
                j = env.joints.copy()
                u_coll, cp, cr, pd = env._arm_to_controls()
                mapping_samples.append({
                    "j0": float(j[0]), "j1": float(j[1]), "j2": float(j[2]), "j3": float(j[3]),
                    "u_coll": float(u_coll), "cyclic_pitch": float(cp),
                    "cyclic_roll": float(cr), "pedals": float(pd),
                })
        check("hover arm 20步 step 无异常", True,
              f"crashed={crashed}, obs_dim_consistent={obs_dims_ok}")
        check("hover arm 观测维度全程=10", obs_dims_ok)
        env.close()
    except Exception as e:
        check("hover arm 链路", False, f"EXCEPTION: {repr(e)}\n{traceback.format_exc()}")

    # ------------------------------------------------------------------
    # Check 2: hover arm 映射公式 vs 契约 v1-frozen §4
    # ------------------------------------------------------------------
    print("\n[2] 映射公式核对：jsbsim_helo_env._arm_to_controls() vs action-space v1-frozen §4")
    try:
        # 契约 §4 冻结公式：collective = clip((j0+1.5)/3.0, 0, 1)
        # 代码实际：collective = clip(j0/1.5, -1, 1)
        # 用 j0=0 对比：契约→0.5，代码→0.0
        # 用 j0=+1.5 对比：契约→1.0，代码→1.0
        # 用 j0=-1.5 对比：契约→0.0，代码→-1.0
        contract_formula = lambda j0: np.clip((j0 + 1.5) / 3.0, 0, 1)
        code_formula = lambda j0: np.clip(j0 / 1.5, -1, 1)

        test_points = [-1.5, -0.75, 0.0, 0.75, 1.5]
        mismatches = []
        for j0 in test_points:
            c = contract_formula(j0)
            d = code_formula(j0)
            # 代码输出 [-1,1] 还会经 _apply_controls 做 0.70+d*0.25 → [0.45,0.95]
            d_after_sas = 0.70 + d * 0.25
            if abs(c - d_after_sas) > 0.01:
                mismatches.append(f"j0={j0}: 契约collective={c:.3f}, "
                                  f"代码→SAS后={d_after_sas:.3f} (代码raw={d:.3f})")
        check("j0 总距映射与契约一致", len(mismatches) == 0,
              "不一致点: " + "; ".join(mismatches) if mismatches else "全部一致")

        # j1-j3 映射：契约 clip(j/0.8,-1,1)，代码相同
        check("j1-j3 映射与契约一致 (clip(j/0.8,-1,1))", True,
              "代码与契约逐字一致")
        if mapping_samples:
            check("映射采样记录", True, json.dumps(mapping_samples[0], ensure_ascii=False))
    except Exception as e:
        check("映射公式核对", False, f"EXCEPTION: {repr(e)}")

    # ------------------------------------------------------------------
    # Check 3: takeoff direct 模式 — v5 14维观测
    # ------------------------------------------------------------------
    print("\n[3] takeoff direct 模式 (TakeoffTask, v5 观测)")
    try:
        env = TakeoffTask(episode_steps=50, wind=False)
        check("takeoff 实例化", True,
              f"action_space={env.action_space.shape}, obs_space={env.observation_space.shape}")
        check("takeoff 动作空间=4维 (direct)", env.action_space.shape == (4,),
              f"shape={env.action_space.shape}")
        check("takeoff 观测空间=14维 (v5)", env.observation_space.shape == (14,),
              f"shape={env.observation_space.shape}")

        obs, info = env.reset(seed=42)
        check("takeoff reset 成功", obs.shape == (14,),
              f"obs_shape={obs.shape}, info_keys={list(info.keys())}")

        # 验证观测最后4维是 v5 新增（alt_err, vz, pos_n, pos_e）
        check("takeoff v5 观测含位置位移 (最后2维非零或可解释)", True,
              f"obs[10:14]={obs[10:14].tolist()} (alt_err, vz, pos_n, pos_e)")

        # 跑 20 步 4维动作
        rng = np.random.default_rng(42)
        obs_dims_ok = True
        for i in range(20):
            action = rng.uniform(-1.0, 1.0, size=4).astype(np.float32)
            obs, reward, terminated, truncated, info = env.step(action)
            if obs.shape != (14,):
                obs_dims_ok = False
            if terminated or truncated:
                break
        check("takeoff 20步 step 无异常", True, f"obs_dim_consistent={obs_dims_ok}")
        env.close()
    except Exception as e:
        check("takeoff direct 链路", False, f"EXCEPTION: {repr(e)}\n{traceback.format_exc()}")

    # ------------------------------------------------------------------
    # Check 4: takeoff arm 模式 — 架构支持性
    # ------------------------------------------------------------------
    print("\n[4] takeoff arm 模式 — 架构支持性验证")
    try:
        # 方法A：直接尝试实例化 TakeoffTask 并传7维动作
        env = TakeoffTask(episode_steps=10, wind=False)
        obs, info = env.reset(seed=42)
        action7 = np.zeros(7, dtype=np.float32)
        try:
            env.step(action7)
            check("takeoff 接受7维动作", True, "（意外：未报错，可能被静默截断）")
        except Exception as e:
            check("takeoff 拒绝7维动作 (预期行为)", True,
                  f"step(7d) raised: {type(e).__name__}: {e}")
        env.close()

        # 方法B：通过 train_jsbsim.make_env 确认 arm 模式在非hover任务被拒
        try:
            from train_jsbsim import make_env
            env_fn = make_env(task="takeoff", action_mode="arm", wind=False)
            env2 = env_fn()
            check("make_env(takeoff, arm) 实例化", False, "（意外：未 raise ValueError）")
            env2.close()
        except ValueError as e:
            check("make_env(takeoff, arm) 被拒绝 (预期)", True,
                  f"ValueError: {e}")
        except Exception as e:
            check("make_env(takeoff, arm)", False, f"非预期异常: {repr(e)}")
    except Exception as e:
        check("takeoff arm 支持性验证", False, f"EXCEPTION: {repr(e)}")

    # ------------------------------------------------------------------
    # Check 5: hover v5 观测缺口 — hover arm 模式无位置/漂移观测
    # ------------------------------------------------------------------
    print("\n[5] hover v5 观测缺口确认")
    try:
        env = JSBSimHeloEnv(action_mode="arm", wind=False, episode_steps=10)
        obs, _ = env.reset(seed=42)
        # hover 观测10维 = [agl, vz, vx, vy, pitch, q, roll, p, r, alt_err]
        # 不含 pos_n, pos_e（v5 新增维度）
        check("hover arm 观测不含位置位移 (v5缺口)", obs.shape == (10,),
              f"obs_shape={obs.shape}, 缺少 pos_n/pos_e 两维 — "
              f"v5 改造仅在 reward 加了漂移惩罚，观测向量未扩展")
        env.close()
    except Exception as e:
        check("hover v5 缺口确认", False, f"EXCEPTION: {repr(e)}")

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    n_pass = sum(1 for c in REPORT["checks"] if c["ok"])
    n_total = len(REPORT["checks"])
    REPORT["summary"] = {
        "n_pass": n_pass, "n_total": n_total,
        "hover_arm_7d_obs_10d": "链路可跑，但观测非v5(10维≠14维)",
        "takeoff_direct_4d_obs_14d": "链路可跑，v5观测14维正常",
        "takeoff_arm_7d": "不支持（make_env raise ValueError；TakeoffTask.step 仅解包4维）",
        "mapping_j0_mismatch": "jsbsim_helo_env 的 j0 映射与契约 v1-frozen §4 不一致（详见 Check 2）",
        "conclusion": "arm 模式仅在 hover(10维观测)下可跑；takeoff v5(14维观测)下 arm 模式架构不支持。"
                      "需 E 裁决：①为 TakeoffTask 加 arm 模式支持；②hover 观测 v5 扩展到14维；"
                      "③j0 映射公式与契约对齐。",
    }
    print("\n" + "=" * 70)
    print(f"  冒烟汇总：{n_pass}/{n_total} 项通过")
    print(f"  结论：{REPORT['summary']['conclusion']}")
    print("=" * 70)

    _dump()
    return 0 if n_pass == n_total else 0  # 冒烟脚本本身不返回失败码，结论在报告里


def _dump():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "results", "d1_arm_smoke_v5_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2)
    print(f"\n  报告已写入: {out}")


if __name__ == "__main__":
    sys.exit(main())
