# -*- coding: utf-8 -*-
"""D1 smoke v3: bypass the train_jsbsim.make_env factory (which blocks arm)
   and call JSBSimHeloEnv directly to prove the gen-1 arm path works."""
import sys, os, warnings, traceback
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from jsbsim_helo_env import JSBSimHeloEnv

print('--- direct (gen-1 baseline) ---')
env = JSBSimHeloEnv(action_mode='direct', wind=False, target_range=(15.0, 15.0))
obs, info = env.reset(seed=42)
for i in range(60):
    obs, r, te, tr, inf = env.step(np.zeros(4))
    if te or tr:
        break
else:
    print(f'  survived 60 steps; last agl={inf.get("agl"):.2f}m')
env.close()

print()
print('--- arm (gen-1, JSBSimHeloEnv direct call) ---')
env = JSBSimHeloEnv(action_mode='arm', wind=False, target_range=(15.0, 15.0))
print(f'  action_space={env.action_space}')
print(f'  obs={env.observation_space.shape}')
obs, info = env.reset(seed=42)
crashed_step = None
for i in range(120):
    obs, r, te, tr, inf = env.step(np.zeros(7))
    if te or tr:
        crashed_step = i + 1
        print(f'  ended at step {i+1}: crashed={inf.get("crashed")} alt={inf.get("agl", 0):.2f}m')
        break
else:
    print(f'  survived 120 zero-action steps; last agl={inf.get("agl"):.2f}m vz={inf.get("vz"):.2f}m/s rpm={inf.get("rpm")}')
env.close()
