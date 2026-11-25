# -*- coding: utf-8 -*-
"""
v4 物理层增强方向可行性探针
验证: 配平收敛后, 双向(左/右)满舵偏航速率是否对称可达
前任探针结论(v3前): trim冻结+0.7时, 任何脚蹬输入下 r 均为负(左转), 正速率不可达
v4 修复: pedal-gain 0.25->0.4, trim限幅对称化[-0.5,+0.5], kI 0.0015->0.005, yaw_gain 0.3->0.45
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import math
from jsbsim_tasks import YawManeuverTask

DT = 0.02

def probe_direction(u_yaw_val, n_steps=300, label=""):
    """施加恒定 u_yaw, 跑 n_steps, 返回末段平均偏航速率(deg/s)"""
    env = YawManeuverTask()
    obs, info = env.reset(seed=42)
    trim_after_settle = env._rud_trim
    r_after_settle = math.degrees(env.fdm.get_property_value('velocities/r-rad_sec'))

    r_list = []
    for i in range(n_steps):
        action = np.array([0.0, 0.0, 0.0, u_yaw_val], dtype=np.float32)
        obs, r, term, trunc, info = env.step(action)
        if term:
            break
        if i > n_steps // 2:  # 只记录后半段稳态
            r_list.append(math.degrees(env.fdm.get_property_value('velocities/r-rad_sec')))

    r_ss = float(np.mean(r_list)) if r_list else float('nan')
    print(f"[{label}] u_yaw={u_yaw_val:+.1f} | trim_after_settle={trim_after_settle:+.3f} "
          f"r_after_settle={r_after_settle:+.1f}dps | steady_r={r_ss:+.1f}dps | steps={i+1} crash={term}")
    env.close()
    return r_ss

print("=" * 70)
print("v4 物理层增强 - 偏航方向可行性探针 (seed=42, 配平8s后测试)")
print("=" * 70)

# 零动作: 配平后的残余偏航速率
print("\n--- 零动作基线 (配平后残余自旋) ---")
probe_direction(0.0, label="zero")

# 满左舵
print("\n--- 满左舵 u_yaw=+1.0 ---")
r_left = probe_direction(+1.0, label="left-full")

# 满右舵
print("\n--- 满右舵 u_yaw=-1.0 ---")
r_right = probe_direction(-1.0, label="right-full")

# 半左舵
print("\n--- 半左舵 u_yaw=+0.5 ---")
probe_direction(+0.5, label="left-half")

# 半右舵
print("\n--- 半右舵 u_yaw=-0.5 ---")
probe_direction(-0.5, label="right-half")

print("\n" + "=" * 70)
print("结论判定:")
print(f"  左舵稳态速率: {r_left:+.1f} dps (期望 < 0, 即左转)")
print(f"  右舵稳态速率: {r_right:+.1f} dps (期望 > 0, 即右转)")
if r_left < -1.0 and r_right > 1.0:
    print("  ✅ 双向偏航速率对称可达 - Yaw 任务数学可解!")
elif r_left < -1.0 and abs(r_right) <= 1.0:
    print("  ⚠️  仅左转可达, 右转仍被压制 - 需要进一步增强 pedal-gain 或 trim 负向限幅")
elif abs(r_left) <= 1.0 and r_right > 1.0:
    print("  ⚠️  仅右转可达, 左转被压制 - 异常, 检查 trim 初始值")
else:
    print("  ❌ 双向均不可达 - 物理层增强不足")
print("=" * 70)
