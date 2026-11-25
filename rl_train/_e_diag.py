# -*- coding: utf-8 -*-
# 主E亲自跑 helispike 30k步快速诊断
import sys, os, warnings, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
import train_jsbsim

logf = open('logs/_e_heli_30k.log', 'w', encoding='utf-8')
def w(msg):
    logf.write(msg + '\n'); logf.flush(); print(msg)

env_fn = lambda: train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=True, seed=42)()
vec_env = DummyVecEnv([env_fn])

# 与 AG-3 同参数: lr=3e-4, ent=0.01 (它全跑失败的组)
model = PPO('MlpPolicy', vec_env, learning_rate=3e-4, n_steps=512, batch_size=64,
            ent_coef=0.01, gamma=0.99, verbose=1, seed=42)
w('[E] PPO 模型构造完成, 开始 30k 步训练, 时间: ' + time.strftime('%H:%M:%S'))
t0 = time.time()
model.learn(total_timesteps=30000, progress_bar=False)
w('[E] 训练完成, 用时 %.1fs' % (time.time() - t0))

# 立即跑 3 回合评估
w('[E] 评估 5 回合随机种子:')
for seed in [42, 123, 7]:
    env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=True, seed=seed)()
    obs, info = env.reset(seed=seed)
    ep_rew = 0.0; done = False; steps = 0
    while not done and steps < 1000:
        a, _ = model.predict(obs, deterministic=True)
        obs, r, te, tr, info = env.step(a)
        ep_rew += r; steps += 1
        done = te or tr
    crashed = info.get('crashed')
    landed = info.get('landed')
    w('  seed=%d steps=%d reward=%.1f crashed=%s landed=%s' % (seed, steps, ep_rew, crashed, landed))
    env.close()
logf.close()