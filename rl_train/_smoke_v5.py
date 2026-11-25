# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"rl_train")
from jsbsim_tasks import make_task
import numpy as np

for name in ['takeoff', 'takeoff_hard', 'takeoff_fg']:
    env = make_task(name)
    obs, info = env.reset(seed=42)
    print(f"{name}: obs_dim={obs.shape[0]}, reset_ok={info.get('ground_start')}")
    for i in range(100):
        obs, r, term, trunc, info = env.step(np.zeros(4))
        if term or trunc:
            break
    print(f"  ran {i+1} steps, crashed={info.get('crashed')}, success={info.get('success')}, drift={info.get('net_drift_m', 'N/A')}")
    env.close()
print("Smoke test PASSED")
