# -*- coding: utf-8 -*-
"""
audit_rewards.py — G1 奖励审计 (验收五道闸之第一道)

用构造轨迹证明奖励函数/成功判据的"等价类缺陷", 不训练任何模型:

  V = "好" 轨迹: 脚本化高度控制器, 不推杆 (尽量垂直起降)
  G = "坏" 轨迹: 同一高度控制器 + 持续前推杆 (故意制造水平漂移的"滑翔"起降)

对每个科目输出并判定:
  (1) G 轨迹是否同样被判 success=True  —— 成功判据是否放行滑翔;
  (2) 累计奖励 R_V 与 R_G 的差距       —— 奖励是否能区分好坏行为;
  (3) 两者的地面水平净位移 / 最大地速  —— 行为层面的量化差异。

FAIL 条件: G 成功 且 (R_V - R_G) < 1.0  (即滑翔起飞/降落几乎不受惩罚, 环境教不会垂直起降)
背景事故: 2026-08-29/30, FG 演示出现滑翔起飞/滑翔降落/悬停漂移 (悬停回放漂移 492m)。

用法:
  python audit_rewards.py                 # 全部科目 (takeoff / landing / hover)
  python audit_rewards.py takeoff hover   # 指定科目
"""
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEED = 42
FT2M = 0.3048
M_PER_DEG_LAT = 111320.0


# ---------- 通用工具 ----------

def _geo(env):
    lat = env.fdm.get_property_value('position/lat-geod-deg')
    lon = env.fdm.get_property_value('position/long-gc-deg')
    vn = env.fdm.get_property_value('velocities/v-north-fps') * FT2M
    ve = env.fdm.get_property_value('velocities/v-east-fps') * FT2M
    return lat, lon, vn, ve


def run_episode(env, action_fn, max_steps, state_fn):
    """跑一个回合, 返回指标字典。action_fn(env_state) -> 4 维动作。"""
    obs, info = env.reset(seed=SEED)
    lat0, lon0, _, _ = _geo(env)
    coslat = math.cos(math.radians(lat0))
    total_r, steps = 0.0, 0
    net2 = 0.0
    path = 0.0
    max_gs = 0.0
    prev = (lat0, lon0)
    terminated = truncated = False
    last_info = info
    while not (terminated or truncated) and steps < max_steps:
        s = state_fn(env)
        obs, r, terminated, truncated, last_info = env.step(np.asarray(action_fn(s), dtype=np.float64))
        total_r += float(r)
        steps += 1
        lat, lon, vn, ve = _geo(env)
        dn = (lat - prev[0]) * M_PER_DEG_LAT
        de = (lon - prev[1]) * M_PER_DEG_LAT * coslat
        path += math.hypot(dn, de)
        net2 = math.hypot((lat - lat0) * M_PER_DEG_LAT,
                          (lon - lon0) * M_PER_DEG_LAT * coslat)
        max_gs = max(max_gs, math.hypot(vn, ve))
        prev = (lat, lon)
    return {
        "success": bool(last_info.get("success", False)),
        "crashed": bool(last_info.get("crashed", False)),
        "reward": round(total_r, 2),
        "steps": steps,
        "net_drift_m": round(net2, 1),
        "path_len_m": round(path, 1),
        "max_groundspeed_mps": round(max_gs, 2),
        "terminal_info": {k: (round(v, 3) if isinstance(v, float) else v)
                          for k, v in last_info.items()
                          if isinstance(v, (int, float, bool))},
    }


def probe_forward_sign(env, state_fn):
    """探测哪个方向的推杆产生前向水平速度, 返回符号 (+1/-1)。"""
    best_sign, best_speed = +1, -1e9
    for sign in (+1, -1):
        obs, info = env.reset(seed=SEED)
        u_sum, n = 0.0, 0
        for i in range(200):  # 4 s
            s = state_fn(env)
            env.step(np.array([0.6 * (1.0 if i > 20 else 0.0), sign * 0.4, 0.0, 0.0]))
            if i > 100:
                u_sum += abs(env.fdm.get_property_value('velocities/v-north-fps')) \
                    + abs(env.fdm.get_property_value('velocities/v-east-fps'))
                n += 1
        speed = u_sum / max(1, n)
        if speed > best_speed:
            best_speed, best_sign = speed, sign
    return best_sign


# ---------- 各科目审计 ----------

def alt_coll_cmd(target, agl, vz):
    """与训练环境启发式一致的爬升/下降高度控制器 (已验证可用的增益)。"""
    vs_cmd = float(np.clip(0.4 * (target - agl), -1.0, 3.0))
    return float(np.clip(0.24 * (vs_cmd - vz), -1.0, 1.0))


def audit_takeoff():
    from jsbsim_tasks import make_task
    env = make_task('takeoff', wind=False)
    sfn = lambda e: e._raw()
    sign = probe_forward_sign(env, sfn)
    target = env.TARGET_ALT_M

    def v_fn(s):
        return [alt_coll_cmd(target, s['agl'], s['vz']), 0.0, 0.0, 0.0]

    def g_fn(s):
        return [alt_coll_cmd(target, s['agl'], s['vz']), sign * 0.45, 0.0, 0.0]

    v = run_episode(env, v_fn, 1000, sfn)
    g = run_episode(env, g_fn, 1000, sfn)
    v["label"], g["label"] = "V=垂直意图", "G=滑翔意图(前推杆%.2f)" % (sign * 0.45)
    env.close() if hasattr(env, 'close') else None
    return "takeoff", [v, g]


def audit_landing():
    from jsbsim_tasks import make_task
    env = make_task('landing', init_alt_m=15.0)  # LandingTask 无 wind 形参, 风场随机但种子固定故确定
    sfn = lambda e: e._raw()
    sign = probe_forward_sign(env, sfn)

    def _descend(s):
        if s['agl'] > 0.4:
            return float(np.clip(0.24 * (-1.2 - s['vz']), -1.0, 1.0))
        return 0.0

    def v_fn(s):
        # v5.2: V=稳定航向+位置保持的垂直下降 (R3 对比对象 = 训练目标行为的可信参照):
        #   总距: 定速下降; 俯仰/滚转: 体轴速度阻尼 (u,v→0 等效位置保持, 与航向无关);
        #   脚蹬: 航向保持 PD。
        hdg_dev = (s['psi'] - getattr(env, 'psi0', s['psi']))
        while hdg_dev > math.pi:
            hdg_dev -= 2 * math.pi
        while hdg_dev < -math.pi:
            hdg_dev += 2 * math.pi
        u_yaw = float(np.clip(-2.0 * hdg_dev - 0.3 * s['r'], -0.5, 0.5))
        theta_des = float(np.clip(0.12 * s['u'], -0.15, 0.15))   # 前滑→抬头刹
        u_pitch = float(np.clip(2.0 * (s['pitch'] - theta_des) - 0.5 * s['q'], -0.6, 0.6))
        phi_des = float(np.clip(-0.30 * s['v'], -0.25, 0.25))    # 侧滑→压坡修正
        u_roll = float(np.clip(2.0 * (phi_des - s['roll']), -0.6, 0.6))
        return [_descend(s), u_pitch, u_roll, u_yaw]

    def g_fn(s):
        base = v_fn(s)
        return [base[0], sign * 0.45, 0.0, 0.0]

    def s_fn(s):
        # v5.2: 打转意图 (持续踩舵) —— 用户令: 滑橇式起落架打转=损坏飞机
        base = v_fn(s)
        return [base[0], 0.0, 0.0, 0.9]

    v = run_episode(env, v_fn, 1200, sfn)
    g = run_episode(env, g_fn, 1200, sfn)
    s = run_episode(env, s_fn, 1200, sfn)
    v["label"] = "V=垂直下降(航向保持)"
    g["label"] = "G=滑翔下降(前推杆%.2f)" % (sign * 0.45)
    s["label"] = "S=打转意图(踩舵0.9)"
    return "landing", [v, g, s]


def audit_hover():
    from jsbsim_helo_env import JSBSimHeloEnv
    env = JSBSimHeloEnv(action_mode="direct", wind=False)
    sfn = lambda e: e._get_raw_state()
    sign = probe_forward_sign(env, sfn)
    obs, info = env.reset(seed=SEED)
    target = info["target_alt"]

    def v_fn(s):
        return [alt_coll_cmd(target, s['agl'], s['vz']), 0.0, 0.0, 0.0]

    def g_fn(s):
        return [alt_coll_cmd(target, s['agl'], s['vz']), sign * 0.45, 0.0, 0.0]

    v = run_episode(env, v_fn, 1000, sfn)
    g = run_episode(env, g_fn, 1000, sfn)
    v["label"], g["label"] = "V=原地悬停意图", "G=漂移悬停(前推杆%.2f)" % (sign * 0.45)
    return "hover", [v, g]


AUDITORS = {"takeoff": audit_takeoff, "landing": audit_landing, "hover": audit_hover}


def main():
    names = [a for a in sys.argv[1:] if a in AUDITORS] or list(AUDITORS)
    out = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
           "method": "scripted V(vertical) vs G(gliding) trajectories, no training",
           "seed": SEED,
           "fail_rules": {
               "R1_reward_equivalence": "G.success and (R_V - R_G) < 1.0  (奖励无法区分好坏行为, 甚至更奖励滑翔)",
               "R2_success_passes_drift": "任一成功轨迹 净漂移 > 50m  (成功判据放行漂移/滑翔)",
               "R3_spin_not_rewarded": "打转轨迹得分不得高于稳定航向轨迹 (v5.2 用户令: 滑橇机打转=损坏)"},
           "audits": {}}
    print("=" * 88)
    print("G1 奖励审计: 构造轨迹对照 (V=垂直意图 vs G=滑翔意图), 无训练, 种子 %d" % SEED)
    print("=" * 88)
    n_fail = 0
    for name in names:
        task, trajs = AUDITORS[name]()
        v = trajs[0]
        delta = round(v["reward"] - trajs[1]["reward"], 2)
        r1 = trajs[1]["success"] and (delta < 1.0)
        drifted = [r["label"] for r in trajs
                   if r["success"] and r["net_drift_m"] > 50.0]
        r2 = bool(drifted)
        # v5.2 R3: 打转轨迹得分不得高于稳定航向轨迹 (打转自毁属预期, 不计 WEAK)
        r3 = False
        s_traj = trajs[2] if len(trajs) > 2 else None
        if s_traj is not None:
            r3 = (not s_traj["crashed"]) and (s_traj["reward"] >= v["reward"])
        if r1 or r2 or r3:
            verdict = "FAIL"
            reason = "".join(["R1" if r1 else "", "+R2" if r2 else "",
                              "+R3" if r3 else ""])
            if r2:
                reason += " (%s 漂移>50m仍判成功)" % "/".join(drifted)
            if r3:
                reason += " (打转轨迹 R_S=%.1f >= 稳定航向 R_V=%.1f)" % (s_traj["reward"], v["reward"])
            n_fail += 1
        elif v["crashed"] or trajs[1]["crashed"]:
            verdict, reason = "WEAK", "轨迹坠毁, 无法判定"
        else:
            verdict, reason = "PASS", ""
        print("\n【%s】" % task)
        for r in trajs:
            print("  %-24s success=%-5s crashed=%-5s R=%8.1f  净漂移=%7.1fm  路径=%7.1fm  最大地速=%5.2fm/s"
                  % (r["label"], r["success"], r["crashed"], r["reward"],
                     r["net_drift_m"], r["path_len_m"], r["max_groundspeed_mps"]))
        print("  奖励差 R_V - R_G = %.2f   成功标签: %s"
              % (delta, " ".join("%s=%s" % (t["label"].split("=")[0], t["success"]) for t in trajs)))
        print("  => 判定: %s %s" % (verdict, reason))
        out["audits"][task] = {"trajectories": trajs, "reward_delta_VG": delta,
                               "verdict": verdict, "reason": reason}

    print("\n" + "=" * 88)
    print("结论: %d/%d 科目 FAIL —— FAIL=环境把'滑翔起降'与'垂直起降'视为同类成功, 奖励无法塑造垂直行为" % (n_fail, len(names)))
    print("=" * 88)
    out["n_fail"] = n_fail
    out["n_audited"] = len(names)
    res_dir = os.path.join(SCRIPT_DIR, "results")
    os.makedirs(res_dir, exist_ok=True)
    fp = os.path.join(res_dir, "audit_g1_rewards_%s.json" % time.strftime("%Y%m%dT%H%M%S"))
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("证据已落盘: %s" % fp)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
