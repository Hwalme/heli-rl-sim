# -*- coding: utf-8 -*-
"""
train_ppo_evidence.py — 带"证据链"打点的 PPO 训练入口
======================================================

与 rl_train/train_ppo.py 相同的训练逻辑, 差别在于:
  1. 训练开始即写入 evidence/live/current_run.json (网站"训练实况"数据源);
  2. 每次中期评估更新 live + 记录成功率曲线点;
  3. 训练结束把"训练运行 + 最终评估"两条证据追加进哈希链, 模型文件以
     SHA-256 固证; 异常退出会记录 incident 证据。
  4. 结果文件按 run 独立保存, 不覆盖历史: results/run_<id>/…

用法:
  python train_ppo_evidence.py --steps 400000
  python train_ppo_evidence.py --steps 1000000 --arm
"""
import argparse
import json
import os
import sys
import time
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "rl_train"))

from evidence_logger import EvidenceLogger  # noqa: E402
from helo_env import FastHeloEnv  # noqa: E402


def evaluate(model, n_episodes=20, action_mode="direct"):
    import numpy as np
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=400_000)
    ap.add_argument("--envs", type=int, default=8)
    ap.add_argument("--arm", action="store_true", help="机械臂关节动作模式")
    ap.add_argument("--tag", default="", help="本次运行标签 (进入证据标题)")
    args = ap.parse_args()
    action_mode = "arm" if args.arm else "direct"

    ev = EvidenceLogger(actor="train_ppo_evidence.py")
    title = f"PPO 训练 {action_mode} 模式" + (f" [{args.tag}]" if args.tag else "")
    run_id = ev.begin_run(title, config={
        "steps": args.steps, "envs": args.envs, "mode": action_mode,
        "algorithm": "PPO", "tag": args.tag,
    })
    out_dir = os.path.join(BASE, "rl_train", "results", f"run_{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    print("=" * 64)
    print(f" [{run_id}] {title}  steps={args.steps} envs={args.envs}")
    print(f" 证据实时状态: evidence/live/current_run.json")
    print("=" * 64)

    t0 = time.time()
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.env_util import make_vec_env
        from stable_baselines3.common.monitor import Monitor
        from stable_baselines3.common.callbacks import BaseCallback

        env = make_vec_env(FastHeloEnv, n_envs=args.envs,
                           env_kwargs={"action_mode": action_mode},
                           wrapper_class=Monitor)
        model = PPO("MlpPolicy", env, n_steps=1024, batch_size=2048, n_epochs=10,
                    gamma=0.99, gae_lambda=0.95, learning_rate=3e-4, ent_coef=0.01,
                    policy_kwargs={"net_arch": [128, 128]}, device="cpu", verbose=0)

        outer = None  # (保留占位, 回调通过闭包访问 args/action_mode)

        class ProgressCB(BaseCallback):
            def _on_step(self):
                if self.num_timesteps % 50_000 < args.envs:
                    sr, cr, err = evaluate(self.model, n_episodes=6,
                                           action_mode=action_mode)
                    sec = time.time() - t0
                    sps = self.num_timesteps / max(sec, 1e-6)
                    print(f"  [{self.num_timesteps:>8d}/{args.steps}] "
                          f"成功率={sr:.0%} 坠毁率={cr:.0%} 误差={err:.2f}m "
                          f"({sps:,.0f} steps/s)", flush=True)
                    ev.update_live(
                        steps=self.num_timesteps, stage="training",
                        metrics={"success_rate": round(sr, 3),
                                 "crash_rate": round(cr, 3),
                                 "mean_alt_err_m": round(err, 2),
                                 "steps_per_second": round(sps)},
                        history_point={"steps": self.num_timesteps,
                                       "success_rate": round(sr, 3),
                                       "crash_rate": round(cr, 3),
                                       "mean_alt_err_m": round(err, 2)})
                return True

        ev.update_live(stage="training")
        model.learn(total_timesteps=args.steps, callback=ProgressCB(),
                    progress_bar=False)
        train_sec = time.time() - t0

        ev.update_live(stage="evaluating",
                       metrics={"success_rate": None})
        sr, cr, err = evaluate(model, n_episodes=20, action_mode=action_mode)
        fps = args.steps / max(train_sec, 1e-6)

        model_path = os.path.join(out_dir, "helo_ppo.zip")
        results_path = os.path.join(out_dir, "results.json")
        model.save(model_path)
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "mode": action_mode, "steps": args.steps,
                       "train_seconds": round(train_sec, 1),
                       "steps_per_second": round(fps),
                       "success_rate": sr, "crash_rate": cr,
                       "mean_alt_err_m": round(err, 2)},
                      f, ensure_ascii=False, indent=2)

        print("\n" + "=" * 64)
        print(f" 训练完成: {train_sec:.0f}s ({fps:,.0f} steps/s)")
        print(f" 最终评估: 成功率={sr:.0%} 坠毁率={cr:.0%} 误差={err:.2f}m")
        print(f" 模型: {model_path}")
        print("=" * 64)

        # ---- 证据链: 训练运行 + 最终评估 ----
        ev.log("training_run", f"{title} · 运行 {run_id}",
               payload={"run_id": run_id, "algorithm": "PPO",
                        "mode": action_mode, "steps": args.steps,
                        "parallel_envs": args.envs, "device": "cpu",
                        "train_seconds": round(train_sec, 1),
                        "steps_per_second": round(fps)})
        ev_rec = ev.log("evaluation", f"{title} · 最终评估 (20 回合确定性)",
                        payload={"run_id": run_id, "episodes": 20,
                                 "deterministic": True,
                                 "success_rate": round(sr, 4),
                                 "crash_rate": round(cr, 4),
                                 "mean_alt_err_m": round(err, 2),
                                 "env": "FastHeloEnv (简化动力学, 50Hz)"},
                        artifacts=[("评估指标", results_path),
                                   ("策略模型", model_path)])
        ev.finish_run(stage="finished")
        ev.update_live(metrics={"success_rate": round(sr, 4),
                                "crash_rate": round(cr, 4),
                                "mean_alt_err_m": round(err, 2)},
                       history_point={"steps": args.steps,
                                      "success_rate": round(sr, 3),
                                      "crash_rate": round(cr, 3),
                                      "mean_alt_err_m": round(err, 2)})
        ev.clear_live()
        print(f" 证据已上链: {ev_rec['id']}  hash={ev_rec['hash'][:16]}…")
        return 0

    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        ev.log("incident", f"{title} · 训练异常终止",
               payload={"run_id": run_id, "error": err[-2000:]})
        ev.finish_run(stage="failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
