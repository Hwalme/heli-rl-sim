# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jsbsim_tasks import WaypointTrackTask

env = WaypointTrackTask()
for i in range(5):
    obs, info = env.reset(seed=42 + i)
    print(f"ep {i}: phase={info['course_phase']}, wps={len(env.wps)}, radius={info['wp_radius_m']}m, hover={info['hover_established']}")
env.close()
print("Waypoint curriculum OK")
