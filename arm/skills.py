#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arm/skills.py — 轨道 D · D4 驾驶动作原子技能库

对应验收（里程碑验收映射表.md §1 需求2 / §4-2）
------------------------------------------------
- 标准动作原子技能库：推拉杆、提放总距、脚蹬联动、按压开关、旋钮开关等 ≥5 种
  （本库实现 **8 种**），每个技能 = **参数化原语（目标关节角序列 + 力矩限幅）**；
- 分层映射：高层语义指令 → 技能序列 → 关节目标 → 执行器力矩；
- 验收指标"操控映射准确率 ≥90%"的载体：run() 输出逐技能"到位 + 时序"成功率，
  判定口径与验收方法草案一致（到位且时序正确）。

分层结构
--------
    语义层   compile_semantic("climb")          → SkillSequence
    技能层   RaiseLowerCollective(amount=+0.5)  → 关键帧序列 (duration, j_target[7])
    执行层   SkillSequence.run(arm)             → set_target_norm 主链路（速率/限位/急停
                                                    包络自动生效，契约 action-space v1 衔接）
                                                    + arm.motor_force = 技能力矩限幅

与 D2 契约的关系：技能库是契约 §1.2 动作空间之上的**组合层**，不改变契约任何数值；
技能目标角一律落在契约归一化坐标，操纵通道有效区遵循契约 §4（j0 全程、j1–j3 |j|≤0.8）。

自检：python arm/skills.py
不含训练代码（轨道 D 禁区）。
"""

import sys
import time

import numpy as np

from kuka_iiwa_model import SOFT_LIMIT, N_JOINTS, KukaIiwaArm

# 操纵通道索引（契约 §4）
CH_COLLECTIVE, CH_PITCH, CH_ROLL, CH_PEDAL = 0, 1, 2, 3
# 座舱末端通道（契约预留，不接操纵映射；开关/旋钮动作专用）
CH_SWITCH, CH_KNOB = 5, 6
# 操纵通道线性有效区（契约 §4：j0 全程 ±1.5，j1–j3 ±0.8）
EFFECTIVE_LIMIT = np.array([1.5, 0.8, 0.8, 0.8, 1.45, 1.45, 1.45])


# ============================================================================
# 技能基类：参数化原语
# ============================================================================

class Skill:
    """原子技能基类。

    子类实现 keyframes(j_now)，返回 [(duration_s, target_j[7]), ...]。
    duration 为该帧的**期望到达用时**（s），执行器据其做时序判定与超时。
    """
    name = "skill_base"

    def __init__(self, torque_limit=200.0, settle_tol=0.05):
        self.torque_limit = float(torque_limit)   # N·m，执行期覆盖 arm.motor_force
        self.settle_tol = float(settle_tol)       # 到位判定容差（unit）

    def keyframes(self, j_now):
        raise NotImplementedError

    def validate(self, j_now):
        """关键帧合法性：维度、有限值、软限位。返回 (ok, msg)。"""
        for dur, tgt in self.keyframes(j_now):
            t = np.asarray(tgt, dtype=float)
            if t.shape != (N_JOINTS,) or not np.all(np.isfinite(t)):
                return False, "keyframe invalid (shape/NaN)"
            if dur <= 0:
                return False, "duration must be > 0"
            if np.any(np.abs(t) > SOFT_LIMIT + 1e-9):
                return False, f"target beyond soft limit: {np.abs(t).max():.2f}"
        return True, ""


def _frame(j_now, changes, duration):
    """构造单关键帧：未指定通道保持 j_now。changes: {channel: value}"""
    tgt = np.asarray(j_now, dtype=float).copy()
    for ch, v in changes.items():
        tgt[ch] = v
    return (float(duration), tgt)


# ============================================================================
# 8 种原子技能
# ============================================================================

class PushPullCyclic(Skill):
    """① 推拉杆（俯仰，j1）。amount>0 = 低头推杆（helo_sim_final 语义：正 pitch=低头）。"""
    name = "push_pull_cyclic"

    def __init__(self, amount=0.4, duration=1.5, **kw):
        super().__init__(**kw)
        self.amount = float(np.clip(amount, -EFFECTIVE_LIMIT[CH_PITCH], EFFECTIVE_LIMIT[CH_PITCH]))
        self.duration = float(duration)

    def keyframes(self, j_now):
        return [_frame(j_now, {CH_PITCH: self.amount}, self.duration)]


class RaiseLowerCollective(Skill):
    """② 提放总距（j0）。amount ∈ [-1.5, 1.5] 全程线性（契约 §4 无饱和通道）。"""
    name = "raise_lower_collective"

    def __init__(self, amount=0.5, duration=1.5, **kw):
        super().__init__(**kw)
        self.amount = float(np.clip(amount, -EFFECTIVE_LIMIT[CH_COLLECTIVE], EFFECTIVE_LIMIT[CH_COLLECTIVE]))
        self.duration = float(duration)

    def keyframes(self, j_now):
        return [_frame(j_now, {CH_COLLECTIVE: self.amount}, self.duration)]


class SidePushCyclic(Skill):
    """③ 左右压杆（滚转，j2）。amount>0 = 左滚（helo_sim_final 测试场景语义）。"""
    name = "side_push_cyclic"

    def __init__(self, amount=0.3, duration=1.5, **kw):
        super().__init__(**kw)
        self.amount = float(np.clip(amount, -EFFECTIVE_LIMIT[CH_ROLL], EFFECTIVE_LIMIT[CH_ROLL]))
        self.duration = float(duration)

    def keyframes(self, j_now):
        return [_frame(j_now, {CH_ROLL: self.amount}, self.duration)]


class PedalStep(Skill):
    """④ 脚蹬联动（尾桨，j3）。amount>0 = 右蹬（对应 pedals 正向）。"""
    name = "pedal_step"

    def __init__(self, amount=0.4, duration=1.5, **kw):
        super().__init__(**kw)
        self.amount = float(np.clip(amount, -EFFECTIVE_LIMIT[CH_PEDAL], EFFECTIVE_LIMIT[CH_PEDAL]))
        self.duration = float(duration)

    def keyframes(self, j_now):
        return [_frame(j_now, {CH_PEDAL: self.amount}, self.duration)]


class CenterChannel(Skill):
    """⑤ 杆量回中：选定通道回到 0。channels 可为索引列表或 "all"（j0–j3 全回中）。"""
    name = "center_channel"

    def __init__(self, channels="all", duration=1.5, **kw):
        super().__init__(**kw)
        if channels == "all":
            channels = [CH_COLLECTIVE, CH_PITCH, CH_ROLL, CH_PEDAL]
        self.channels = list(channels)
        self.duration = float(duration)

    def keyframes(self, j_now):
        changes = {ch: 0.0 for ch in self.channels}
        return [_frame(j_now, changes, self.duration)]


class HoldPose(Skill):
    """⑥ 姿态保持：冻结当前关节角 duration 秒（时序等待，不产生新目标）。"""
    name = "hold_pose"

    def __init__(self, duration=1.0, **kw):
        super().__init__(**kw)
        self.duration = float(duration)

    def keyframes(self, j_now):
        return [_frame(j_now, {}, self.duration)]


class PressSwitch(Skill):
    """⑦ 按压开关（j5 末端，非操纵通道）：下压 depth → 保持 press_time → 回弹原位。"""
    name = "press_switch"

    def __init__(self, depth=0.3, press_time=0.3, duration=1.0, **kw):
        super().__init__(**kw)
        self.depth = float(np.clip(depth, 0.05, EFFECTIVE_LIMIT[CH_SWITCH]))
        self.press_time = float(press_time)
        self.duration = float(duration)

    def keyframes(self, j_now):
        j0 = float(j_now[CH_SWITCH])
        return [
            _frame(j_now, {CH_SWITCH: j0 + self.depth}, self.duration),        # 下压
            _frame(j_now, {CH_SWITCH: j0 + self.depth}, self.press_time),      # 保持
            _frame(j_now, {CH_SWITCH: j0}, self.duration),                     # 回弹
        ]


class TurnKnob(Skill):
    """⑧ 旋钮开关（j6 末端，非操纵通道）：旋转 angle_unit 后保持 hold_time。"""
    name = "turn_knob"

    def __init__(self, angle_unit=0.5, hold_time=0.4, duration=1.2, **kw):
        super().__init__(**kw)
        self.angle_unit = float(np.clip(angle_unit, -EFFECTIVE_LIMIT[CH_KNOB], EFFECTIVE_LIMIT[CH_KNOB]))
        self.hold_time = float(hold_time)
        self.duration = float(duration)

    def keyframes(self, j_now):
        k0 = float(j_now[CH_KNOB])
        return [
            _frame(j_now, {CH_KNOB: k0 + self.angle_unit}, self.duration),     # 旋转
            _frame(j_now, {CH_KNOB: k0 + self.angle_unit}, self.hold_time),    # 停位
        ]


ALL_SKILLS = {cls.name: cls for cls in
              [PushPullCyclic, RaiseLowerCollective, SidePushCyclic, PedalStep,
               CenterChannel, HoldPose, PressSwitch, TurnKnob]}


# ============================================================================
# 语义层：高层指令 → 技能序列
# ============================================================================

def compile_semantic(cmd, amount=None, duration=None):
    """高层语义指令编译为 SkillSequence。amount/duration None 时用各技能缺省。

    返回 SkillSequence；未知指令抛 KeyError（由上层意图引擎负责，见验收映射 §1 需求3）。
    """
    def A(default):
        return default if amount is None else float(amount)

    def D(default):
        return default if duration is None else float(duration)

    table = {
        # —— 飞行操纵 ——
        "climb":       [RaiseLowerCollective(amount=A(0.5), duration=D(1.5))],
        "descend":     [RaiseLowerCollective(amount=A(-0.5), duration=D(1.5))],
        "pitch_down":  [PushPullCyclic(amount=A(+0.4), duration=D(1.0))],   # 正 pitch=低头
        "pitch_up":    [PushPullCyclic(amount=A(-0.4), duration=D(1.0))],
        "roll_left":   [SidePushCyclic(amount=A(+0.3), duration=D(1.0))],
        "roll_right":  [SidePushCyclic(amount=A(-0.3), duration=D(1.0))],
        "yaw_left":    [PedalStep(amount=A(-0.4), duration=D(1.0))],
        "yaw_right":   [PedalStep(amount=A(+0.4), duration=D(1.0))],
        "hover_neutral": [CenterChannel(duration=D(1.5)), HoldPose(duration=D(1.0))],
        # —— 座舱电气（演示技能库泛化到非操纵动作）——
        "cabin_power_on": [PressSwitch(), TurnKnob()],
    }
    if cmd not in table:
        raise KeyError(f"unknown semantic cmd: {cmd!r} (known: {sorted(table)})")
    return SkillSequence(table[cmd], label=cmd)


# ============================================================================
# 执行层：技能序列 → 关节力矩（经契约主链路）
# ============================================================================

class SkillSequence:
    """技能序列执行器。run() 逐帧走 arm.set_target_norm 主链路（契约包络自动生效）。"""

    def __init__(self, skills, label="seq"):
        self.skills = list(skills)
        self.label = label

    def validate(self, arm):
        return [s for s in self.skills if not s.validate(arm.get_joint_states())[0]]

    def run(self, arm, dt=None, time_scale=1.0):
        """执行全部技能，返回报告 dict。

        单技能成功判定（对接验收口径"到位且时序正确"）：
          settled     — 到达目标（max|Δj| ≤ settle_tol）
          duration_ok — 实际用时 ≤ 期望用时×1.5 + 0.5s 超时余量
        任一技能失败立即中止（安全优先），后续技能记 skipped。
        """
        dt = arm.control_dt if dt is None else float(dt)
        report = {"label": self.label, "steps": [], "success_rate": 0.0, "aborted": False}

        n_ok = 0
        for skill in self.skills:
            ok, msg = skill.validate(arm.get_joint_states())
            if not ok:
                report["steps"].append({"skill": skill.name, "status": "invalid",
                                        "detail": msg})
                report["aborted"] = True
                break
            if arm.esd_level >= 2:
                report["steps"].append({"skill": skill.name, "status": "skipped_esd"})
                report["aborted"] = True
                break

            default_force = arm.motor_force
            arm.motor_force = skill.torque_limit      # 技能级力矩限幅
            settled_all, time_ok_all = True, True
            try:
                for frame_dur, target in skill.keyframes(arm.get_joint_states()):
                    if not arm.set_target_norm(target):
                        settled_all = False           # 急停激活/无效目标
                        break
                    expect = max(frame_dur / max(time_scale, 1e-6), dt)
                    deadline = expect * 1.5 + 0.5
                    t_elapsed, settled = 0.0, False
                    t_arrive = None                               # 首次入带时刻（连续保持段起点）
                    hold_need = max(1, int(round(0.3 / dt)))   # 到位=容差带内稳定保持 0.3 s
                    hold_cnt = 0
                    while t_elapsed < deadline:
                        arm.step(dt)
                        t_elapsed += dt
                        j = arm.get_joint_states()
                        if np.max(np.abs(j - np.asarray(target))) <= skill.settle_tol:
                            if t_arrive is None:
                                t_arrive = t_elapsed
                            hold_cnt += 1
                            if hold_cnt >= hold_need:
                                settled = True
                                break
                        else:
                            hold_cnt = 0
                            t_arrive = None       # 连续性被破坏，到达时刻重置
                    if not settled:
                        settled_all = False
                    # 时序考核"到达时刻"（不含 0.3 s 确认窗口——窗口属判定观测，非执行耗时）
                    if not (settled and t_arrive is not None
                            and t_arrive <= expect * 1.5 + 1e-9):
                        time_ok_all = False
            finally:
                arm.motor_force = default_force       # 恢复缺省力矩

            ok = settled_all and time_ok_all
            n_ok += int(ok)
            report["steps"].append({
                "skill": skill.name,
                "status": "ok" if ok else ("timeout" if not time_ok_all else "not_settled"),
                "settled": settled_all, "duration_ok": time_ok_all,
                "torque_limit": skill.torque_limit,
            })
            if not ok:
                report["aborted"] = True
                break

        report["success_rate"] = n_ok / len(self.skills)
        return report


# ============================================================================
# 自检（python arm/skills.py）——机制验证，非训练
# ============================================================================

def _self_test():
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    print("=" * 62)
    print("  arm/skills.py 自检（D4 原子技能库）")
    print("=" * 62)

    check("S0 技能数量 ≥8", len(ALL_SKILLS) >= 8, f"n={len(ALL_SKILLS)}: {sorted(ALL_SKILLS)}")

    # S1 参数钳位与关键帧合法性（操纵通道不越契约有效区）
    bad = []
    for name, cls in ALL_SKILLS.items():
        try:
            sk = cls(amount=99.0) if name in ("push_pull_cyclic", "raise_lower_collective",
                                              "side_push_cyclic", "pedal_step") else cls()
            ok, msg = sk.validate(np.zeros(N_JOINTS))
            if not ok:
                bad.append(f"{name}:{msg}")
        except TypeError:
            bad.append(f"{name}:ctor")
    check("S1 全技能关键帧合法（参数越界被钳位）", not bad, "; ".join(bad))
    sk = PushPullCyclic(amount=99.0)
    check("S1a 推杆幅度钳到有效区 0.8", abs(sk.amount - 0.8) < 1e-12, f"amount={sk.amount}")

    # S2 语义映射
    seq = compile_semantic("climb", amount=0.8)
    check("S2a climb → 提总距技能", seq.skills[0].name == "raise_lower_collective"
          and abs(seq.skills[0].amount - 0.8) < 1e-12)
    check("S2b hover_neutral → 回中+保持", [s.name for s in compile_semantic("hover_neutral").skills]
          == ["center_channel", "hold_pose"])
    check("S2c cabin_power_on → 开关+旋钮", [s.name for s in compile_semantic("cabin_power_on").skills]
          == ["press_switch", "turn_knob"])
    try:
        compile_semantic("fly_to_moon")
        check("S2d 未知指令报错", False)
    except KeyError:
        check("S2d 未知指令报错", True)

    # S3 仿真执行：climb 技能到位 + 时序
    arm = KukaIiwaArm(control_dt=0.02)
    seq = compile_semantic("climb", amount=0.8, duration=1.5)
    rep = seq.run(arm)
    step0 = rep["steps"][0]
    check("S3a climb 到位", step0["settled"] is True, f"status={step0['status']}")
    check("S3b climb 时序正确", step0["duration_ok"] is True)
    check("S3c 序列成功率 100%", rep["success_rate"] == 1.0)
    c = arm.get_controls()
    expect_c = (0.8 + 1.5) / 3.0
    # 断言容差与 settle_tol 自洽：j0 稳态容差 0.05 → collective 容差 0.05/3 ≈ 0.017
    check("S3d 契约映射输出正确 collective", abs(c[0] - expect_c) < 0.02,
          f"c={c[0]:.3f} expect={expect_c:.3f}")

    # S4 非操纵技能不扰动操纵通道
    arm.reset()
    before = arm.get_joint_states()[[CH_COLLECTIVE, CH_PITCH, CH_ROLL, CH_PEDAL]].copy()
    rep = compile_semantic("cabin_power_on").run(arm)
    after = arm.get_joint_states()[[CH_COLLECTIVE, CH_PITCH, CH_ROLL, CH_PEDAL]]
    check("S4a cabin_power_on 全部技能 ok", rep["success_rate"] == 1.0,
          f"{[s['status'] for s in rep['steps']]}")
    check("S4b 操纵通道零扰动", np.max(np.abs(after - before)) < 1e-9,
          f"max_dev={np.max(np.abs(after - before)):.2e}")

    # S5 急停联动：执行中止 + 报告
    arm.reset()
    seq = SkillSequence([HoldPose(0.2), RaiseLowerCollective(amount=1.0, duration=1.0),
                         PushPullCyclic(amount=0.4)], label="estop_test")
    arm.estop(level=2)                      # 执行前急停
    rep = seq.run(arm)
    check("S5a 急停期间技能被拒/跳过", rep["aborted"] and rep["success_rate"] == 0.0,
          f"steps={[s['status'] for s in rep['steps']]}")
    arm.clear_estop()

    # S6 力矩限幅恢复
    check("S6 执行后 motor_force 恢复缺省", abs(arm.motor_force - 200.0) < 1e-9,
          f"force={arm.motor_force}")

    arm.close()
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("\n" + "=" * 62)
    print(f"  自检结果：{len(results) - n_fail}/{len(results)} 通过"
          + (f"（{n_fail} 项失败）" if n_fail else " —— 全部通过"))
    print("=" * 62)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(_self_test())
