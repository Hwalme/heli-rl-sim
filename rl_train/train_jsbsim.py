# -*- coding: utf-8 -*-
"""
train_jsbsim.py: 在 JSBSim (AH-1S / helispike) 高保真飞行动力学环境上训练 PPO 强化学习智能体

支持:
  - --task hover|forward|landing|roll|yaw|waypoint|takeoff|takeoff_hard|takeoff_fg|heli_hover|heli_takeoff|heli_landing|heli_forward
  - --mode direct (4通道控制) 或 --mode arm (7关节机械臂, 仅 hover 支持)
  - --steps 训练总步数 (默认 300,000)
  - --n-envs 并行环境数 (默认 4)
  - --eval-episodes 评估回合数 (默认 20)
  - --seeds 多种子鲁棒性评估 (例如 42,123,7,0,99)
  - --eval-only 纯评估模式
"""
import os
import sys
import time
import json
import shutil
import argparse
import numpy as np

# Force UTF-8 on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.monitor import Monitor

import torch

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from jsbsim_helo_env import JSBSimHeloEnv
from jsbsim_tasks import make_task
from helispike_env import make_helispike_env

TASK_NAMES = [
    "hover", "forward", "landing", "roll", "yaw", "waypoint", "takeoff", "takeoff_hard", "takeoff_fg",
    "heli_hover", "heli_takeoff", "heli_landing", "heli_forward", "heli_roll", "heli_yaw", "heli_waypoint"
]


def init_from_model(model, init_model_path):
    """课程学习权重迁移: 旧模型与任务观测维度不同时 (悬停 10 维 -> 任务
    10+n 维), 拷贝公共权重, 新增观测维度对应的第一层列置零 —— 初始行为
    与旧策略完全一致, 梯度可流入新维度。"""
    old = PPO.load(init_model_path, device="cpu")
    with torch.no_grad():
        model.policy.action_net.load_state_dict(old.policy.action_net.state_dict())
        # mlp_extractor: policy_net / value_net 的第二层起全拷贝
        for name in ("policy_net", "value_net"):
            old_seq = getattr(old.policy.mlp_extractor, name)
            new_seq = getattr(model.policy.mlp_extractor, name)
            new_seq[2].load_state_dict(old_seq[2].state_dict())
            old_w = old_seq[0].weight          # [64, 10]
            new_w = new_seq[0].weight          # [64, 10+n]
            min_dim = min(old_w.shape[1], new_w.shape[1])
            new_w[:, :min_dim] = old_w[:, :min_dim]
            if new_w.shape[1] > min_dim:
                new_w[:, min_dim:] = 0.0
            new_seq[0].bias.copy_(old_seq[0].bias)
        if hasattr(old.policy, "value_net") and hasattr(model.policy, "value_net"):
            model.policy.value_net.load_state_dict(old.policy.value_net.state_dict())
        if hasattr(old.policy, "log_std") and hasattr(model.policy, "log_std"):
            model.policy.log_std.data.copy_(old.policy.log_std.data)
    print(f"[Curriculum Init] Weights transferred from {init_model_path}")


_HOVER_POLICY_CACHE = {}


class MidTrainEvalCallback:
    """E 轨 D9-扩展条: 长程训练中途评估（防 v2.3"60 万步死态无人发现"重演）。

    每隔 `every` 步用当前策略做 n_episodes 回合确定性评估，把
    时间步/成功率/平均高度误差/动作 std/熵损失 追加写入 log_path。
    任何异常都被吞掉，绝不因为评估失败而中断训练。
    """

    def __new__(cls, task, mode, every, n_episodes, log_path):
        from stable_baselines3.common.callbacks import BaseCallback

        class _CB(BaseCallback):
            def __init__(self):
                super().__init__()
                self._next = every

            def _on_step(self):
                if self.num_timesteps < self._next:
                    return True
                self._next += every
                try:
                    env = make_env(task=task, action_mode=mode, wind=True, seed=777)()
                    succ = crash = 0
                    errs = []
                    for ep in range(n_episodes):
                        obs, info = env.reset(seed=9000 + ep)
                        done = False
                        while not done:
                            act, _ = self.model.predict(obs, deterministic=True)
                            obs, _, te, tr, info = env.step(act)
                            done = te or tr
                        succ += bool(info.get("success"))
                        crash += bool(info.get("crashed"))
                        errs.append(info.get("mean_alt_err", info.get("alt_err", 99.0)))
                    env.close()
                    std = float(self.model.policy.log_std.exp().mean())
                    try:
                        ent = float(self.logger.name_to_value.get("train/entropy_loss", float("nan")))
                    except Exception:
                        ent = float("nan")
                    msg = ("[MidEval] steps=%d  success=%.0f%%  crash=%.0f%%  mean_alt_err=%.2fm  std=%.2f  entropy_loss=%.2f"
                           % (self.num_timesteps, 100.0 * succ / max(1, n_episodes),
                              100.0 * crash / max(1, n_episodes),
                              float(np.mean(errs)) if errs else float("nan"), std, ent))
                    print(msg, flush=True)
                    with open(log_path, "a", encoding="utf-8") as fo:
                        fo.write(msg + "\n")
                except Exception as exc:  # 评估失败不得影响训练
                    print("[MidEval] skipped: %r" % (exc,), flush=True)
                return True

        return _CB()


def _get_hover_policy():
    """加载已训悬停策略 (模块级缓存), 供任务环境 reset 阶段飞爬升段使用"""
    if "model" not in _HOVER_POLICY_CACHE:
        path = os.path.join(script_dir, "results", "jsbsim_ppo_direct.zip")
        _HOVER_POLICY_CACHE["model"] = PPO.load(path, device="cpu")
    return _HOVER_POLICY_CACHE["model"]


def make_env(task="hover", action_mode="direct", wind=True, target_range=(10.0, 30.0), seed=0):
    def _init():
        if task.startswith("heli_") or task.startswith("helispike_"):
            if action_mode == "arm" and "hover" not in task:
                raise ValueError(
                    f"task={task} 暂不支持 arm 模式 (D12 决议: 本期无真机路线仅放行 heli_hover; "
                    "其余科目待 interfaces/action-space v2 契约发布后再解冻)")
            env = make_helispike_env(task=task, action_mode=action_mode, wind=wind, target_range=target_range, seed=seed)
        elif task == "hover":
            env = JSBSimHeloEnv(action_mode=action_mode, wind=wind, target_range=target_range)
        else:
            if action_mode != "direct":
                raise ValueError(f"task={task} 仅支持 direct 模式 (arm 映射契约目前只覆盖悬停任务)")
            env = make_task(task)
            try:
                env.attach_hover_policy(_get_hover_policy())
            except Exception as e:
                print(f"[Warn] hover policy attach failed ({e}), falling back to PD climb")
        env = Monitor(env)
        return env
    return _init


def evaluate_policy(model, env_fn, n_episodes=20, seed=42, record_trajectory=False, max_recorded_episodes=3):
    """评估策略，可选记录逐帧飞行轨迹数据（用于网站自动绘制曲线）。"""
    env = env_fn()
    successes = []
    crashes = []
    alt_errors = []
    ep_lengths = []
    trajectory = []

    for ep in range(n_episodes):
        obs, info = env.reset(seed=seed + ep * 100)
        done = False
        step = 0
        last_info = info
        ep_frames = []

        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated

            if record_trajectory and ep < max_recorded_episodes:
                try:
                    unwrapped = env.unwrapped
                    _s = getattr(unwrapped, '_raw', lambda: {})()
                    frame = {
                        "t": round(step * 0.02, 2),
                        "agl": round(float(_s.get('agl', 0)), 2),
                        "vz": round(float(_s.get('vz', 0)), 2),
                        "pitch": round(float(np.degrees(_s.get('pitch', 0))), 2),
                        "roll": round(float(np.degrees(_s.get('roll', 0))), 2),
                        "psi": round(float(np.degrees(_s.get('psi', 0))), 2),
                        "rpm": round(float(_s.get('rpm', 0)), 1),
                        "coll": round(float(action[0]), 3),
                        "cyclic_lon": round(float(action[1]), 3),
                        "cyclic_lat": round(float(action[2]), 3),
                        "pedal": round(float(action[3]), 3),
                    }
                    try:
                        _fdm = getattr(unwrapped, 'fdm', None)
                        if _fdm:
                            frame["heading"] = round(float(np.degrees(_fdm.get_property_value('attitude/psi-rad'))), 2)
                            frame["lat"] = round(float(_fdm.get_property_value('position/lat-geod-deg')), 7)
                            frame["lon"] = round(float(_fdm.get_property_value('position/long-gc-deg')), 7)
                    except Exception:
                        pass
                    ep_frames.append(frame)
                except Exception:
                    pass

            step += 1
            last_info = info

        if record_trajectory and ep < max_recorded_episodes and ep_frames:
            trajectory.append({
                "episode": ep,
                "seed": seed + ep * 100,
                "success": bool(last_info.get("success", False)),
                "crashed": bool(last_info.get("crashed", False)),
                "n_steps": step,
                "frames": ep_frames,
            })

        success = last_info.get("success", False)
        crashed = last_info.get("crashed", False)
        mean_err = last_info.get("mean_alt_err",
                                 last_info.get("mean_err",
                                               abs(last_info.get("agl", 0) - last_info.get("target_alt", 0))))
        mean_err = float(np.nan_to_num(mean_err, nan=50.0, posinf=50.0, neginf=50.0))
        mean_err = float(np.clip(mean_err, 0.0, 100.0))

        successes.append(float(success))
        crashes.append(float(crashed))
        alt_errors.append(float(mean_err))

        ep_lengths.append(step)

    result = {
        "success_rate": float(np.mean(successes)),
        "crash_rate": float(np.mean(crashes)),
        "mean_alt_err_m": float(np.mean(alt_errors)),
        "episodes": n_episodes,
    }
    if record_trajectory and trajectory:
        result["trajectory"] = trajectory
    return result


def _archive_existing(path, ts=None):
    if not os.path.exists(path):
        return None
    ts = ts or time.strftime("%Y%m%dT%H%M%S")
    archive_dir = os.path.join(script_dir, "results", "archive")
    os.makedirs(archive_dir, exist_ok=True)
    bak = os.path.join(archive_dir, f"{os.path.basename(path)}.{ts}.bak")
    shutil.move(path, bak)
    return bak


def _resolve_output_paths(args, results_dir):
    base = f"jsbsim_ppo_{args.mode}" if args.task == "hover" else f"jsbsim_ppo_{args.task}"
    # heli_* 任务 arm 模式必须在结果名中标注 mode, 否则与 direct canonical
    # (jsbsim_ppo_heli_hover.*) 撞名, 违反 D9 三件套可归因纪律
    if args.task != "hover" and args.mode == "arm":
        base = f"{base}_arm"

    if args.eval_only or args.model_path:
        save_path = args.model_path or os.path.join(results_dir, f"{base}.zip")
        stem = os.path.splitext(os.path.basename(save_path))[0]
        return stem, save_path, os.path.join(results_dir, f"{stem}_results.json")

    trio = [os.path.join(results_dir, base + s) for s in (".zip", "_results.json", "_trajectory.json")]
    if any(os.path.exists(p) for p in trio):
        if getattr(args, "overwrite_canonical", False):
            ts = time.strftime("%Y%m%dT%H%M%S")
            for p in trio:
                _archive_existing(p, ts)
            print(f"[覆盖保护] --overwrite-canonical: '{base}' 旧三件套已归档至 results/archive/")
        else:
            base = f"{base}_v{time.strftime('%Y%m%dT%H%M%S')}"
            print("=" * 60)
            print(f"[覆盖保护] canonical 名已存在 -> 本次结果改用新名字: {base}")
            print("=" * 60)
    return base, os.path.join(results_dir, f"{base}.zip"), os.path.join(results_dir, f"{base}_results.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, default="hover", choices=TASK_NAMES)
    parser.add_argument("--mode", type=str, default="direct", choices=["direct", "arm"])
    parser.add_argument("--steps", type=int, default=300000)
    parser.add_argument("--n-envs", type=int, default=4)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--ent-coef", type=float, default=0.005)
    parser.add_argument("--gamma", type=float, default=0.99,
                        help="折扣因子 gamma (默认 0.99; Waypoint 长视野任务建议 0.995)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-seeds", type=str, default="42,123,7,0,99")
    parser.add_argument("--eval-only", action="store_true")
    parser.add_argument("--model-path", type=str, default=None)
    parser.add_argument("--init-model", type=str, default=None,
                        help="从已有模型权重初始化 (课程学习), 然后继续训练")
    parser.add_argument("--overwrite-canonical", action="store_true",
                        help="允许顶替 canonical 文件名 (旧三件套自动归档到 results/archive/)")
    parser.add_argument("--checkpoint-every", type=int, default=0,
                        help="每 N 步保存一次检查点到 results/checkpoints/<model>/ (0=关闭)")
    parser.add_argument("--target-kl", type=float, default=None,
                        help="PPO 早停 KL 阈值 (E 轨 2026-09-12: 防策略逃逸至随机区, 建议 0.01)")
    parser.add_argument("--mid-eval-every", type=int, default=0,
                        help="每 N 步做一次中途抽样评估并写日志 (E 轨 D9-扩展条, 建议 250000)")
    parser.add_argument("--mid-eval-episodes", type=int, default=8,
                        help="中途评估回合数 (默认 8)")
    args = parser.parse_args()

    results_dir = os.path.join(script_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    model_name, save_path, results_path = _resolve_output_paths(args, results_dir)
    logs_dir = os.path.join(script_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    mid_eval_log = os.path.join(logs_dir, "%s_mideval.log" % model_name)

    is_helispike = args.task.startswith("heli_") or args.task.startswith("helispike_")
    aircraft_name = "helispike" if is_helispike else "ah1s"
    physics_desc = "helispike-0.2-WIP (200Hz FDM + AFCS)" if is_helispike else "6-DoF rigid body + BET rotor + ground effect + aero tables"

    print("=" * 60)
    print(f"  JSBSim PPO Training / Evaluation [Task: {args.task} | Aircraft: {aircraft_name}]")
    print(f"  Steps: {args.steps} | Seed: {args.seed} | Envs: {args.n_envs}")
    print("=" * 60)

    if not args.eval_only:
        env_fns = [make_env(task=args.task, action_mode=args.mode, seed=args.seed + i) for i in range(args.n_envs)]
        vec_env = DummyVecEnv(env_fns)

        model = PPO(
            "MlpPolicy",
            vec_env,
            learning_rate=args.lr,
            n_steps=1024,
            batch_size=128,
            n_epochs=10,
            gamma=args.gamma,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=args.ent_coef,
            vf_coef=0.5,
            max_grad_norm=0.5,
            target_kl=args.target_kl,
            verbose=1,
            seed=args.seed,
            device="cpu",
        )

        if args.init_model:
            init_from_model(model, args.init_model)

        t0 = time.time()
        print(f"\n[Training Start] Target steps: {args.steps} ...")
        callbacks = []
        if args.checkpoint_every > 0:
            from stable_baselines3.common.callbacks import CheckpointCallback
            ckpt_dir = os.path.join(results_dir, "checkpoints", model_name)
            os.makedirs(ckpt_dir, exist_ok=True)
            callbacks.append(CheckpointCallback(
                save_freq=max(1, args.checkpoint_every // args.n_envs),
                save_path=ckpt_dir, save_vecnormalize=False, name_prefix=model_name))
            print(f"[Checkpoint] every {args.checkpoint_every} steps -> {ckpt_dir}")
        # E 轨 D9-扩展条(2026-09-12): 长程训练强制中途评估。
        #   v2.3 事故: 145 万步 -50 死态持续 60 万步无人发现(检查点只存不评)。
        #   现每 mid_eval_every 步做一次短评估并把 成功率/std/熵 写入日志，
        #   劣化即早发现，且评估失败不中断训练。
        if args.mid_eval_every > 0:
            callbacks.append(MidTrainEvalCallback(
                task=args.task, mode=args.mode, every=args.mid_eval_every,
                n_episodes=args.mid_eval_episodes, log_path=mid_eval_log))
            print(f"[MidEval] every {args.mid_eval_every} steps -> {mid_eval_log}")
        model.learn(total_timesteps=args.steps, callback=callbacks or None)
        t1 = time.time()

        train_seconds = t1 - t0
        sps = args.steps / max(1e-3, train_seconds)
        print(f"\n[Training Done] Time: {train_seconds:.1f}s | Throughput: {sps:.1f} steps/s")

        model.save(save_path)
        print(f"[Model Saved] -> {save_path}")
        vec_env.close()
    else:
        print(f"\n[Model Load] Loading from {save_path} ...")
        model = PPO.load(save_path)
        train_seconds = 0.0
        sps = 0.0

    eval_seeds = [int(s.strip()) for s in args.eval_seeds.split(",") if s.strip()]
    print(f"\n[Multi-Seed Robustness Evaluation] Evaluating {len(eval_seeds)} seeds x 20 episodes...")

    eval_env_fn = make_env(task=args.task, action_mode=args.mode, wind=True, seed=999)
    seed_results = {}
    all_success_rates = []
    all_crash_rates = []
    all_alt_errors = []
    all_trajectories = {}

    for s in eval_seeds:
        res = evaluate_policy(model, eval_env_fn, n_episodes=20, seed=s,
                              record_trajectory=True, max_recorded_episodes=3)
        if "trajectory" in res:
            all_trajectories[f"seed_{s}"] = res.pop("trajectory")
        seed_results[f"seed_{s}"] = res
        all_success_rates.append(res["success_rate"])
        all_crash_rates.append(res["crash_rate"])
        all_alt_errors.append(res["mean_alt_err_m"])
        print(f"  Seed {s:4d}: Success = {res['success_rate']*100:5.1f}%, Crash = {res['crash_rate']*100:4.1f}%, AltErr = {res['mean_alt_err_m']:5.2f}m")

    overall_success = float(np.mean(all_success_rates))
    overall_crash = float(np.mean(all_crash_rates))
    overall_alt_err = float(np.mean(all_alt_errors))

    print("\n" + "=" * 60)
    print(f"  [Final Evaluation Summary] {len(eval_seeds)} Seeds x 20 Episodes = {len(eval_seeds)*20} Test Runs")
    print(f"  Overall Success Rate: {overall_success*100:5.1f}% (Procurement Target: >= 85%)")
    print(f"  Overall Crash Rate:   {overall_crash*100:5.1f}% (Target: 0.0%)")
    print(f"  Overall Alt Error:    {overall_alt_err:5.2f} m")
    print(f"  Acceptance Status:    {'[PASS] Meets Procurement Requirements' if overall_success >= 0.85 and overall_crash == 0.0 else '[FAIL] Needs Tuning'}")
    print("=" * 60)

    env_config = {
        "engine": "jsbsim-1.3.1",
        "aircraft": aircraft_name,
        "physics": physics_desc,
        "action_mode": args.mode,
        "wind_enabled": True,
        "wind_range_fps": [-6.0, 6.0],
        "dt": 0.005 if is_helispike else 0.02,
        "action_repeat": 4 if is_helispike else 1,
        "crash_criteria": "俯仰/滚转 > 60° 或 硬接地 vz < -3 m/s",
        "success_criteria": "未坠毁 且 满足任务指标",
    }

    train_config = {
        "algorithm": "PPO",
        "total_steps": args.steps,
        "n_envs": args.n_envs,
        "learning_rate": args.lr,
        "ent_coef": args.ent_coef,
        "seed": args.seed,
        "eval_seeds": eval_seeds,
        "eval_episodes_per_seed": 20,
        "ppo_hyperparams": {
            "n_steps": 1024,
            "batch_size": 128,
            "n_epochs": 10,
            "gamma": args.gamma,
            "gae_lambda": 0.95,
            "clip_range": 0.2,
            "vf_coef": 0.5,
            "max_grad_norm": 0.5,
        },
        "init_model": args.init_model,
    }

    task_metrics_map = {
        "hover": "mean_alt_err_m",
        "heli_hover": "mean_alt_err_m",
        "takeoff": "mean_alt_err_m",
        "takeoff_hard": "mean_alt_err_m",
        "takeoff_fg": "mean_alt_err_m",
        "heli_takeoff": "mean_alt_err_m",
        "landing": "mean_descent_profile_err_mps",
        "heli_landing": "mean_descent_profile_err_mps",
        "forward": "mean_speed_err_kt",
        "heli_forward": "mean_speed_err_kt",
        "roll": "mean_bank_err_deg",
        "heli_roll": "mean_bank_err_deg",
        "yaw": "mean_heading_err_deg",
        "heli_yaw": "mean_heading_err_deg",
        "waypoint": "mean_wp_miss_dist_m",
        "heli_waypoint": "mean_wp_miss_dist_m",
    }

    report_data = {
        "engine": "jsbsim-1.3.1",
        "aircraft": aircraft_name,
        "physics": physics_desc,
        "task": args.task,
        "task_metric": task_metrics_map.get(args.task, "mean_alt_err_m"),
        "mode": args.mode,
        "steps": args.steps,
        "train_seconds": round(train_seconds, 1),
        "steps_per_second": round(sps, 1),
        "overall_success_rate": overall_success,
        "overall_crash_rate": overall_crash,
        "overall_mean_alt_err_m": round(overall_alt_err, 2),
        "procurement_acceptance_target": ">= 85% success, 0% crash",
        "acceptance_passed": bool(overall_success >= 0.85 and overall_crash == 0.0),
        "env_config": env_config,
        "train_config": train_config,
        "seed_results": seed_results,
        "verified_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    print(f"[Results Written] Saved to: {results_path}")

    if all_trajectories:
        trajectory_path = os.path.join(results_dir, f"{model_name}_trajectory.json")
        trajectory_data = {
            "engine": "jsbsim-1.3.1",
            "aircraft": aircraft_name,
            "task": args.task,
            "mode": args.mode,
            "model": model_name,
            "dt": 0.02,
            "recorded_episodes_per_seed": 3,
            "seeds": eval_seeds,
            "overall_success_rate": overall_success,
            "overall_crash_rate": overall_crash,
            "verified_timestamp": report_data["verified_timestamp"],
            "trajectories": all_trajectories,
        }
        with open(trajectory_path, "w", encoding="utf-8") as f:
            json.dump(trajectory_data, f, ensure_ascii=False, separators=(",", ":"))
        traj_size = os.path.getsize(trajectory_path)
        print(f"[Trajectory Written] Saved to: {trajectory_path} ({traj_size/1024:.1f} KB)")


if __name__ == '__main__':
    main()
