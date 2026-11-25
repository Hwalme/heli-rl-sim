# -*- coding: utf-8 -*-
"""
PPO 训练: FastHeloEnv 起飞-悬停任务
用法:
  python train_ppo.py                     # 默认 400k 步, 8 并行环境
  python train_ppo.py --steps 1000000     # 加大训练量
  python train_ppo.py --arm               # 用机械臂关节动作模式训练
输出:
  rl_train/results/helo_ppo.zip      训练好的策略
  rl_train/results/results.json      最终指标 (成功率/坠毁率/高度误差/耗时)
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helo_env import FastHeloEnv  # noqa: E402


def evaluate(model, n_episodes=20, action_mode="direct"):
    env = FastHeloEnv(action_mode=action_mode)
    succ, crash, errs = 0, 0, []
    for ep in range(n_episodes):
        obs, info = env.reset(seed=10_000 + ep)
        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, _, term, trunc, info = env.step(action)
            done = term or trunc
        succ += bool(info.get("success"))
        crash += bool(info.get("crashed"))
        errs.append(info.get("mean_alt_err", 99.0))
    env.close()
    return succ / n_episodes, crash / n_episodes, float(np.mean(errs))


class ProgressCallback:
    """简易进度: 每 progress_every 个 step 打印一次并做中期评估"""

    def __init__(self, model, total, progress_every=50_000, action_mode="direct"):
        from stable_baselines3.common.callbacks import BaseCallback

        outer = self

        class _CB(BaseCallback):
            def _on_step(self):
                if self.num_timesteps % progress_every < self.training_env.num_envs:
                    sr, cr, err = evaluate(outer.model, n_episodes=6,
                                           action_mode=outer.action_mode)
                    print(f"  [{self.num_timesteps:>8d}/{total}] 成功率={sr:.0%} "
                          f"坠毁率={cr:.0%} 平均高度误差={err:.2f}m", flush=True)
                return True

        self.cb = _CB()
        self.model = model
        self.action_mode = action_mode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=400_000)
    ap.add_argument("--envs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=None,
                    help="PPO + env 复现种子 (None=系统随机)")
    ap.add_argument("--arm", action="store_true", help="机械臂关节动作模式")
    ap.add_argument("--out", default=os.path.join("results", "helo_ppo"))
    args = ap.parse_args()
    action_mode = "arm" if args.arm else "direct"

    from stable_baselines3 import PPO
    from stable_baselines3.common.env_util import make_vec_env
    from stable_baselines3.common.monitor import Monitor

    print("=" * 64)
    print(f" PPO 训练 FastHeloEnv  mode={action_mode}  steps={args.steps}  "
          f"envs={args.envs}  seed={args.seed}")
    print("=" * 64)

    env = make_vec_env(FastHeloEnv, n_envs=args.envs,
                       env_kwargs={"action_mode": action_mode},
                       monitor_dir=None, wrapper_class=Monitor,
                       seed=args.seed)

    model = PPO(
        "MlpPolicy", env,
        n_steps=1024, batch_size=2048, n_epochs=10,
        gamma=0.99, gae_lambda=0.95,
        learning_rate=3e-4, ent_coef=0.01,  # 熵激励: 帮助逃出"不起飞"局部最优
        policy_kwargs={"net_arch": [128, 128]},
        device="cpu",   # 小型 MLP, CPU 反而比 GPU 调度快
        seed=args.seed,
        verbose=0,
    )

    pcb = ProgressCallback(model, args.steps, action_mode=action_mode)
    t0 = time.time()
    model.learn(total_timesteps=args.steps, callback=pcb.cb, progress_bar=False)
    train_sec = time.time() - t0

    sr, cr, err = evaluate(model, n_episodes=20, action_mode=action_mode)
    fps = args.steps / max(train_sec, 1e-6)
    print("\n" + "=" * 64)
    print(f" 训练完成: {train_sec:.0f}s ({fps:,.0f} steps/s)")
    print(f" 最终评估 (20 回合, 确定性策略):")
    print(f"   成功率       : {sr:.0%}")
    print(f"   坠毁率       : {cr:.0%}")
    print(f"   平均高度误差 : {err:.2f} m")
    print("=" * 64)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    model.save(args.out)
    with open(args.out + "_results.json", "w", encoding="utf-8") as f:
        json.dump({
            "mode": action_mode, "steps": args.steps, "train_seconds": round(train_sec, 1),
            "steps_per_second": round(fps), "success_rate": sr, "crash_rate": cr,
            "mean_alt_err_m": round(err, 2), "seed": args.seed,
        }, f, ensure_ascii=False, indent=2)
    print(f"模型已保存: {args.out}.zip")


if __name__ == "__main__":
    main()
