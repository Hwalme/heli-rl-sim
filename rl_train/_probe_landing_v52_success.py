# -*- coding: utf-8 -*-
"""
_probe_landing_v52_success.py — landing v5.2 成功判据"正向单元探针"(E 14:48 指令③)

背景: E 质疑 v5.2 landing 成功判据存在"永不触发"型 bug (两个 2M 训练同型 0%/0%)。
audit_rewards.py 只测判别力 (R1/R2/R3), 测不出"好轨迹也判不成功"的 bug。
本探针构造一条**应当成功**的轨迹 (全保持: 定速下降+位置保持+航向保持+近地减速+强制
穿透接地), 逐项断言 7 个成功条件, 最终断言 success=True:
  1. landed=True
  2. touchdown_vs >= -3.0            (软接地)
  3. net_drift < 30.0                (漂移)
  4. touchdown_hs < 2.0              (接地水平速度)
  5. max_heading_dev < 15°           (下降全程航向)
  6. max_near_vz <= 1.0              (agl<2m 段下降率)
  7. max_near_hs < 0.5               (agl<1m 段水平速度)
若逐项满足而 success=False => 判据代码 bug 实锤; 若某条件物理不可满足 => 判据过严需 E 重校。
不训练, 种子固定, 静风。
"""
import io
import sys
import contextlib

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np
import math

SEED = 42


def run_should_succeed():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        from jsbsim_tasks import LandingTask
        from stable_baselines3 import PPO
        import os
        env = LandingTask(init_alt_m=15.0, episode_steps=2500)
        env.wind_enabled = False  # 静风, 排除构造器无法控风的因素
        # v2: 挂载真实爬升策略 (与训练/评估 make_env 同 setup) —— 不挂则 PD 爬升
        #   无侧向控制, 下降段起点带 v=-8.9 m/s 侧向滑移, 任何控制器都不可恢复
        env.attach_hover_policy(PPO.load(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'results', 'jsbsim_ppo_direct.zip'), device='cpu'))
        obs, info = env.reset(seed=SEED)
        # v3: 悬停策略追踪匀速下降的高度基准 —— 用已证技能 (100% 悬停=定高+位置保持)
        #   组合出"应成功"轨迹, 避免脚本 PD 权限不足的构造器缺陷。动作仍由策略给出
        #   (4 维契约不变), 只是 obs 里的 alt 误差被替换为对下降基准的误差。
        total_r, steps = 0.0, 0
        term = trunc = False
        target_alt = env.init_alt_m
        while not (term or trunc) and steps < 2500:
            s = env._raw()
            target_alt = max(0.0, target_alt - 1.0 * 0.02)   # -1.0 m/s 匀速下降基准
            alt_err = target_alt - s['agl']
            hover_obs = env._hover_obs(s, env.init_alt_m).copy()
            hover_obs[0] = float(np.clip(s['agl'] / 50.0, -10, 10))       # agl 真值
            hover_obs[9] = float(np.clip(alt_err / 50.0, -10, 10))        # 换成对下降基准的误差
            action, _ = env._hover_policy.predict(hover_obs, deterministic=True)  # 10 维旧悬停策略
            obs, r, term, trunc, info = env.step(np.asarray(action))
            total_r += float(r)
            steps += 1
    return info, total_r, steps


def main():
    print("=" * 88)
    print("landing v5.2 判据正向单元探针: 构造应当成功的轨迹, 逐项断言 7 条件 (种子 %d, 静风)" % SEED)
    print("=" * 88)
    info, total_r, steps = run_should_succeed()
    tdvs = info.get('touchdown_vs')
    tdhs = info.get('touchdown_hs_mps')
    checks = [
        ("1 landed (真实接地)", info.get('landed') is True),
        ("2 td_vs>=-3 (软接地)", tdvs is not None and tdvs >= -3.0),
        ("3 drift<30m", info.get('net_drift_m', 99) < 30.0),
        ("4 td_hs<2m/s", tdhs is not None and tdhs < 2.0),
        ("5 dev<15°", info.get('max_heading_dev_deg', 99) < 15.0),
        ("6 near_vz<=1.0", info.get('max_near_vz_mps', 99) <= 1.0),
        ("7 near_hs<0.5", info.get('max_near_hs_mps', 99) < 0.5),
    ]
    print("实测: steps=%d R=%.1f" % (steps, total_r))
    print("  td_vs=%s m/s | td_hs=%s m/s | drift=%.1fm | dev=%.1f° | near_vz=%s | near_hs=%s"
          % ('None' if tdvs is None else '%.2f' % tdvs,
             'None' if tdhs is None else '%.2f' % tdhs,
             info.get('net_drift_m', -1), info.get('max_heading_dev_deg', -1),
             'None' if info.get('max_near_vz_mps') is None else '%.2f' % info['max_near_vz_mps'],
             'None' if info.get('max_near_hs_mps') is None else '%.2f' % info['max_near_hs_mps']))
    print("-" * 88)
    n_pass = 0
    for name, ok in checks:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        n_pass += ok
    env_success = info.get('success')
    print("-" * 88)
    print("环境 success=%s | 条件逐项: %d/7 PASS" % (env_success, n_pass))
    if n_pass == 7 and env_success is True:
        print("=> 判定: PASS — 判据无'永不触发'bug, 应成功轨迹得到 success=True, 可放行重训")
        return 0
    if n_pass == 7 and env_success is False:
        print("=> 判定: BUG 实锤 — 7 条件全满足但 success=False, 判据代码有逻辑错误, 停训修码")
        return 2
    failed = [n for n, ok in checks if not ok]
    print("=> 判定: 判据本身无 bug, 但构造轨迹未满足: %s (物理可行性/构造器问题, 转 E 重校门槛)" % failed)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
