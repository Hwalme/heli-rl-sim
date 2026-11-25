# -*- coding: utf-8 -*-
"""D1 v2 smoke: validate helispike env arm mode after the patch
   (assert removed, _arm_to_controls added, step() dispatched on action_mode)."""
import sys, os, warnings, traceback
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import train_jsbsim

print('--- helispike heli_hover direct (regression baseline) ---')
try:
    env_fn = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=False,
                                  target_range=(15.0, 15.0), seed=42)
    env = env_fn()
    obs, info = env.reset(seed=42)
    for i in range(60):
        obs, r, te, tr, inf = env.step(np.zeros(4, dtype=np.float32))
        if te or tr:
            break
    else:
        print(f'  survived 60 steps; last agl={inf.get("agl"):.2f}m')
    env.close()
    print('  RESULT: OK (direct mode unaffected)')
except Exception as e:
    print(f'  RESULT: FAILED -- {e}')
    traceback.print_exc()

print()
print('--- helispike heli_hover arm (D1 main goal) ---')
try:
    env_fn = train_jsbsim.make_env(task='heli_hover', action_mode='arm', wind=False,
                                  target_range=(15.0, 15.0), seed=42)
    env = env_fn()
    print(f'  env_class={type(env.unwrapped).__name__}')
    print(f'  obs shape={env.observation_space.shape}')
    print(f'  action_space={env.action_space}')
    obs, info = env.reset(seed=42)
    print(f'  initial info keys: {sorted(info.keys())[:6]}')
    for i in range(120):
        obs, r, te, tr, inf = env.step(np.zeros(7, dtype=np.float32))
        if te or tr:
            print(f'  ended step {i+1}: crashed={inf.get("crashed")} '
                  f'alt={inf.get("agl", 0):.2f}m')
            break
    else:
        print(f'  survived 120 zero-action steps; '
              f'last agl={inf.get("agl"):.2f}m vz={inf.get("vz"):.2f}m/s')
    env.close()
    print('  RESULT: OK (arm mode patches work on helispike)')
except Exception as e:
    print(f'  RESULT: FAILED -- {e}')
    traceback.print_exc()
