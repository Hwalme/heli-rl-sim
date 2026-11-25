# -*- coding: utf-8 -*-
"""简化测试：验证 JSBSim 环境和 PPO 模型能否正常加载和运行"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("[1] Importing...")
import numpy as np
from jsbsim_helo_env import JSBSimHeloEnv
from stable_baselines3 import PPO

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "jsbsim_ppo_direct.zip")
print(f"[2] Model path: {MODEL_PATH}")
print(f"[2] Model exists: {os.path.exists(MODEL_PATH)}")

print("[3] Creating env...")
env = JSBSimHeloEnv(action_mode="direct", wind=True, target_range=(15.0, 25.0))
print("[3] Env created")

print("[4] Loading model...")
model = PPO.load(MODEL_PATH, env=env)
print("[4] Model loaded")

print("[5] Running 1 episode...")
obs, info = env.reset(seed=42)
print(f"[5] Reset done, target_alt={info.get('target_alt')}")

total_reward = 0
for step in range(500):
    action, _ = model.predict(obs, deterministic=True)
    obs, reward, terminated, truncated, info = env.step(action)
    total_reward += reward
    if step % 100 == 0:
        s = env._get_raw_state()
        print(f"  step={step} agl={s['agl']:.2f}m vz={s['vz']:.2f}m/s "
              f"roll={np.degrees(s['roll']):.1f}deg reward={reward:.3f}")
    if terminated or truncated:
        print(f"  Done at step={step}: term={terminated} trunc={truncated} "
              f"success={info.get('success')} crash={info.get('crashed')}")
        break

print(f"[6] Episode done: total_reward={total_reward:.2f}")
print("[7] SUCCESS - all tests passed!")
env.close()
