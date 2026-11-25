# -*- coding: utf-8 -*-
"""D1 smoke (v2): verify arm mode actually works for heli_hover, on both
   the helispike (gen-2, currently asserts) and the JSBSimHeloEnv (gen-1)."""
import sys, os, warnings, traceback
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import train_jsbsim

print('=== A) JSBSimHeloEnv (一代) heli_hover arm mode ===')
try:
    env_fn = train_jsbsim.make_env(task='heli_hover', action_mode='arm', wind=False, seed=42)
    env = env_fn()
    obs, info = env.reset(seed=42)
    print(f'  obs shape={obs.shape}  action_space={env.action_space}')
    for i in range(60):
        a = np.zeros(7, dtype=np.float32)
        obs, r, te, tr, inf = env.step(a)
        if te or tr:
            print(f'  ep ended at step {i+1}: crashed={inf.get("crashed")} alt={inf.get("agl"):.2f}m')
            break
    else:
        print(f'  survived 60 steps; last agl={inf.get("agl"):.2f}m vz={inf.get("vz"):.2f}')
    env.close()
    print('  RESULT: OK')
except Exception as e:
    print(f'  RESULT: FAILED -- {e}')
    traceback.print_exc()

print()
print('=== B) HelispikeHoverEnv (二代) heli_hover arm mode -- EXPECT assert fail (D1 entry point) ===')
try:
    env_fn = train_jsbsim.make_env(task='heli_hover', action_mode='arm', wind=False, target_range=(15.0,15.0), seed=42)
    env = env_fn()
    print(f'  UNEXPECTED: helispike accepted arm mode, obs={env.observation_space.shape}')
except Exception as e:
    print(f'  blocked as expected: {e}')

print()
print('=== C) HelispikeHoverEnv direct mode sanity (baseline) ===')
try:
    env_fn = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=False, target_range=(15.0,15.0), seed=42)
    env = env_fn()
    obs, info = env.reset(seed=42)
    for i in range(60):
        a = np.zeros(4, dtype=np.float32)
        obs, r, te, tr, inf = env.step(a)
        if te or tr: break
    else:
        print(f'  survived 60 steps; last agl={inf.get("agl"):.2f}m')
    env.close()
    print('  RESULT: OK')
except Exception as e:
    print(f'  RESULT: FAILED -- {e}')
