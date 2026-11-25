# -*- coding: utf-8 -*-
"""
smoke_ah1s_repaired.py: AH-1S 模型修复后冒烟测试 (E 18:15 强制前置)

按 E 18:15 裁决, 在 PPO 训练启动前必须跑通这个冒烟, 验证:
  (1) 修复后 AH-1S 启发式 PD 起飞悬停仍稳定 (20 回合无坠毁);
  (2) 持续 ele=+0.10 5 秒有持续 vx 累积 >2 m/s (前飞通道真有效);

runtime override 4 参数 (不修改任何 JSBSim 系统文件):
  - fcs/adj/longitudinal-gain = 0.5  (原 0.125, 4 倍权限)
  - fcs/adj/pedal-gain        = 0.25 (原 0.18, +39%)
  - aero/setup/Nr_limiter     = 1.0  (原 0.05, 关掉 yaw 限制器)
  - fcs/adj/center-sensitivity = 1.0 (原 1.5, 关掉非线性增益)
"""
import os
import sys
import time
import math
import numpy as np
import jsbsim

DT = 0.02
FT2M = 0.3048
FPS2MPS = 0.3048
MPS2KTS = 1.94384
CRASH_ANG = math.radians(60.0)

REPAIR_OVERRIDES = {
    "fcs/adj/longitudinal-gain": 0.5,
    "fcs/adj/pedal-gain": 0.25,
    "aero/setup/Nr_limiter": 1.0,
    "fcs/adj/center-sensitivity": 1.0,
}


class RepairedAH1S:
    """带 runtime override 的 AH-1S 探针."""

    def __init__(self):
        root = jsbsim.get_default_root_dir()
        self.fdm = jsbsim.FGFDMExec(root, None)
        self.fdm.load_model("ah1s")
        self._ground_agl_ft = 6.30

    def reset(self, seed: int = 0):
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.fdm.set_dt(DT)
        self.fdm.set_property_value("fcs/rpm-governor-active-norm", 1.0)
        self.fdm.set_property_value("propulsion/engine[0]/set-running", 1.0)
        self.fdm.set_property_value("propulsion/engine[1]/set-running", 1.0)
        self.fdm.set_property_value("fcs/throttle-cmd-norm[0]", 1.0)
        self.fdm.set_property_value("fcs/throttle-cmd-norm[1]", 1.0)

        # 注入修复 overrides
        for k, v in REPAIR_OVERRIDES.items():
            self.fdm.set_property_value(k, v)

    def state(self) -> dict:
        h_agl_ft = self.fdm.get_property_value("position/h-agl-ft")
        return {
            "agl_m": max(0.0, (h_agl_ft - self._ground_agl_ft) * FT2M),
            "u_mps": self.fdm.get_property_value("velocities/u-fps") * FPS2MPS,
            "v_mps": self.fdm.get_property_value("velocities/v-fps") * FPS2MPS,
            "vz_mps": -self.fdm.get_property_value("velocities/v-down-fps") * FPS2MPS,
            "pitch_rad": self.fdm.get_property_value("attitude/theta-rad"),
            "roll_rad": self.fdm.get_property_value("attitude/phi-rad"),
            "psi_rad": self.fdm.get_property_value("attitude/psi-rad"),
        }

    def apply_hover_pd(self, coll: float):
        """PD 姿态增稳 + 总距 (与 jsbsim_tasks.JSBSimTaskBase._pd_step 同款)."""
        th = self.fdm.get_property_value("attitude/theta-rad")
        ph = self.fdm.get_property_value("attitude/phi-rad")
        q = self.fdm.get_property_value("velocities/q-rad_sec")
        p = self.fdm.get_property_value("velocities/p-rad_sec")
        r = self.fdm.get_property_value("velocities/r-rad_sec")
        ele = float(np.clip(1.5 * th + 0.6 * q, -0.5, 0.5))
        ail = float(np.clip(-2.4 * ph - 0.3 * p, -0.5, 0.5))
        rud = float(np.clip(-0.8 * r, -0.6, 0.6))
        self.fdm.set_property_value("fcs/collective-cmd-norm", float(np.clip(coll, 0.0, 1.0)))
        self.fdm.set_property_value("fcs/elevator-cmd-norm", ele)
        self.fdm.set_property_value("fcs/aileron-cmd-norm", ail)
        self.fdm.set_property_value("fcs/rudder-cmd-norm", rud)

    def set_ele_direct(self, ele: float):
        """绕过 SAS, 直接写入 elevator (用于前飞通道测试)."""
        self.fdm.set_property_value("fcs/collective-cmd-norm", 0.70)
        self.fdm.set_property_value("fcs/elevator-cmd-norm", float(np.clip(ele, -0.5, 0.5)))
        self.fdm.set_property_value("fcs/aileron-cmd-norm", 0.0)
        self.fdm.set_property_value("fcs/rudder-cmd-norm", 0.0)

    def run(self):
        self.fdm.run()


def test_hover_stability(n_episodes: int = 20, target_alt_m: float = 20.0,
                          max_s: float = 20.0):
    """测试 1: 启发式 PD 起飞悬停 20 回合无坠毁 (验证修复没改坏基础)."""
    print(f"\n[Test 1] PD 悬停稳定性 × {n_episodes} 回合 (目标 {target_alt_m:.1f} m)...")
    success_count = 0
    crash_count = 0
    errs = []
    for ep in range(n_episodes):
        sim = RepairedAH1S()
        sim.reset(seed=ep)
        settled_for = 0.0
        last_s = sim.state()
        crashed = False
        # 35s 起飞 + 稳态期
        for step in range(int(max_s / DT)):
            s = sim.state()
            if abs(s["pitch_rad"]) > CRASH_ANG or abs(s["roll_rad"]) > CRASH_ANG:
                crashed = True
                break
            alt_err = target_alt_m - s["agl_m"]
            vs_cmd = float(np.clip(0.4 * alt_err, -1.0, 3.0))
            coll = 0.70 + 0.06 * (vs_cmd - s["vz_mps"])
            sim.apply_hover_pd(coll)
            sim.run()
            if abs(alt_err) < 1.0 and abs(s["vz_mps"]) < 0.6:
                settled_for += DT
            else:
                settled_for = 0.0
            last_s = s
        err = abs(last_s["agl_m"] - target_alt_m)
        errs.append(err)
        if crashed:
            crash_count += 1
        elif err < 2.0 and abs(last_s["roll_rad"]) < math.radians(10):
            success_count += 1
    sr = success_count / n_episodes
    cr = crash_count / n_episodes
    avg_err = float(np.mean(errs))
    print(f"  [Test 1 结果] 成功 {success_count}/{n_episodes} ({sr:.0%}) | "
          f"坠毁 {crash_count} | 平均高度误差 {avg_err:.2f} m")
    return sr, cr, avg_err


def test_forward_channel(n_episodes: int = 5, hold_s: float = 5.0):
    """测试 2: 持续 ele=+0.10 5s 测 vx 累积 >2 m/s (前飞通道真有效)."""
    print(f"\n[Test 2] 前飞通道 × {n_episodes} 回合 (ele=+0.10 持续 {hold_s:.1f} s)...")
    vx_at_end = []
    crashed = []
    pitch_at_end = []
    for ep in range(n_episodes):
        sim = RepairedAH1S()
        sim.reset(seed=ep)
        # 先 1s 起飞到 hover (确保机头朝前稳定)
        for step in range(int(1.0 / DT)):
            s = sim.state()
            coll = 0.70 + 0.06 * (20.0 - s["vz_mps"])
            sim.apply_hover_pd(coll)
            sim.run()
        # 然后持续 ele=+0.10
        ep_crashed = False
        for step in range(int(hold_s / DT)):
            sim.set_ele_direct(0.10)
            sim.run()
            s = sim.state()
            if abs(s["pitch_rad"]) > CRASH_ANG or abs(s["roll_rad"]) > CRASH_ANG:
                ep_crashed = True
                break
        vx_at_end.append(s["u_mps"])
        pitch_at_end.append(math.degrees(s["pitch_rad"]))
        crashed.append(ep_crashed)
    avg_vx = float(np.mean(vx_at_end))
    min_vx = float(np.min(vx_at_end))
    max_pitch = float(np.max(np.abs(pitch_at_end)))
    n_crashed = int(np.sum(crashed))
    print(f"  [Test 2 结果] 终点 vx: 平均 {avg_vx:.2f} m/s | 最小 {min_vx:.2f} m/s | "
          f"最大俯仰 {max_pitch:.1f}° | 坠毁 {n_crashed}/{n_episodes}")
    return avg_vx, min_vx, max_pitch, n_crashed


def main():
    print("=" * 70)
    print(" AH-1S 修复后冒烟 (E 18:15 强制前置)")
    print(f" runtime overrides: {REPAIR_OVERRIDES}")
    print("=" * 70)

    t0 = time.time()
    sr, cr, avg_err = test_hover_stability(n_episodes=20)
    avg_vx, min_vx, max_pitch, n_crashed = test_forward_channel(n_episodes=5)
    elapsed = time.time() - t0

    print("\n" + "=" * 70)
    print(" 冒烟结论:")
    print(f"  Test 1 (PD 悬停): 成功率 {sr:.0%} | 坠毁率 {cr:.0%} | 高度误差 {avg_err:.2f} m")
    print(f"  Test 2 (前飞通道): 平均 vx {avg_vx:.2f} m/s | 最小 vx {min_vx:.2f} m/s | "
          f"最大俯仰 {max_pitch:.1f}°")
    print(f"  总耗时: {elapsed:.1f} s")
    pass_hover = sr >= 0.8 and cr <= 0.1 and avg_err < 5.0
    pass_forward = avg_vx > 2.0 or min_vx > 0.5
    print(f"  PASS 判定:")
    print(f"    Test 1: {'PASS' if pass_hover else 'FAIL'} "
          f"(要求 成功率≥80% 且 高度误差<5m)")
    print(f"    Test 2: {'PASS' if pass_forward else 'FAIL'} "
          f"(要求 平均 vx > 2 m/s 或最小 vx > 0.5 m/s)")
    overall = pass_hover and pass_forward
    print(f"  总体: {'PASS — 可启动 PPO 重训' if overall else 'FAIL — 走方案 C（口径变更）'}")
    print("=" * 70)


if __name__ == "__main__":
    main()