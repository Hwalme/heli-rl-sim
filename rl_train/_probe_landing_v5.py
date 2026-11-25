# -*- coding: utf-8 -*-
"""
_probe_landing_v5.py — A1 landing v5 补丁的判据级验证 (audit_rewards.py 的补充)

audit_rewards.py 的脚本控制器在 agl<=0.4m 收零总距, 两轨迹均不接地,
成功判据 (漂移<30m + 接地水平速度<2m/s) 未被触发, 证据不完整。
本探针让 V(垂直意图)/G(滑翔意图) 真实落地或飞离, 验证新判据的区分能力:

  1. V 软接地达成 (td_vs > -3 m/s)         —— 垂直着陆物理可行;
  2. V 接地水平速度若 >= 2 m/s 或漂移 >= 30m -> success=False
     (旧判据只看 vz>-3 会把它判成功 —— 正是用户目击的滑翔降落, 新判据拒绝);
  3. G 滑翔被拒绝 (success=False) 且 R_V - R_G > 0。

注: 脚本 V 控制器无位置反馈, 不追求 V.success=True; "带位置保持的垂直
着陆可达性"由重训后策略 (14 维位置观测) 在 G2 评估中证明。
不训练, 种子固定 42, 静风 (wind off) 以隔离判据本身。
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

SEED = 42


def run_touchdown(mode):
    """mode='V' 垂直下降 / 'G' 滑翔下降 (持续前推杆)。返回 (终止info, 累计奖励, 步数)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        from jsbsim_tasks import LandingTask
        env = LandingTask(init_alt_m=15.0, episode_steps=2500)
        env.wind_enabled = False  # 静风, 隔离判据本身
        obs, info = env.reset(seed=SEED)
        total_r, steps = 0.0, 0
        term = trunc = False
        while not (term or trunc) and steps < 2500:
            s = env._raw()
            u_coll = float(np.clip(0.24 * (-1.2 - s['vz']), -1.0, 1.0))
            if s['agl'] < 1.0:
                u_coll -= 0.25   # 穿透地面效应, 强制真实接地 (audit 控制器在此悬停)
            u_pitch = 0.45 if mode == 'G' else 0.0   # G: 持续前推杆制造滑翔
            obs, r, term, trunc, info = env.step(np.array([u_coll, u_pitch, 0.0, 0.0]))
            total_r += float(r)
            steps += 1
    return info, total_r, steps


def fmt(x, nd=2):
    return 'None' if x is None else ('%.*f' % (nd, x))


def main():
    print("=" * 88)
    print("A1 landing v5 判据级探针: V=垂直下降 vs G=滑翔下降 (强制真接地, 种子 %d, 静风)" % SEED)
    print("=" * 88)
    rows = []
    for mode in ('V', 'G'):
        info, r, steps = run_touchdown(mode)
        rows.append((mode, info, r, steps))
        print("[%s] landed=%s crashed=%s steps=%d" % (mode, info.get('landed'), info.get('crashed'), steps))
        print("    接地垂直速度=%s m/s | 接地水平速度=%s m/s | 净漂移=%.1f m | 累计奖励=%.1f"
              % (fmt(info.get('touchdown_vs')), fmt(info.get('touchdown_hs_mps')),
                 info.get('net_drift_m', -1), r))
        print("    success=%s  (判据: 软接地 + 漂移<30m + 接地水平速度<2m/s)" % info.get('success'))

    (_, vi, vr, _), (_, gi, gr, _) = rows
    v_hs, g_hs = vi.get('touchdown_hs_mps'), gi.get('touchdown_hs_mps')
    print("-" * 88)
    ok1 = vi.get('landed') and vi.get('touchdown_vs') is not None and vi['touchdown_vs'] > -3.0
    print("检查 1: V 软接地达成 (td_vs=%s > -3)                    -> %s"
          % (fmt(vi.get('touchdown_vs')), "PASS" if ok1 else "FAIL"))
    ok2 = (vi.get('success') is False) and (v_hs is not None and v_hs >= 2.0)
    print("检查 2: 旧判据会放行的滑翔式接地被新判据拒绝            -> %s" % ("PASS" if ok2 else "FAIL"))
    print("        (V 接地水平速度 %s m/s >= 2.0, 旧判据只看 vz>-3 会判 success — 即用户目击的滑翔降落)"
          % fmt(v_hs))
    ok3 = (gi.get('success') is False)
    print("检查 3: G 滑翔被拒绝 (success=False)                    -> %s" % ("PASS" if ok3 else "FAIL"))
    ok4 = (vr - gr) > 0
    print("检查 4: 奖励区分 R_V-R_G = %.1f > 0                     -> %s" % (vr - gr, "PASS" if ok4 else "FAIL"))
    verdict = all((ok1, ok2, ok3, ok4))
    print("=> 判定: %s" % ("PASS — 新判据在真实接地时刻正确区分垂直/滑翔, 滑翔受罚" if verdict else "FAIL — 需复核"))
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
