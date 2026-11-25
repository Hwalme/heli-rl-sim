# -*- coding: utf-8 -*-
"""
_probe_ah1s_liveness.py — 属性活性交叉探针 (主E任务书更新·A 轨第 2 项, 10 分钟科目)

背景: B 轨在 helispike (二代, 自定义 root/自研 FDM) 上发现 velocities/*-radps、
position/agl-ft 等属性恒 0 ("属性死亡", INTERFACE_CONTRACT_v1.md §3.2)。
主E要求在 A 的生产环境 (ah1s, 默认 root, 一代管线) 交叉验证同类属性活性——
一代的观测/奖励/成功判据全部压在这些属性上, 若死则一代数字全部存疑。

科目 (读初值 → 施加扰动 → 读末值, 阈值判活):
  velocities/p-rad_sec   ← 副翼脉冲 (滚转力矩)
  velocities/q-rad_sec   ← 升降脉冲 (俯仰力矩)
  velocities/r-rad_sec   ← 脚蹬脉冲 (偏航力矩)
  position/h-agl-ft      ← 总距爬升 (升力)
  attitude/psi-rad       ← 脚蹬持续 (航向积分)
双重口径: 默认 dt (1/120) 与 生产 set_dt(0.02) 各跑一遍 (B 的死亡怀疑与步长绑定)。
静风、种子固定。结果落 results/ah1s_liveness_<ts>.json 并报 mailbox-A。
"""
import io
import json
import math
import os
import sys
import time
import contextlib

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEED = 42
DT = 0.02
FT2M = 0.3048

# 阈值: 末值-初值绝对量超过它判 ALIVE (量级取自生产观测刻度)
THRESH = {
    'velocities/p-rad_sec': 0.05,    # rad/s (≈2.9°/s)
    'velocities/q-rad_sec': 0.05,
    'velocities/r-rad_sec': 0.05,
    'position/h-agl-ft': 1.0,        # ft
    'attitude/psi-rad': 0.05,        # rad (≈2.9°)
}


def make_fdm(set_dt):
    with contextlib.redirect_stdout(io.StringIO()):
        import jsbsim
        f = jsbsim.FGFDMExec(jsbsim.get_default_root_dir(), None)
        f.load_model('ah1s')
        f.load_ic('reset00.xml', True)
        f.run_ic()
        if set_dt:
            f.set_dt(DT)
        f.set_property_value('fcs/rpm-governor-active-norm', 1.0)
        f.set_property_value('propulsion/engine[0]/set-running', 1.0)
        f.set_property_value('propulsion/engine[1]/set-running', 1.0)
        f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
        f.set_property_value('fcs/throttle-cmd-norm[1]', 1.0)
        # 生产同款: 先建立悬停 (v5.2.3 的 settle 纪律), 探针在悬停态施扰
        for _ in range(int(15.0 / DT)):
            s_agl = (f.get_property_value('position/h-agl-ft') - 6.30) * FT2M
            vz = -f.get_property_value('velocities/v-down-fps') * FT2M
            vs_cmd = float(np.clip(0.4 * (12.0 - s_agl), -1.0, 3.0))
            coll = float(np.clip(0.70 + 0.06 * (vs_cmd - vz), 0.0, 1.0))
            th = f.get_property_value('attitude/theta-rad')
            ph = f.get_property_value('attitude/phi-rad')
            q = f.get_property_value('velocities/q-rad_sec')
            p = f.get_property_value('velocities/p-rad_sec')
            r = f.get_property_value('velocities/r-rad_sec')
            f.set_property_value('fcs/collective-cmd-norm', coll)
            f.set_property_value('fcs/elevator-cmd-norm', float(np.clip(1.5 * th + 0.6 * q, -0.5, 0.5)))
            f.set_property_value('fcs/aileron-cmd-norm', float(np.clip(-2.4 * ph - 0.3 * p, -0.5, 0.5)))
            f.set_property_value('fcs/rudder-cmd-norm', float(np.clip(-0.8 * r, -0.6, 0.6)))
            f.run()
    return f


def probe_property(f, prop, disturb, steps, tag):
    """读初值 → 施加扰动 steps 步 → 读末值。返回 dict。"""
    v0 = f.get_property_value(prop)
    for _ in range(steps):
        disturb(f)
        f.run()
    v1 = f.get_property_value(prop)
    alive = abs(v1 - v0) > THRESH.get(prop, 0.05)
    return {"property": prop, "initial": round(v0, 4), "final": round(v1, 4),
            "delta": round(v1 - v0, 4), "threshold": THRESH.get(prop, 0.05),
            "alive": bool(alive), "disturbance": tag}


def run_suite(set_dt, label):
    f = make_fdm(set_dt)
    results = []

    # p ← 副翼脉冲 (正负双向取大者)
    res = probe_property(f, 'velocities/p-rad_sec',
                         lambda fd: fd.set_property_value('fcs/aileron-cmd-norm', 0.3),
                         int(0.5 / DT), 'aileron +0.3 × 0.5s')
    if not res['alive']:
        res2 = probe_property(f, 'velocities/p-rad_sec',
                              lambda fd: fd.set_property_value('fcs/aileron-cmd-norm', -0.3),
                              int(0.5 / DT), 'aileron -0.3 × 0.5s (反向复核)')
        res['alive'] = res2['alive'] or res['alive']
        res['recheck'] = res2['delta']
    results.append(res)

    # q ← 升降脉冲
    res = probe_property(f, 'velocities/q-rad_sec',
                         lambda fd: fd.set_property_value('fcs/elevator-cmd-norm', 0.3),
                         int(0.5 / DT), 'elevator +0.3 × 0.5s')
    if not res['alive']:
        res2 = probe_property(f, 'velocities/q-rad_sec',
                              lambda fd: fd.set_property_value('fcs/elevator-cmd-norm', -0.3),
                              int(0.5 / DT), 'elevator -0.3 × 0.5s (反向复核)')
        res['alive'] = res2['alive'] or res['alive']
        res['recheck'] = res2['delta']
    results.append(res)

    # r ← 脚蹬脉冲
    res = probe_property(f, 'velocities/r-rad_sec',
                         lambda fd: fd.set_property_value('fcs/rudder-cmd-norm', 0.35),
                         int(0.5 / DT), 'rudder +0.35 × 0.5s')
    if not res['alive']:
        res2 = probe_property(f, 'velocities/r-rad_sec',
                              lambda fd: fd.set_property_value('fcs/rudder-cmd-norm', -0.35),
                              int(0.5 / DT), 'rudder -0.35 × 0.5s (反向复核)')
        res['alive'] = res2['alive'] or res['alive']
        res['recheck'] = res2['delta']
    results.append(res)

    # h-agl ← 总距爬升 3s
    results.append(probe_property(f, 'position/h-agl-ft',
                                  lambda fd: fd.set_property_value('fcs/collective-cmd-norm', 0.80),
                                  int(3.0 / DT), 'collective 0.80 × 3s (爬升)'))

    # psi ← 脚蹬持续 2s
    results.append(probe_property(f, 'attitude/psi-rad',
                                  lambda fd: fd.set_property_value('fcs/rudder-cmd-norm', 0.35),
                                  int(2.0 / DT), 'rudder +0.35 × 2s (航向)'))
    return results


def main():
    print("=" * 88)
    print("ah1s 属性活性交叉探针 (生产环境: 默认 root; 双口径 dt; 悬停态施扰; 种子 %d)" % SEED)
    print("=" * 88)
    report = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
              "target": "ah1s @ default root (A 轨一代生产环境)",
              "background": "B 轨 helispike 死属性发现 (INTERFACE_CONTRACT_v1 §3.2) 的交叉验证",
              "protocol": "读初值 → 控制通道施加扰动 → 读末值; |Δ|>阈值判 ALIVE; 失败做反向复核",
              "suites": {}}
    n_alive = n_total = 0
    for set_dt, label in ((False, "默认dt(1/120)"), (True, "生产dt(set_dt 0.02)")):
        results = run_suite(set_dt, label)
        report["suites"][label] = results
        print("\n【%s】" % label)
        for r in results:
            n_total += 1
            n_alive += r['alive']
            print("  %-24s 初=%+9.4f 末=%+9.4f Δ=%+9.4f -> %s (%s)"
                  % (r['property'], r['initial'], r['final'], r['delta'],
                     "ALIVE" if r['alive'] else "DEAD?", r['disturbance']))
    print("\n" + "=" * 88)
    print("结论: %d/%d 项 ALIVE" % (n_alive, n_total))
    if n_alive == n_total:
        print("=> 一代生产环境 (ah1s 默认 root) 全部关键属性存活 —— B 的死属性发现为 helispike/自定义 root 专属, 一代数字不受影响。")
    else:
        print("=> 存在死属性! 一代观测/判据需立即排查 (升级 E)。")
    report["n_alive"], report["n_total"] = n_alive, n_total
    report["all_alive"] = bool(n_alive == n_total)
    out = os.path.join(SCRIPT_DIR, "results", "ah1s_liveness_%s.json" % time.strftime("%Y%m%dT%H%M%S"))
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print("证据已落盘: %s" % out)
    return 0 if n_alive == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
