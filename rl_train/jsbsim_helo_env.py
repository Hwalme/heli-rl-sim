# -*- coding: utf-8 -*-
"""
JSBSimHeloEnv: 基于 JSBSim AH-1S 高保真飞行动力学模型的 Gymnasium 强化学习环境

特性:
  1. 高保真物理: 基于 JSBSim 6-DoF 刚体动力学 + 桨叶元素理论(BET)旋翼模型 + 地面效应 + 气动查表
  2. 姿态增稳内环 (SAS): 采用真实直升机标准阻尼增稳内环，抑制开环角速度发散
  3. 配平中心化动作空间 (Trim-Centered Actions): action[0]=0 对应稳态悬停总距 (0.70)
  4. 极速执行: Python 进程内直接绑定调用，无网络协议延迟
  5. 契约兼容: 对齐 interfaces/action-space.md (v1-frozen) 与 interfaces/sim-state-schema.md
  6. 势函数奖励塑形: 保证起飞探索因果清晰，快速收敛
"""
import os
import math
import numpy as np
import gymnasium as gym
from gymnasium import spaces

import jsbsim

DT = 0.02                 # 50 Hz
EP_SECONDS = 20.0
EP_STEPS = int(EP_SECONDS / DT)   # 1000 步

FT2M = 0.3048
M2FT = 3.28084
FPS2MPS = 0.3048
MPS2FPS = 3.28084

MAX_ANG = math.radians(45.0)
CRASH_ANG = math.radians(60.0)
HARD_LANDING_VS = -3.0    # m/s (验收文档指标: 软接地 vz > -3m/s)


class JSBSimHeloEnv(gym.Env):
    metadata = {"render_modes": []}

    # AH-1S 物理受限修复 (E 18:15 裁决, smoke_ah1s_repaired.py 已 PASS)
    # 不修改任何 JSBSim 系统文件, 仅在 reset 时运行时覆盖 4 个 property
    REPAIR_OVERRIDES = {
        "fcs/adj/longitudinal-gain": 0.5,    # 原 0.125 -> 4 倍权限 (前飞/俯仰通道)
        "fcs/adj/pedal-gain": 0.25,           # 原 0.18 -> +39% (尾桨抗扭矩)
        "aero/setup/Nr_limiter": 1.0,          # 原 0.05 -> 关掉 yaw 限制器
        "fcs/adj/center-sensitivity": 1.0,     # 原 1.5 -> 关掉非线性增益
    }

    def __init__(self, action_mode="direct", wind=True, target_range=(10.0, 30.0),
                 episode_steps=EP_STEPS, repair_overrides=True):
        super().__init__()
        assert action_mode in ("direct", "arm")
        self.action_mode = action_mode
        self.wind_enabled = wind
        self.target_range = target_range
        self.episode_steps = episode_steps
        self.repair_overrides = repair_overrides   # 默认开启 (E 18:15 生效后)

        # 动作空间: direct 为 4 维 [-1, 1], arm 为 7 关节 [-1.5, 1.5]
        if action_mode == "direct":
            self.action_space = spaces.Box(-1.0, 1.0, shape=(4,), dtype=np.float32)
        else:
            self.action_space = spaces.Box(-1.5, 1.5, shape=(7,), dtype=np.float32)

        # 观测空间 (12 维, v5 整改令 §6.2 加位置观测):
        # [agl_norm, vz_norm, vx_norm, vy_norm, pitch_norm, q_norm, roll_norm, p_norm,
        #  r_norm, alt_err_norm, pos_n_norm, pos_e_norm]
        # v5 依据: 10 维下策略看不见漂移 (G1 审计悬停漂移 163m/342m 均判成功),
        # 只能靠体轴速度间接抑制, 有风时不够 —— 必须给位置观测。
        self.observation_space = spaces.Box(-10.0, 10.0, shape=(12,), dtype=np.float32)

        self.rng = np.random.default_rng()
        self._init_jsbsim()

    def _init_jsbsim(self):
        root = jsbsim.get_default_root_dir()
        self.fdm = jsbsim.FGFDMExec(root, None)
        self.fdm.load_model('ah1s')
        self.ic_path = os.path.join(root, 'aircraft', 'ah1s', 'reset00.xml')
        self._ground_agl_ft = 6.30  # reset00 初始停机高度

    def _reset_jsbsim(self):
        self.fdm.load_ic('reset00.xml', True)
        self.fdm.run_ic()
        self.fdm.set_dt(DT)

        # 激活转速调节器与双发
        self.fdm.set_property_value('fcs/rpm-governor-active-norm', 1.0)
        self.fdm.set_property_value('propulsion/engine[0]/set-running', 1.0)
        self.fdm.set_property_value('propulsion/engine[1]/set-running', 1.0)
        self.fdm.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
        self.fdm.set_property_value('fcs/throttle-cmd-norm[1]', 1.0)

        # 风场扰动
        if self.wind_enabled:
            wind_n = float(self.rng.uniform(-6.0, 6.0))  # fps
            wind_e = float(self.rng.uniform(-6.0, 6.0))
            self.fdm.set_property_value('atmosphere/wind-north-fps', wind_n)
            self.fdm.set_property_value('atmosphere/wind-east-fps', wind_e)

        # AH-1S 物理受限修复 (E 18:15 裁决生效, 冒烟已 PASS)
        if self.repair_overrides:
            for k, v in self.REPAIR_OVERRIDES.items():
                self.fdm.set_property_value(k, v)

    def _reset_state(self):
        self._reset_jsbsim()
        self.target_alt = float(self.rng.uniform(*self.target_range))
        self.t = 0.0
        self.steps = 0
        self.crashed = False
        self._alt_err_hist = []
        # v5: 记录起始点经纬度 (用于漂移计算)
        self._lat0 = self.fdm.get_property_value('position/lat-geod-deg')
        self._lon0 = self.fdm.get_property_value('position/long-gc-deg')

        # 机械臂内部状态 (arm 模式)
        self.joints = np.zeros(7)
        self.joint_targets = np.zeros(7)

    def _arm_to_controls(self):
        """机械臂 7 关节 -> 4 通道映射 (对齐 interfaces/action-space.md v1-frozen)"""
        j = self.joints
        # 关节0: 总距 [-1, 1]
        collective = float(np.clip(j[0] / 1.5, -1.0, 1.0))
        # 关节1: 俯仰 [-1, 1]
        cyclic_pitch = float(np.clip(j[1] / 0.8, -1.0, 1.0))
        # 关节2: 滚转 [-1, 1]
        cyclic_roll = float(np.clip(j[2] / 0.8, -1.0, 1.0))
        # 关节3: 尾桨偏航 [-1, 1]
        pedals = float(np.clip(j[3] / 0.8, -1.0, 1.0))
        return collective, cyclic_pitch, cyclic_roll, pedals

    def _apply_controls(self, u_coll, u_pitch, u_roll, u_yaw):
        pitch_rad = self.fdm.get_property_value('attitude/theta-rad')
        roll_rad = self.fdm.get_property_value('attitude/phi-rad')
        p = self.fdm.get_property_value('velocities/p-rad_sec')
        q = self.fdm.get_property_value('velocities/q-rad_sec')
        r = self.fdm.get_property_value('velocities/r-rad_sec')

        # 1. 配平中心化总距: 0 对应稳态悬停配平点 (0.70), 范围 [0.45, 0.95]
        coll = float(np.clip(0.70 + u_coll * 0.25, 0.0, 1.0))

        # 2. 俯仰 + 姿态增稳
        ele = float(np.clip(u_pitch * 0.3 + 0.6 * pitch_rad + 0.25 * q, -0.5, 0.5))

        # 3. 横滚 + 姿态增稳
        ail = float(np.clip(u_roll * 0.3 - 0.6 * roll_rad - 0.25 * p, -0.5, 0.5))

        # 4. 偏航 + 反扭矩补偿 + 阻尼
        rud = float(np.clip(u_yaw * 0.3 + 0.4 * r - 0.15 * (coll - 0.5), -0.6, 0.6))

        self.fdm.set_property_value('fcs/collective-cmd-norm', coll)
        self.fdm.set_property_value('fcs/elevator-cmd-norm', ele)
        self.fdm.set_property_value('fcs/aileron-cmd-norm', ail)
        self.fdm.set_property_value('fcs/rudder-cmd-norm', rud)

    def _get_raw_state(self):
        h_agl_ft = self.fdm.get_property_value('position/h-agl-ft')
        agl_m = max(0.0, (h_agl_ft - self._ground_agl_ft) * FT2M)

        v_down_fps = self.fdm.get_property_value('velocities/v-down-fps')
        vz_mps = -v_down_fps * FPS2MPS

        u_fps = self.fdm.get_property_value('velocities/u-fps')
        v_fps = self.fdm.get_property_value('velocities/v-fps')
        vx_mps = u_fps * FPS2MPS
        vy_mps = v_fps * FPS2MPS

        pitch_rad = self.fdm.get_property_value('attitude/theta-rad')
        roll_rad = self.fdm.get_property_value('attitude/phi-rad')
        p_rad_s = self.fdm.get_property_value('velocities/p-rad_sec')
        q_rad_s = self.fdm.get_property_value('velocities/q-rad_sec')
        r_rad_s = self.fdm.get_property_value('velocities/r-rad_sec')

        return {
            'agl': agl_m,
            'vz': vz_mps,
            'vx': vx_mps,
            'vy': vy_mps,
            'pitch': pitch_rad,
            'pitch_rate': q_rad_s,
            'roll': roll_rad,
            'roll_rate': p_rad_s,
            'yaw_rate': r_rad_s,
        }

    def _pos_ne(self):
        """v5: 相对起始点的北/东位置 (m)"""
        lat = self.fdm.get_property_value('position/lat-geod-deg')
        lon = self.fdm.get_property_value('position/long-gc-deg')
        M_PER_DEG_LAT = 111320.0
        return ((lat - self._lat0) * M_PER_DEG_LAT,
                (lon - self._lon0) * M_PER_DEG_LAT * math.cos(math.radians(self._lat0)))

    def _obs(self, s):
        alt_err = self.target_alt - s['agl']
        pos_n, pos_e = self._pos_ne()  # v5: 相对起始点的北/东位移 (m)
        return np.array([
            float(s['agl'] / 50.0),
            float(s['vz'] / 15.0),
            float(s['vx'] / 15.0),
            float(s['vy'] / 15.0),
            float(s['pitch'] / MAX_ANG),
            float(s['pitch_rate'] / 2.0),
            float(s['roll'] / MAX_ANG),
            float(s['roll_rate'] / 2.0),
            float(s['yaw_rate'] / 2.0),
            float(alt_err / 50.0),
            float(pos_n / 50.0),  # v5: 北向位移 (与任务族 pos 刻度一致)
            float(pos_e / 50.0),  # v5: 东向位移
        ], dtype=np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_state()
        s = self._get_raw_state()
        return self._obs(s), {"target_alt": self.target_alt}

    def step(self, action):
        action = np.asarray(action, dtype=np.float64).flatten()
        if self.action_mode == "arm":
            self.joint_targets = action.copy()
            self.joints += (self.joint_targets - self.joints) * 2.0 * DT
            u_coll, cp, cr, pd = self._arm_to_controls()
        else:
            u_coll = float(np.clip(action[0], -1.0, 1.0))
            cp = float(np.clip(action[1], -1.0, 1.0))
            cr = float(np.clip(action[2], -1.0, 1.0))
            pd = float(np.clip(action[3], -1.0, 1.0))

        prev_s = self._get_raw_state()
        self._apply_controls(u_coll, cp, cr, pd)

        self.fdm.run()

        s = self._get_raw_state()
        self.t += DT
        self.steps += 1

        alt_err = abs(s['agl'] - self.target_alt)
        prev_alt_err = abs(prev_s['agl'] - self.target_alt)
        self._alt_err_hist.append(alt_err)

        if abs(s['pitch']) > CRASH_ANG or abs(s['roll']) > CRASH_ANG:
            self.crashed = True
        if s['agl'] <= 0.1 and prev_s['agl'] > 0.5 and s['vz'] < HARD_LANDING_VS:
            self.crashed = True

        # ---- 势函数奖励 + 悬停奖励 ----
        progress = 2.0 * (prev_alt_err - alt_err)  # 势函数梯度
        att_pen = 0.3 * (abs(s['pitch']) + abs(s['roll']))
        ctrl_pen = 0.01 * float(np.sum(np.square(action)))
        grounded_pen = 0.1 if (s['agl'] < 0.5 and self.t > 2.0) else 0.0

        # v5: 水平速度惩罚 + 漂移惩罚 (整改令 §6.2, G1审计悬停漂移163m仍判成功)
        v_horiz = math.hypot(s['vx'], s['vy'])
        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)
        horiz_speed_pen = 0.1 * v_horiz
        drift_pen = 0.02 * net_drift

        hover_bonus = 0.0
        if alt_err < 2.0 and abs(s['pitch']) < math.radians(10) and abs(s['roll']) < math.radians(10):
            hover_bonus = 2.0
        elif alt_err < 4.0:
            hover_bonus = 0.8

        reward = progress - att_pen - ctrl_pen - grounded_pen + hover_bonus + 0.1 - horiz_speed_pen - drift_pen

        terminated = False
        if self.crashed:
            reward -= 50.0
            terminated = True

        truncated = self.steps >= self.episode_steps

        info = {
            "agl": s['agl'],
            "vz": s['vz'],
            "target_alt": self.target_alt,
            "alt_err": alt_err,
            "crashed": self.crashed,
        }

        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self._alt_err_hist)))
            tail = self._alt_err_hist[-n_tail:]
            # v5: 成功判据加漂移上限 20m (整改令 §5 G4: 悬停净漂移<20m)
            info["success"] = (not self.crashed) and (np.mean(tail) < 3.0) and (net_drift < 20.0)
            info["mean_alt_err"] = float(np.mean(self._alt_err_hist))
            info["net_drift_m"] = float(net_drift)

        return self._obs(s), float(reward), terminated, truncated, info
