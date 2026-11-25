# -*- coding: utf-8 -*-
"""
FastHeloEnv: 直升机起飞-悬停任务的 Gymnasium 环境（供 PPO 等 RL 训练）

修复了 helo_sim_final.py / helo_sim_auto.py 中简化动力学的两个物理 bug:
  1) 地面效应: 原版在离地瞬间升力骤降 20% (硬切换) -> 贴地弹跳/呆滞;
     本版地面效应随离地高度指数平滑衰减。
  2) 总距-推力标定: 原版悬停阈值 0.6655 但无任何反馈回路, 开环保持总距
     只会停在平衡点; 本版阈值 ~0.70 且观测中含高度误差, 策略可闭环调节。

特性:
  - SI 单位, dt=0.02s (50Hz), 20s/回合 = 1000 步, 无 sleep, 纯 numpy, 每秒数万步
  - 支持域随机化: 每回合随机目标高度、风、质量扰动
  - action_mode="direct": 动作直接是 [总距, 俯仰, 滚转, 尾桨]
    action_mode="arm":   动作是 7 关节目标角, 经 P 控制器 + helo_sim_final 同款映射
  - 坠毁判定: 姿态超限(空中)/硬接地, 与 helo_fg_loop.is_crashed 语义一致
"""
import math
import numpy as np
import gymnasium as gym
from gymnasium import spaces

DT = 0.02                 # 50 Hz
EP_SECONDS = 20.0
EP_STEPS = int(EP_SECONDS / DT)

MASS = 2000.0             # kg
G = 9.81
MAX_THRUST = 28000.0      # N  -> 悬停总距 = MASS*G/MAX_THRUST = 0.7014
HOVER_COLLECTIVE = MASS * G / MAX_THRUST
GE_PEAK = 1.35            # agl=0 时地面效应最大增升
GE_DECAY = 3.0            # m, 指数衰减尺度
MAX_ANG = math.radians(45.0)
CRASH_ANG = math.radians(60.0)
ANG_CMD_ACC = 3.0         # rad/s^2 per unit command
ANG_DAMP = 2.5            # 1/s
KD = 0.08                 # 平移阻尼系数 1/s
YAW_RATE_MAX = math.radians(60.0)
VS_MAX = 15.0             # m/s
HARD_LANDING_VS = -3.0    # m/s, 低于此垂速接地判坠毁


class FastHeloEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, action_mode="direct", wind=True, target_range=(25.0, 70.0),
                 episode_steps=EP_STEPS):
        super().__init__()
        assert action_mode in ("direct", "arm")
        self.action_mode = action_mode
        self.wind_enabled = wind
        self.target_range = target_range
        self.episode_steps = episode_steps

        if action_mode == "direct":
            self.action_space = spaces.Box(-1.0, 1.0, shape=(4,), dtype=np.float32)
        else:  # arm: 7 关节目标角, 范围与 helo_sim_final 一致 [-1.5, 1.5]
            self.action_space = spaces.Box(-1.5, 1.5, shape=(7,), dtype=np.float32)
        # obs: agl, vz, vx, vy, pitch, pitch_rate, roll, roll_rate, yaw_rate, alt_err
        self.observation_space = spaces.Box(-10.0, 10.0, shape=(10,), dtype=np.float32)

        self.rng = np.random.default_rng()
        self._reset_state()

    # ---------------- 内部物理 ----------------
    def _reset_state(self):
        self.agl = 0.0            # m, 离地高度
        self.vz = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.pitch = 0.0          # rad, 正 = 低头前飞(与 helo_sim_final 一致)
        self.roll = 0.0
        self.pitch_rate = 0.0
        self.roll_rate = 0.0
        self.yaw_rate = 0.0
        self.t = 0.0
        self.steps = 0
        self.crashed = False
        self.target_alt = self.rng.uniform(*self.target_range)
        self.wind_x = self.rng.uniform(-3.0, 3.0) if self.wind_enabled else 0.0
        self.wind_y = self.rng.uniform(-2.0, 2.0) if self.wind_enabled else 0.0
        self.mass = MASS * self.rng.uniform(0.95, 1.05)  # 质量扰动
        self._alt_err_hist = []

        # 机械臂层 (arm 模式): 与 helo_sim_final.VirtualRobotArm 相同的 P 控制器
        self.joints = np.zeros(7)
        self.joint_targets = np.zeros(7)

    def _arm_to_controls(self):
        """关节 -> 控制量, 映射公式与 helo_sim_final.py 193-203 行一致"""
        j = self.joints
        collective = float(np.clip((j[0] + 1.5) / 3.0, 0.0, 1.0))
        cyclic_pitch = float(np.clip(j[1] / 0.8, -1.0, 1.0))
        cyclic_roll = float(np.clip(j[2] / 0.8, -1.0, 1.0))
        pedals = float(np.clip(j[3] / 0.8, -1.0, 1.0))
        return collective, cyclic_pitch, cyclic_roll, pedals

    def _physics_step(self, collective, cyclic_pitch, cyclic_roll, pedals):
        # ---- 姿态一阶动力学 (带阻尼) ----
        self.pitch_rate += (cyclic_pitch * ANG_CMD_ACC - ANG_DAMP * self.pitch_rate) * DT
        self.roll_rate += (cyclic_roll * ANG_CMD_ACC - ANG_DAMP * self.roll_rate) * DT
        self.yaw_rate = pedals * YAW_RATE_MAX

        airborne = self.agl > 0.05
        if airborne:
            self.pitch = float(np.clip(self.pitch + self.pitch_rate * DT, -MAX_ANG, MAX_ANG))
            self.roll = float(np.clip(self.roll + self.roll_rate * DT, -MAX_ANG, MAX_ANG))

        # ---- 推力: 总距 -> 推力, 地面效应平滑衰减 ----
        ge = 1.0 + (GE_PEAK - 1.0) * math.exp(-max(self.agl, 0.0) / GE_DECAY)
        thrust = collective * MAX_THRUST * ge
        t_vert = thrust * math.cos(self.pitch) * math.cos(self.roll)
        t_fwd = thrust * math.sin(self.pitch)
        t_side = -thrust * math.sin(self.roll)

        weight = self.mass * G
        if self.agl <= 0.0 and t_vert <= weight:
            # 地面锁止: skid 支撑, 不动
            self.agl = 0.0
            self.vz = self.vx = self.vy = 0.0
            self.pitch_rate = self.roll_rate = self.yaw_rate = 0.0
        else:
            self.vz += ((t_vert - weight) / self.mass) * DT
            self.vz = float(np.clip(self.vz, -VS_MAX, VS_MAX))
            self.vx += (t_fwd / self.mass - KD * self.vx + self.wind_x * 0.05) * DT
            self.vy += (t_side / self.mass - KD * self.vy + self.wind_y * 0.05) * DT
            self.agl += self.vz * DT

            if self.agl < 0.0:  # 接地
                if self.vz < HARD_LANDING_VS:
                    self.crashed = True
                self.agl = 0.0
                self.vz = self.vx = self.vy = 0.0

        if airborne and (abs(self.pitch) > CRASH_ANG or abs(self.roll) > CRASH_ANG):
            self.crashed = True

        self.t += DT
        self.steps += 1

    def _obs(self):
        alt_err = self.target_alt - self.agl
        return np.array([
            self.agl / 100.0,
            self.vz / VS_MAX,
            self.vx / 10.0,
            self.vy / 10.0,
            self.pitch / MAX_ANG,
            self.pitch_rate / ANG_CMD_ACC,
            self.roll / MAX_ANG,
            self.roll_rate / ANG_CMD_ACC,
            self.yaw_rate / YAW_RATE_MAX,
            alt_err / 100.0,
        ], dtype=np.float32)

    # ---------------- Gymnasium API ----------------
    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_state()
        return self._obs(), {"target_alt": self.target_alt}

    def step(self, action):
        action = np.asarray(action, dtype=np.float64).flatten()
        if self.action_mode == "arm":
            self.joint_targets = action.copy()
            # P 控制器, speed=2.0 与 helo_sim_final 一致
            self.joints += (self.joint_targets - self.joints) * 2.0 * DT
            collective, cp, cr, pd = self._arm_to_controls()
        else:
            collective = float(np.clip((action[0] + 1.0) / 2.0, 0.0, 1.0))
            cp, cr, pd = float(np.clip(action[1], -1, 1)), \
                         float(np.clip(action[2], -1, 1)), \
                         float(np.clip(action[3], -1, 1))

        prev_agl = self.agl
        self._physics_step(collective, cp, cr, pd)

        alt_err = abs(self.agl - self.target_alt)
        prev_alt_err = abs(prev_agl - self.target_alt)
        self._alt_err_hist.append(alt_err)

        # ---- v2 奖励: 势函数塑形 (potential-based shaping) ----
        # v1 教训: 只有 |alt_err| 项时, 地面是"死锁平台"(动作不改变状态, 无因果
        # 信号), 而早期飞行尝试只会挨罚 -> 策略收敛到"永远不起飞"局部最优。
        # 势函数让"每爬升 1 米"立即获得 +1.0, 起飞动作直接有梯度。
        progress = prev_alt_err - alt_err
        att_pen = 0.5 * (abs(self.pitch) + abs(self.roll))
        ctrl_pen = 0.01 * float(np.sum(np.square(action)))
        grounded_pen = 0.05 if (self.agl < 0.5 and self.t > 2.5) else 0.0
        hover_bonus = 0.5 if (alt_err < 5.0
                              and abs(self.pitch) < math.radians(15)
                              and abs(self.roll) < math.radians(15)) else 0.0

        reward = progress - att_pen - ctrl_pen - grounded_pen + hover_bonus + 0.02
        terminated = False
        if self.crashed:
            reward -= 25.0
            terminated = True
        truncated = self.steps >= self.episode_steps

        info = {
            "agl": self.agl,
            "target_alt": self.target_alt,
            "alt_err": alt_err,
            "crashed": self.crashed,
        }
        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self._alt_err_hist)))
            tail = self._alt_err_hist[-n_tail:]
            info["success"] = (not self.crashed) and (np.mean(tail) < 5.0)
            info["mean_alt_err"] = float(np.mean(self._alt_err_hist))
        return self._obs(), float(reward), terminated, truncated, info


if __name__ == "__main__":
    env = FastHeloEnv()
    obs, info = env.reset(seed=0)
    print("obs_space:", env.observation_space.shape, "act_space:", env.action_space.shape)
    print("HOVER_COLLECTIVE =", round(HOVER_COLLECTIVE, 4))
    # 恒定满总距冒烟测试: 应能离地爬升
    total = 0.0
    for i in range(300):
        obs, r, term, trunc, info = env.step(np.array([1.0, 0, 0, 0], dtype=np.float32))
        total += r
        if i % 100 == 0:
            print(f"step {i}: agl={info['agl']:.1f}m r={r:.3f}")
        if term or trunc:
            break
    print("smoke: crashed =", info["crashed"], "agl =", round(info["agl"], 1), "m")
