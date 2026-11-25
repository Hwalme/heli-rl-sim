# -*- coding: utf-8 -*-
"""
启发式基准: 用手写 PD 控制器验证环境可解 (起飞->目标高度悬停)。
如果这个都跑不通, 说明环境本身有 bug, 先修环境再谈 RL。
另: 对 arm 模式做 100 步冒烟测试。
"""
import numpy as np
from helo_env import FastHeloEnv, HOVER_COLLECTIVE


def pd_control(obs, target_alt):
    """obs = [agl, vz, vx, vy, pitch, pr, roll, rr, yawr, alt_err] (归一化刻度)"""
    agl = obs[0] * 100.0
    vz = obs[1] * 15.0
    pitch = obs[4] * np.radians(45)
    pitch_rate = obs[5] * 3.0
    roll = obs[6] * np.radians(45)
    roll_rate = obs[7] * 3.0
    yaw_rate = obs[8] * np.radians(60)

    vs_cmd = float(np.clip(0.4 * (target_alt - agl), -2.0, 5.0))
    collective = float(np.clip(HOVER_COLLECTIVE + 0.06 * (vs_cmd - vz), 0.0, 1.0))
    cyclic_pitch = float(np.clip(-1.5 * pitch - 0.3 * pitch_rate, -1, 1))
    cyclic_roll = float(np.clip(-1.5 * roll - 0.3 * roll_rate, -1, 1))
    pedals = float(np.clip(-0.5 * yaw_rate / np.radians(60), -1, 1))
    return np.array([collective * 2 - 1, cyclic_pitch, cyclic_roll, pedals], dtype=np.float32)


def main():
    print("=" * 64)
    print(" 启发式基准 (direct 模式, PD 控制器)")
    print("=" * 64)

    env = FastHeloEnv(action_mode="direct", episode_steps=1000)
    n_success, n_crash, errs = 0, 0, []
    episodes = 20
    for ep in range(episodes):
        obs, info = env.reset(seed=ep)
        target = info["target_alt"]
        ep_r, done = 0.0, False
        while not done:
            obs, r, term, trunc, info = env.step(pd_control(obs, target))
            ep_r += r
            done = term or trunc
        n_success += bool(info.get("success"))
        n_crash += bool(info.get("crashed"))
        errs.append(info.get("mean_alt_err", 99))
        if ep < 3 or ep == episodes - 1:
            print(f"  ep{ep:02d}: target={target:5.1f}m final={info['agl']:6.1f}m "
                  f"crash={info['crashed']} success={info.get('success')} R={ep_r:8.1f}")

    print(f"\n[结果] {episodes} 回合: 成功 {n_success}/{episodes} | "
          f"坠毁 {n_crash} | 平均高度误差 {np.mean(errs):.2f} m")

    print("\n" + "=" * 64)
    print(" arm 模式冒烟测试 (100 步)")
    print("=" * 64)
    env2 = FastHeloEnv(action_mode="arm", episode_steps=100)
    obs, info = env2.reset(seed=0)
    # 关节0 目标 1.0 -> collective ≈ (1.0+1.5)/3 ≈ 0.833 > 悬停阈值, 应离地
    act = np.zeros(7, dtype=np.float32)
    act[0] = 1.0
    for i in range(100):
        obs, r, term, trunc, info = env2.step(act)
    print(f"  arm 模式 100 步后 agl={info['agl']:.1f}m (关节0=1.0, 应>0)")
    assert info["agl"] > 0.5, "arm 模式无法起飞!"
    print("\n[PASS] 环境自检通过, 可以开始 RL 训练")


if __name__ == "__main__":
    main()
