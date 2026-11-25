#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arm/kuka_iiwa_model.py — 轨道 D · D1 机械臂本体建模（PyBullet KUKA iiwa）

定位
----
本模块是 `interfaces/action-space.md` **v1 契约的参考实现**（Reference Implementation）。
契约层使用"归一化关节坐标" j ∈ [-1.5, 1.5]，与 helo_sim_final.py / rl_train 的
arm 模式保持一致；本模块负责把归一化坐标落到 PyBullet 内置 KUKA iiwa URDF 的
真实关节空间，并实现 v1 新增的：安全软限位、关节角速率上限、控制延迟模型、急停语义。

真实臂型号确定后，仅替换本文件的 URDF 路径与标定参数（见契约 §7 标定流程），
契约映射公式本身不变。

层级关系（只依赖指向，不反向依赖）
----------------------------------
    RL 策略 (轨道 A, 7 维动作)
        └─> KukaIiwaArm.set_target_norm(j[7])        归一化目标角, 软限位钳位
              └─> step(dt)                            延迟 + 速率限制 + P 跟踪 → PyBullet
                    └─> get_controls()                契约映射 → 直升机 4 维控制量 (轨道 A 消费)

不包含任何训练代码（轨道 D 军规禁区）。

用法
----
    from arm.kuka_iiwa_model import KukaIiwaArm
    arm = KukaIiwaArm()                # DIRECT 模式（无 GUI）
    arm.set_target_norm([1.0, 0, 0, 0, 0, 0, 0])
    for _ in range(50):
        arm.step()                     # 每次推进一个控制周期（默认 0.02 s）
    c, cp, cr, pd = arm.get_controls()

    自检：python arm/kuka_iiwa_model.py
"""

import math
import sys

import numpy as np

try:
    import pybullet as p
    import pybullet_data
except ImportError:  # pragma: no cover
    raise SystemExit("[arm] 缺少 pybullet：pip install pybullet")

# ============================================================================
# 契约常量（与 interfaces/action-space.md v1 一一对应；改契约必须同步这里）
# ============================================================================

N_JOINTS = 7
J_NORM_MAX = 1.5            # 归一化关节坐标硬限位（契约动作域边界）
URDF_REL_PATH = "kuka_iiwa/model.urdf"   # PyBullet 内置 KUKA iiwa（7 自由度）

# --- 契约 §3.1 限位 ---
# 软限位（执行层目标角钳位）。j0 不收紧：collective 满程需要 j0 触达 ±1.5。
# j1..j6 收紧到 1.45：这些通道线性有效区只有 |j| ≤ 0.8（见映射），收紧无功能损失。
SOFT_LIMIT = np.array([1.50, 1.45, 1.45, 1.45, 1.45, 1.45, 1.45])

# --- 契约 §3.2 速率上限（归一化坐标 unit/s）---
# 缺省 1.5 unit/s → 全行程 1.0 s；数值量级对齐 KUKA iiwa14 手册 A1~A3 ≈ 85°/s ≈ 1.48 rad/s。
# 真实臂确定后按标定流程（契约 §7）覆盖。
JOINT_RATE_MAX = 1.5

# --- 契约 §3.3 控制延迟模型 ---
# v1 缺省 L1 档：hold_steps 个控制周期零阶保持 + 一阶跟踪。
# 一阶跟踪沿用 helo_sim_final.VirtualRobotArm 的 P 形式 ĵ = SPEED * (j* − j)，
# 等效时间常数 tau_eq = 1 / SPEED = 0.5 s。
SPEED = 2.0                 # P 控制器增益（1/s），与既有实现一致，保证 v0 行为可复现
TAU_EQ = 1.0 / SPEED
HOLD_STEPS = 1              # 零阶保持的控制周期数（可按标定覆盖）

# --- 契约 §5 急停 ---
ESD_GAIN = 0.5              # 硬停时的降级 P 增益

# 契约 §4 映射：cyclic/pedal 通道线性有效区半宽
CYCLIC_HALF_SPAN = 0.8


# ============================================================================
# 归一化坐标 <-> URDF 真实关节坐标
# ============================================================================

def norm_to_q(j_norm, q_lower, q_upper):
    """归一化坐标 j ∈ [-J_NORM_MAX, J_NORM_MAX] → 真实关节角 q ∈ [lower, upper]（线性满程）。"""
    mid = 0.5 * (q_lower + q_upper)
    half = 0.5 * (q_upper - q_lower)
    return mid + (np.asarray(j_norm) / J_NORM_MAX) * half


def q_to_norm(q, q_lower, q_upper):
    """真实关节角 → 归一化坐标（norm_to_q 的逆）。"""
    mid = 0.5 * (q_lower + q_upper)
    half = 0.5 * (q_upper - q_lower)
    return (np.asarray(q) - mid) / half * J_NORM_MAX


# ============================================================================
# 契约 §4 映射：归一化关节角 → 直升机控制量（与 helo_sim_final.py L193-203 公式一致）
# ============================================================================

def map_joints_to_controls(joints):
    """
    契约映射（v1，公式与 v0/既有实现完全一致，仅正式化语义）：

        collective     = clip((j0 + 1.5) / 3.0, 0, 1)
        cyclic_pitch   = clip(j1 / 0.8, -1, 1)
        cyclic_roll    = clip(j2 / 0.8, -1, 1)
        pedals         = clip(j3 / 0.8, -1, 1)
        j4..j6         预留，无输出贡献

    返回 (collective, cyclic_pitch, cyclic_roll, pedals)，均为 float。
    """
    j = np.asarray(joints, dtype=float)[:4]
    collective = float(np.clip((j[0] + J_NORM_MAX) / (2.0 * J_NORM_MAX), 0.0, 1.0))
    cyclic_pitch = float(np.clip(j[1] / CYCLIC_HALF_SPAN, -1.0, 1.0))
    cyclic_roll = float(np.clip(j[2] / CYCLIC_HALF_SPAN, -1.0, 1.0))
    pedals = float(np.clip(j[3] / CYCLIC_HALF_SPAN, -1.0, 1.0))
    return collective, cyclic_pitch, cyclic_roll, pedals


# ============================================================================
# D1 本体模型
# ============================================================================

class KukaIiwaArm:
    """PyBullet KUKA iiwa 机械臂（契约 v1 参考实现）。

    Parameters
    ----------
    use_gui : bool     True 时开 GUI 窗口（演示用）；默认 DIRECT 无头。
    control_dt : float 控制周期（s）。rl_train arm 模式为 0.02（50 Hz）。
    rate_max : float   关节角速率上限（归一化坐标 unit/s），标定可覆盖。
    hold_steps : int   控制延迟的零阶保持周期数。
    """

    def __init__(self, use_gui=False, control_dt=0.02,
                 rate_max=JOINT_RATE_MAX, hold_steps=HOLD_STEPS,
                 motor_force=200.0):
        self.control_dt = float(control_dt)
        self.rate_max = float(rate_max)
        self.hold_steps = max(1, int(hold_steps))
        self.motor_force = float(motor_force)   # 关节力矩限幅（N·m），技能执行器可临时覆盖

        self.client = p.connect(p.GUI if use_gui else p.DIRECT)
        p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=self.client)
        p.setTimeStep(self.control_dt, physicsClientId=self.client)
        p.setGravity(0, 0, -9.81, physicsClientId=self.client)

        self.body_id = p.loadURDF(URDF_REL_PATH, basePosition=[0, 0, 0],
                                  useFixedBase=True, physicsClientId=self.client)

        # 收集 7 个转动关节并读取 URDF 限位（不硬编码，以 URDF 为准）
        self.joint_ids = []
        self.joint_names = []
        self.q_lower = []
        self.q_upper = []
        self.q_urdf_vel = []   # URDF 声明的最大关节速度（rad/s），仅记录参考
        for i in range(p.getNumJoints(self.body_id, physicsClientId=self.client)):
            info = p.getJointInfo(self.body_id, i, physicsClientId=self.client)
            if info[2] == p.JOINT_REVOLUTE:
                self.joint_ids.append(i)
                self.joint_names.append(info[1].decode("utf-8"))
                self.q_lower.append(info[8])
                self.q_upper.append(info[9])
                self.q_urdf_vel.append(info[11])
        assert len(self.joint_ids) == N_JOINTS, (
            f"[arm] {URDF_REL_PATH} 转动关节数 {len(self.joint_ids)} != {N_JOINTS}")

        self.q_lower = np.array(self.q_lower, dtype=float)
        self.q_upper = np.array(self.q_upper, dtype=float)
        self.q_urdf_vel = np.array(self.q_urdf_vel, dtype=float)

        # 运行状态（全部为归一化坐标）
        self.joints = np.zeros(N_JOINTS)
        self.joint_targets = np.zeros(N_JOINTS)
        self._staged_target = None    # set_target_norm 暂存的新目标
        self._hold_counter = 0        # 剩余保持周期数
        self.prev_joints = self.joints.copy()
        self.esd_level = 0            # 0=正常 1=软停 2=硬停 3=预留
        self.warnings = []            # 累积告警（供 safety.md 的异常检测消费）

    # ---------------- 目标设置（契约 §3.1） ----------------

    def set_target_norm(self, target):
        """设置归一化目标角（7 维）。无效值丢弃并告警；越软限位钳位并告警。"""
        t = np.asarray(target, dtype=float).copy()
        if t.shape != (N_JOINTS,) or not np.all(np.isfinite(t)):
            self._warn("invalid_target", f"丢弃无效目标 {target!r}")
            return False
        if self.esd_level >= 2:
            self._warn("esd_active", "急停激活中，目标被忽略（须先 clear_estop）")
            return False
        over = np.abs(t) > SOFT_LIMIT
        if np.any(over):
            self._warn("soft_limit", f"目标越软限位已钳位: idx={np.where(over)[0].tolist()}")
            t = np.clip(t, -SOFT_LIMIT, SOFT_LIMIT)
        self._staged_target = t
        self._hold_counter = self.hold_steps
        return True

    # ---------------- 步进（契约 §3.2 速率 / §3.3 延迟） ----------------

    def step(self, dt=None):
        """推进一个控制周期：延迟保持 → P 跟踪 → 速率钳位 → PyBullet 执行。"""
        dt = self.control_dt if dt is None else float(dt)

        if self.esd_level >= 2:
            # 硬停：目标冻结为当前角，降级增益
            self.joint_targets = self.joints.copy()
            gain = ESD_GAIN
        elif self.esd_level == 1:
            # 软停：目标冻结，增益不变
            self.joint_targets = self.joints.copy()
            gain = SPEED
        else:
            if self._hold_counter > 0:
                self._hold_counter -= 1          # 延迟周期：新目标尚未生效
            elif self._staged_target is not None:
                self.joint_targets = self._staged_target
            gain = SPEED

        # P 跟踪（与既有实现同形式）+ 速率钳位（契约 §3.2）
        prev = self.joints.copy()
        jdot = gain * (self.joint_targets - self.joints)
        jdot = np.clip(jdot, -self.rate_max, self.rate_max)
        self.joints = np.clip(self.joints + jdot * dt, -J_NORM_MAX, J_NORM_MAX)

        # 速率超限检测（异常检测的一部分，正常情况下钳位后不应触发）
        actual_rate = np.abs(self.joints - prev) / dt
        if np.any(actual_rate > self.rate_max * 1.05):
            self._warn("rate_exceed", f"速率超限: max={actual_rate.max():.2f} unit/s")

        # 落到 PyBullet 真实关节并步进
        q_cmd = norm_to_q(self.joints, self.q_lower, self.q_upper)
        for k, jid in enumerate(self.joint_ids):
            p.setJointMotorControl2(self.body_id, jid, p.POSITION_CONTROL,
                                    targetPosition=float(q_cmd[k]),
                                    force=self.motor_force, physicsClientId=self.client)
        p.stepSimulation(physicsClientId=self.client)

        self.last_q_actual = self.get_q_actual()
        return self.joints.copy()

    # ---------------- 状态读取 ----------------

    def get_q_actual(self):
        """URDF 真实关节角（rad）。"""
        states = p.getJointStates(self.body_id, self.joint_ids, physicsClientId=self.client)
        return np.array([s[0] for s in states], dtype=float)

    def get_joint_states(self):
        """契约归一化关节角（7 维）——与 helo_sim_final.VirtualRobotArm.get_joint_states 同语义。"""
        return self.joints.copy()

    # ---------------- 契约 §4 映射 ----------------

    def get_controls(self):
        """当前关节角 → 直升机控制量（4 维）。

        硬停（L2）时输出被覆盖为安全值：总距保持当前值（避免瞬间掉高），
        姿态/航向指令全部归零 —— 契约 §5 急停语义。
        """
        c, cp, cr, pd = map_joints_to_controls(self.joints)
        if self.esd_level >= 2:
            return c, 0.0, 0.0, 0.0
        return c, cp, cr, pd

    # ---------------- 契约 §5 急停 ----------------

    def estop(self, level=2):
        """触发急停。level=1 软停（冻结目标，保持输出）；level=2 硬停（冻结+降增益+姿态指令归零）。"""
        if level not in (1, 2, 3):
            raise ValueError("esd level ∈ {1,2,3}")
        self.esd_level = max(self.esd_level, level)
        self._staged_target = None
        self._warn("estop", f"急停 L{self.esd_level} 触发")

    def clear_estop(self):
        """解除急停：仅当全部关节回到软限位内才允许。返回是否成功。"""
        if np.any(np.abs(self.joints) > SOFT_LIMIT + 1e-9):
            self._warn("esd_clear_denied", "关节越软限位，拒绝解除急停")
            return False
        self.esd_level = 0
        self.joint_targets = self.joints.copy()
        self._staged_target = None
        self._hold_counter = 0
        return True

    # ---------------- 生命周期 ----------------

    def reset(self):
        """复位到中位（全零目标），清告警与急停。"""
        self.joints = np.zeros(N_JOINTS)
        self.joint_targets = np.zeros(N_JOINTS)
        self._staged_target = None
        self._hold_counter = 0
        self.esd_level = 0
        self.warnings.clear()
        q_mid = norm_to_q(np.zeros(N_JOINTS), self.q_lower, self.q_upper)
        for k, jid in enumerate(self.joint_ids):
            p.resetJointState(self.body_id, jid, float(q_mid[k]), 0.0,
                              physicsClientId=self.client)

    def close(self):
        if p.isConnected(self.client):
            p.disconnect(physicsClientId=self.client)

    # ---------------- 内部 ----------------

    def _warn(self, code, msg):
        self.warnings.append((code, msg))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ============================================================================
# 自检（python arm/kuka_iiwa_model.py）——非训练代码，仅验证建模与契约一致性
# ============================================================================

def _self_test():
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    print("=" * 62)
    print("  arm/kuka_iiwa_model.py 自检（契约 action-space v1 参考实现）")
    print("=" * 62)

    arm = KukaIiwaArm(control_dt=0.02)

    # T1 关节信息与坐标映射
    print("\n[T1] URDF 关节限位（真实 rad）与归一化坐标映射")
    for k in range(N_JOINTS):
        print(f"    j{k} {arm.joint_names[k]:<20} "
              f"[{arm.q_lower[k]:+.3f}, {arm.q_upper[k]:+.3f}] rad  "
              f"urdf_vel={arm.q_urdf_vel[k]:.2f} rad/s")
    q_plus = norm_to_q(np.full(N_JOINTS, 1.5), arm.q_lower, arm.q_upper)
    check("T1a j=+1.5 → URDF upper", np.allclose(q_plus, arm.q_upper))
    check("T1b j=0 → 行程中位",
          np.allclose(norm_to_q(np.zeros(N_JOINTS), arm.q_lower, arm.q_upper),
                      0.5 * (arm.q_lower + arm.q_upper)))
    check("T1c 往返映射一致",
          np.allclose(q_to_norm(q_plus, arm.q_lower, arm.q_upper),
                      np.full(N_JOINTS, 1.5)))

    # T2 契约映射公式（对照 helo_sim_final L193-203）
    print("\n[T2] 契约映射公式")
    c, cp, cr, pd = map_joints_to_controls([1.5, 0.8, -0.8, 0.8, 0, 0, 0])
    check("T2a j0=+1.5 → collective=1.0", abs(c - 1.0) < 1e-12, f"c={c}")
    check("T2b j1=+0.8 → pitch=+1.0", abs(cp - 1.0) < 1e-12, f"cp={cp}")
    check("T2c j2=-0.8 → roll=-1.0", abs(cr + 1.0) < 1e-12, f"cr={cr}")
    check("T2d j3=+0.8 → pedals=+1.0", abs(pd - 1.0) < 1e-12, f"pd={pd}")
    c0 = map_joints_to_controls([0, 0, 0, 0, 0, 0, 0])
    check("T2e 全零 → collective=0.5 其余 0",
          abs(c0[0] - 0.5) < 1e-12 and max(abs(v) for v in c0[1:]) == 0.0)
    sat = map_joints_to_controls([1.5, 1.5, -1.5, 1.5, 0, 0, 0])
    check("T2f 饱和区截断到 [0,1]/[-1,1]",
          sat == (1.0, 1.0, -1.0, 1.0), f"{sat}")

    # T3 阶跃跟踪（速率限制下全行程时间）
    print("\n[T3] 阶跃跟踪 + 速率钳位（j0: 0 → +1.5, dt=0.02, rate_max=1.5）")
    arm.reset()
    arm.set_target_norm([1.5, 0, 0, 0, 0, 0, 0])
    prev_j = float(arm.joints[0])
    rates = []
    t_arrive = None
    for i in range(150):  # 最长 3 s
        arm.step()
        rates.append(abs(arm.joints[0] - prev_j) / 0.02)
        prev_j = float(arm.joints[0])
        if t_arrive is None and arm.joints[0] >= 1.5 - 0.015:
            t_arrive = (i + 1) * 0.02
    max_rate = max(rates)
    check("T3a 速率 ≤ rate_max×1.05", max_rate <= arm.rate_max * 1.05, f"max={max_rate:.3f} unit/s")
    # 两段式轨迹：匀速段 diff>0.75 (0.5 s) + 指数尾段 jdot=2*diff 到 99% 行程 (ln50/2≈1.96 s)
    check("T3b 全行程(99%)用时 ≈ 2.5 s ± 0.3", t_arrive is not None and abs(t_arrive - 2.5) < 0.3,
          f"t={t_arrive}s")
    for _ in range(50):   # 再给 1 s 让契约层指数逼近到 1.5、URDF 侧 PD 稳态收敛
        arm.step()
    q_act = arm.get_q_actual()
    check("T3c URDF 侧 j0 稳态到达 upper（误差<1e-2 rad）",
          abs(q_act[0] - arm.q_upper[0]) < 1e-2,
          f"err={abs(q_act[0] - arm.q_upper[0]):.2e} rad")
    check("T3d URDF 侧其余关节保持中位（<0.05 rad）",
          bool(np.max(np.abs(q_act[1:] - norm_to_q(0, arm.q_lower[1:], arm.q_upper[1:]))) < 0.05),
          f"max_dev={np.max(np.abs(q_act[1:] - norm_to_q(0, arm.q_lower[1:], arm.q_upper[1:]))):.3f} rad")

    # T4 控制延迟：目标改变后 hold_steps 周期内关节不应启动
    print("\n[T4] 控制延迟（hold_steps=1）")
    arm.reset()
    arm.set_target_norm([-1.5, 0, 0, 0, 0, 0, 0])
    j1 = arm.step()          # 延迟周期：新目标尚未生效
    check("T4a 保持周期内关节未动", abs(j1[0]) < 1e-9, f"j0={j1[0]:.2e}")
    j2 = arm.step()
    check("T4b 保持结束后开始动作", j2[0] < 0.0, f"j0={j2[0]:.4f}")

    # T5 急停语义
    print("\n[T5] 急停")
    arm.reset()
    arm.set_target_norm([0.8, 0, 0, 0, 0, 0, 0])
    for _ in range(60):
        arm.step()
    frozen = arm.joints.copy()
    c_before = arm.get_controls()
    arm.estop(level=2)
    arm.set_target_norm([-1.5, -1.5, -1.5, -1.5, 0, 0, 0])   # 应被忽略
    for _ in range(25):
        arm.step()
    check("T5a 硬停中关节冻结", np.allclose(arm.joints, frozen, atol=0.01),
          f"漂移={np.max(np.abs(arm.joints - frozen)):.3f}")
    c_after = arm.get_controls()
    check("T5b 硬停输出覆盖：总距保持、姿态指令归零",
          abs(c_after[0] - c_before[0]) < 0.01 and max(abs(v) for v in c_after[1:]) == 0.0,
          f"{c_before} → {c_after}")
    check("T5c 急停期间 set_target 拒绝", arm.set_target_norm([0, 0, 0, 0, 0, 0, 0]) is False)
    check("T5d 软限位内允许解除", arm.clear_estop() is True)

    # T6 无效目标与软限位钳位
    print("\n[T6] 无效目标 / 软限位")
    arm.reset()
    check("T6a NaN 目标被丢弃", arm.set_target_norm([float('nan')] * 7) is False)
    ok = arm.set_target_norm([0, 1.5, 0, 0, 0, 0, 0])
    staged = arm._staged_target[1] if arm._staged_target is not None else None
    check("T6b j1 越软限位钳到 1.45", ok and staged is not None and abs(staged - 1.45) < 1e-12,
          f"staged={staged}")
    check("T6c 有软限位告警", any(w[0] == "soft_limit" for w in arm.warnings))

    arm.close()
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("\n" + "=" * 62)
    print(f"  自检结果：{len(results) - n_fail}/{len(results)} 通过"
          + (f"（{n_fail} 项失败）" if n_fail else " —— 全部通过"))
    print("=" * 62)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(_self_test())
