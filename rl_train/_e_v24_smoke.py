# -*- coding: utf-8 -*-
"""v2.4 smoke: confirm the reshaped potential no longer collapses and the env runs."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import train_jsbsim

env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=True, seed=42)()
tot = 0.0
for ep in range(3):
    obs, info = env.reset(seed=100 + ep)
    done = False
    r_sum = 0.0
    steps = 0
    i2 = {}
    while not done and steps < 60:
        obs, r, te, tr, i2 = env.step(np.zeros(4, dtype=np.float32))
        r_sum += r
        steps += 1
        done = te or tr
    tot += r_sum
    drift = i2.get('net_drift_m', 0.0)
    print('  ep%d steps=%d rew=%.1f drift=%.1fm crashed=%s'
          % (ep, steps, r_sum, drift, i2.get('crashed')))
print('mean rew=%.1f  (v2.4: 势函数 0.5 权重 + 净值式)' % (tot / 3.0))
env.close()
