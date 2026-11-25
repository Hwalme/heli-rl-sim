# -*- coding: utf-8 -*-
"""
eval_robustness.py: A4 鲁棒性评估 — 不重训, 纯评估现有 PPO 模型在扰动矩阵下的表现

设计:
  - 复用 train_jsbsim.evaluate_policy 的评估骨架 (deterministic policy)
  - 扰动矩阵注入: 风强度、质量摄动、控制延迟 (L0/L1/L2)
  - 输入: 5 个 PPO zip + 评估配置 (种数, 每种回合数)
  - 输出: results/robustness_<task>.json (每任务全扰动结果)
          results/robustness_summary.json (跨任务汇总)

不动 jsbsim_tasks.py 与 jsbsim_helo_env.py (现役任务族), 仅做评测。
"""
import os
import sys
import time
import json
import argparse
import numpy as np
from typing import Dict, Any

# UTF-8 on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from jsbsim_tasks import TASK_CLASSES
from jsbsim_helo_env import JSBSimHeloEnv

TASK_MODELS = {
    # 任务名: (model_path, 构造环境的 task 类, 默认参数)
    "hover":    ("jsbsim_ppo_direct.zip", "hover", {}),
    "forward":  ("jsbsim_ppo_forward.zip", "forward", {}),
    "landing":  ("jsbsim_ppo_landing.zip", "landing", {}),
    "roll":     ("jsbsim_ppo_roll.zip", "roll", {}),
    "yaw":      ("jsbsim_ppo_yaw.zip", "yaw", {}),
    "waypoint": ("jsbsim_ppo_waypoint.zip", "waypoint", {}),
}


# ============ 扰动矩阵 ============
# 设计原则: 训练时域随机化参数为 wind ±6 fps, mass ±5%, delay L0 (无延迟).
# 评估矩阵向外推 3 个等级 (弱/中/强), 评估模型在"超出训练域"时的退化曲线.

WIND_LEVELS = {
    "calm":     {"wind_max_fps": 0.0,  "gust": False},        # 静风
    "nominal":  {"wind_max_fps": 6.0,  "gust": False},        # 训练域内
    "strong":   {"wind_max_fps": 15.0, "gust": False},        # 超出训练域
    "gust":     {"wind_max_fps": 10.0, "gust": True},         # 阵风
}

MASS_LEVELS = {
    "nominal":  {"mass_perturb_pct": 0.05},
    "heavy15":  {"mass_perturb_pct": 0.15},
    "heavy25":  {"mass_perturb_pct": 0.25},
}

DELAY_LEVELS = {
    "L0": {"hold_steps": 0},                       # 训练时配置
    "L1": {"hold_steps": 1},                       # D 契约 v1 默认
    "L2": {"hold_steps": 2},                       # 严重延迟
}


def make_env_with_disturbances(task_name: str, wind_cfg: Dict[str, Any],
                                mass_cfg: Dict[str, Any],
                                delay_cfg: Dict[str, Any], seed: int):
    """构造带扰动的环境工厂."""
    def _init():
        if task_name == "hover":
            env = JSBSimHeloEnv(action_mode="direct", wind=True)
        else:
            env = TASK_CLASSES[task_name]()

        # ---- 风扰动 ----
        env.wind_enabled = True
        env._wind_max_fps = wind_cfg["wind_max_fps"]
        env._wind_gust = wind_cfg.get("gust", False)
        # wind 注入在 _reset_jsbsim 中按 _wind_max_fps 采样
        # 修补: 重置时覆盖采样范围
        orig_reset = env._reset_jsbsim
        def patched_reset():
            orig_reset()
            # 强制按当前 wind_cfg 重置风
            try:
                wx = float(env.rng.uniform(-env._wind_max_fps, env._wind_max_fps))
                wy = float(env.rng.uniform(-env._wind_max_fps, env._wind_max_fps))
                env.fdm.set_property_value("atmosphere/wind-north-fps", wy)
                env.fdm.set_property_value("atmosphere/wind-east-fps", wx)
                if env._wind_gust:
                    # 阵风: 周期 5s 的 sin 叠加, 幅值 8 fps
                    t = env.steps * 0.02
                    gust_n = 8.0 * np.sin(2 * np.pi * t / 5.0)
                    gust_e = 8.0 * np.cos(2 * np.pi * t / 5.0)
                    env.fdm.set_property_value("atmosphere/wind-north-fps", wy + gust_n)
                    env.fdm.set_property_value("atmosphere/wind-east-fps", wx + gust_e)
            except Exception:
                pass
        env._reset_jsbsim = patched_reset

        # ---- 质量摄动 ----
        env._mass_perturb_pct = mass_cfg["mass_perturb_pct"]
        orig_init = env.__init__
        def patched_init():
            orig_init()
        # 修补 reset 中的质量摄动: 在 fly_to_hover 之前缩放 coll 中心
        # 简化: 直接改写 _fly_to_hover 中心 coll (从 0.70 改为 0.70 + 0.10 * mass_perturb)
        if hasattr(env, '_fly_to_hover'):
            fly = env._fly_to_hover
            def patched_fly(target_alt_m, settle_s=3.0, max_s=35.0):
                # 质量偏移用抬高悬停 coll 中心近似
                coll_extra = 0.10 * (env._mass_perturb_pct - 0.05) / 0.05
                orig_coll_func = env._pd_step
                def patched_pd_step(coll):
                    return orig_coll_func(min(1.0, coll + coll_extra))
                env._pd_step = patched_pd_step
                result = fly(target_alt_m, settle_s, max_s)
                env._pd_step = orig_coll_func
                return result
            env._fly_to_hover = patched_fly

        # ---- 控制延迟 ----
        env._delay_hold_steps = delay_cfg["hold_steps"]
        # 修补 step: 对动作先 hold_steps 次零阶保持
        orig_step = env.step
        if env._delay_hold_steps > 0:
            def delayed_step(action):
                # 内部存 _last_action, _hold_count
                if not hasattr(env, '_hold_count'):
                    env._hold_count = 0
                    env._last_action = np.zeros_like(np.asarray(action, dtype=np.float64))
                if env._hold_count <= 0:
                    env._last_action = np.clip(np.asarray(action, dtype=np.float64), -1.0, 1.0)
                    env._hold_count = env._delay_hold_steps
                env._hold_count -= 1
                return orig_step(env._last_action)
            env.step = delayed_step

        return env
    return _init


def evaluate(model, env_fn, n_episodes: int, seed: int) -> Dict[str, float]:
    """确定性策略评估 (复用 train_jsbsim.evaluate_policy 字段)."""
    env = env_fn()
    successes, crashes, errs, lengths = [], [], [], []
    for ep in range(n_episodes):
        obs, info = env.reset(seed=seed + ep * 100)
        done = False
        step = 0
        last_info = info
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, term, trunc, info = env.step(action)
            done = term or trunc
            step += 1
            last_info = info
        success = float(bool(last_info.get("success", False)))
        crashed = float(bool(last_info.get("crashed", False)))
        mean_err = float(last_info.get(
            "mean_alt_err", last_info.get("mean_err",
            abs(last_info.get("agl", 0) - last_info.get("target_alt", 0)))))
        successes.append(success)
        crashes.append(crashed)
        errs.append(mean_err)
        lengths.append(step)
    return {
        "success_rate": float(np.mean(successes)),
        "crash_rate": float(np.mean(crashes)),
        "mean_err": float(np.mean(errs)),
        "mean_ep_length": float(np.mean(lengths)),
        "episodes": n_episodes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="hover,forward,landing,roll,yaw,waypoint",
                    help="逗号分隔任务名")
    ap.add_argument("--wind", default="nominal,strong,gust",
                    help="风等级逗号分隔")
    ap.add_argument("--mass", default="nominal,heavy15",
                    help="质量等级逗号分隔")
    ap.add_argument("--delay", default="L0,L1",
                    help="延迟等级逗号分隔")
    ap.add_argument("--seeds", default="42",
                    help="种子列表")
    ap.add_argument("--episodes", type=int, default=20)
    ap.add_argument("--out", default=os.path.join("results", "robustness_summary.json"))
    args = ap.parse_args()

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    winds = [w.strip() for w in args.wind.split(",") if w.strip()]
    masses = [m.strip() for m in args.mass.split(",") if m.strip()]
    delays = [d.strip() for d in args.delay.split(",") if d.strip()]
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]

    print("=" * 70)
    print(f" A4 鲁棒性评估")
    print(f" 任务: {tasks}")
    print(f" 扰动: 风={winds}, 质量={masses}, 延迟={delays}")
    print(f" 评估: {len(seeds)} 种子 × {args.episodes} 回合")
    print("=" * 70)

    summary = {"conditions": len(winds) * len(masses) * len(delays),
               "tasks": {}}

    results_dir = os.path.join(script_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    for task_name in tasks:
        if task_name not in TASK_MODELS:
            print(f"  [skip] 未知任务 {task_name}")
            continue
        model_file, _, _ = TASK_MODELS[task_name]
        model_path = os.path.join(results_dir, model_file)
        if not os.path.exists(model_path):
            print(f"  [skip] 模型缺失 {model_path}")
            continue
        print(f"\n[Task: {task_name}] 模型 {model_path}")
        model = PPO.load(model_path)

        task_results = {"model_path": model_path, "conditions": {}}
        for w_label in winds:
            for m_label in masses:
                for d_label in delays:
                    cond_label = f"w_{w_label}__m_{m_label}__d_{d_label}"
                    print(f"  评估 {cond_label}...", end=" ", flush=True)
                    env_fn = make_env_with_disturbances(
                        task_name,
                        WIND_LEVELS[w_label],
                        MASS_LEVELS[m_label],
                        DELAY_LEVELS[d_label],
                        seed=seeds[0])
                    seed_results = []
                    for s in seeds:
                        res = evaluate(model, env_fn, args.episodes, s)
                        seed_results.append(res)
                    sr = float(np.mean([r["success_rate"] for r in seed_results]))
                    cr = float(np.mean([r["crash_rate"] for r in seed_results]))
                    er = float(np.mean([r["mean_err"] for r in seed_results]))
                    task_results["conditions"][cond_label] = {
                        "wind": w_label, "mass": m_label, "delay": d_label,
                        "success_rate": sr, "crash_rate": cr, "mean_err": er,
                        "seed_results": seed_results,
                    }
                    print(f"succ={sr:.0%} crash={cr:.0%} err={er:.2f}")
        # 写单任务文件
        task_path = os.path.join(results_dir, f"robustness_{task_name}.json")
        with open(task_path, "w", encoding="utf-8") as f:
            json.dump(task_results, f, ensure_ascii=False, indent=2)
        summary["tasks"][task_name] = {
            "model_path": model_path,
            "n_conditions": len(task_results["conditions"]),
            "results_file": task_path,
        }

    with open(os.path.join(script_dir, args.out), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n[Done] 汇总: {os.path.join(script_dir, args.out)}")


if __name__ == "__main__":
    main()