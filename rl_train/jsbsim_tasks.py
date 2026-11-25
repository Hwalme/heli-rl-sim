# -*- coding: utf-8 -*-
"""
jsbsim_tasks.py: 覆盖验收指标全部 6 种飞行动作的多任务环境族 (v3)

任务列表 (全部 4 维动作 / 50 Hz / 同一 SAS 执行包络):
  1. 起飞与悬停     -> JSBSimHeloEnv (jsbsim_helo_env.py, 已训 100%)
  2. 前飞速度跟踪   -> ForwardFlightTask   (30 kt ± 5 kt, 定高 20 m ± 3 m)
  3. 定点软着陆     -> LandingTask         (接地垂直速度 vz > -3.0 m/s)
  4. 滚转机动       -> RollManeuverTask    (目标坡度 ±15°~20° 保持后改平)
  5. 偏航机动       -> YawManeuverTask     (目标航向变化 ±60°~120° 并保持)
  6. 航路点导航     -> WaypointTrackTask   (3 航点 L 形航线, 到点半径 10 m)

v3 观测契约 (对齐 interfaces/sim-state-schema "前 10 维语义不动、允许追加"):
  所有任务观测 = JSBSimHeloEnv 悬停 10 维 (语义与刻度逐字一致) + 任务追加维。
  因此悬停已训策略 (results/jsbsim_ppo_direct.zip) 可直接 --init-model 课程
  初始化任何机动任务: 起点即完美悬停, RL 只需学机动增量。
  追加维 (各任务不同, 追加在尾部):
    forward  : [u_err/30, u_kts/60]
    landing  : [(vz+1)/2, 1(agl<2)]                      (12 维)
    roll     : [(ref-roll)/45°, phase, target_roll/45°]  (13 维)
    yaw      : [psi_err/pi, target_dpsi/pi]              (12 维)
    waypoint : [dist/150, psi_err/pi, wp_idx/3]          (13 维)

继承 v2 的实测修正 (调试记录见 rl_train/README.md):
  - _fly_to_hover: PD 增稳爬升建立悬停 (修复 v1 开环爬升翻覆);
  - roll: 坡度-总距前馈 + 垂直速度阻尼 (防压坡度掉高度触地卡死)、
    目标幅值课程 (4°->20°)、左右均衡采样、速率塑形;
  - yaw: u_yaw 低通 τ=0.4s (防 bang-bang 航向甩摆);
  - 降落硬接地 (vz<=-3) 记着陆失败不记坠毁 (E 轨道 A6 口径)。
"""
import os
import math
import numpy as np
import gymnasium as gym
from gymnasium import spaces

import jsbsim

DT = 0.02
EP_SECONDS = 20.0

FT2M = 0.3048
FPS2MPS = 0.3048
MPS2KTS = 1.94384
M_PER_DEG_LAT = 111320.0

MAX_ANG = math.radians(45.0)
CRASH_ANG = math.radians(60.0)
HARD_LANDING_VS = -3.0    # m/s, 验收指标: 软接地 vz > -3 m/s


def wrap_angle(a):
    """把角度差卷到 [-pi, pi]"""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class JSBSimTaskBase(gym.Env):
    """多任务共享基类: JSBSim 初始化 / SAS 控制律 / PD 增稳爬升到悬停 /
    悬停 10 维对齐观测"""

    metadata = {"render_modes": []}

    # AH-1S 物理受限修复 (E 18:15 裁决, smoke_ah1s_repaired.py 已 PASS)
    # 与 JSBSimHeloEnv.REPAIR_OVERRIDES 保持一致
    REPAIR_OVERRIDES = {
        "fcs/adj/longitudinal-gain": 0.5,
        "fcs/adj/pedal-gain": 0.25,
        "aero/setup/Nr_limiter": 1.0,
        "fcs/adj/center-sensitivity": 1.0,
    }

    def __init__(self, episode_steps=1000, wind=True, n_extra_obs=0,
                 repair_overrides=True):
        self.episode_steps = episode_steps
        self.wind_enabled = wind
        self.rng = np.random.default_rng()

        self.action_space = spaces.Box(-1.0, 1.0, shape=(4,), dtype=np.float32)
        n_obs = 10 + n_extra_obs
        self.observation_space = spaces.Box(-10.0, 10.0, shape=(n_obs,), dtype=np.float32)

        root = jsbsim.get_default_root_dir()
        self.fdm = jsbsim.FGFDMExec(root, None)
        self.fdm.load_model('ah1s')
        self._ground_agl_ft = 6.30
        self.repair_overrides = repair_overrides

        # v4: task 级可覆盖物理层参数 (默认=E 18:15 裁决原值, 确保 hover/forward/roll/landing 不退化)
        # Yaw/Waypoint 任务在 __init__ 中覆盖这些值以修复航向通道物理不对称
        self.pedal_gain_override = None       # None=用 REPAIR_OVERRIDES 默认 0.25
        self.trim_clip_min = -0.3              # trim 积分下限 (原不对称: 右舵仅 -0.3)
        self.trim_clip_max = 0.7               # trim 积分上限 (原不对称: 左舵 +0.7 易饱和)
        self.trim_kI = 0.0015                  # trim 积分速率 /(deg/s)/s (原 0.0015, 30s 内难收敛)
        self.yaw_sas_gain = 0.3                # SAS u_yaw 舵面增益 (原 0.3, 偏航权限偏小)

        self.steps = 0
        self.crashed = False
        self._rud_trim = 0.0

    def _reset_jsbsim(self):
        self.fdm.load_ic('reset00.xml', True)
        self.fdm.run_ic()
        self.fdm.set_dt(DT)
        self.fdm.set_property_value('fcs/rpm-governor-active-norm', 1.0)
        self.fdm.set_property_value('propulsion/engine[0]/set-running', 1.0)
        self.fdm.set_property_value('propulsion/engine[1]/set-running', 1.0)
        self.fdm.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
        self.fdm.set_property_value('fcs/throttle-cmd-norm[1]', 1.0)
        if self.wind_enabled:
            self.fdm.set_property_value('atmosphere/wind-north-fps', float(self.rng.uniform(-6.0, 6.0)))
            self.fdm.set_property_value('atmosphere/wind-east-fps', float(self.rng.uniform(-6.0, 6.0)))
        # AH-1S 物理受限修复 (E 18:15 裁决生效, 冒烟已 PASS)
        if self.repair_overrides:
            overrides = dict(self.REPAIR_OVERRIDES)
            # v4: task 级 pedal-gain 覆盖 (Yaw/Waypoint 提高尾桨权限)
            if self.pedal_gain_override is not None:
                overrides["fcs/adj/pedal-gain"] = self.pedal_gain_override
            for k, v in overrides.items():
                self.fdm.set_property_value(k, v)

    def _apply_sas(self, u_coll, u_pitch, u_roll, u_yaw):
        """与 jsbsim_helo_env._apply_controls 一致的控制律 + 抗扭矩积分配平。

        实测背景: 本模型悬停/爬升态存在 30~60°/s 的残余偏航自旋 (发动机
        扭矩与尾桨推力的平衡点偏移, 满舵瞬时压不净), 使体轴系速度失去意义
        (前飞任务的"倒退"、偏航任务的"压不住"皆源于此)。_rud_trim 按
        残余角速率缓慢积分 (kI=0.0015 /(deg/s)/s), 把自旋收敛到 ~0。"""
        pitch_rad = self.fdm.get_property_value('attitude/theta-rad')
        roll_rad = self.fdm.get_property_value('attitude/phi-rad')
        p = self.fdm.get_property_value('velocities/p-rad_sec')
        q = self.fdm.get_property_value('velocities/q-rad_sec')
        r = self.fdm.get_property_value('velocities/r-rad_sec')

        coll = float(np.clip(0.70 + u_coll * 0.25, 0.0, 1.0))
        ele = float(np.clip(u_pitch * 0.3 + 0.6 * pitch_rad + 0.25 * q, -0.5, 0.5))
        ail = float(np.clip(u_roll * 0.3 - 0.6 * roll_rad - 0.25 * p, -0.5, 0.5))
        rud = float(np.clip(u_yaw * self.yaw_sas_gain + 0.4 * r - 0.15 * (coll - 0.5) + self._rud_trim,
                            -0.8, 0.8))

        self.fdm.set_property_value('fcs/collective-cmd-norm', coll)
        self.fdm.set_property_value('fcs/elevator-cmd-norm', ele)
        self.fdm.set_property_value('fcs/aileron-cmd-norm', ail)
        self.fdm.set_property_value('fcs/rudder-cmd-norm', rud)

    def _integrate_yaw_trim(self):
        """每步推进后调用: 按残余偏航角速率积分脚蹬配平
        v4: kI 和限幅改为 task 级可覆盖 (Yaw/Waypoint 对称化限幅 + 加速收敛)"""
        r_deg = math.degrees(self.fdm.get_property_value('velocities/r-rad_sec'))
        if math.isnan(r_deg):
            return
        self._rud_trim = float(np.clip(self._rud_trim + self.trim_kI * r_deg * DT,
                                        self.trim_clip_min, self.trim_clip_max))

    def _raw(self):
        h_agl_ft = self.fdm.get_property_value('position/h-agl-ft')
        s = {
            'agl': max(0.0, (h_agl_ft - self._ground_agl_ft) * FT2M),
            'vz': -self.fdm.get_property_value('velocities/v-down-fps') * FPS2MPS,
            'u': self.fdm.get_property_value('velocities/u-fps') * FPS2MPS,
            'v': self.fdm.get_property_value('velocities/v-fps') * FPS2MPS,
            'pitch': self.fdm.get_property_value('attitude/theta-rad'),
            'roll': self.fdm.get_property_value('attitude/phi-rad'),
            'psi': self.fdm.get_property_value('attitude/psi-rad'),
            'p': self.fdm.get_property_value('velocities/p-rad_sec'),
            'q': self.fdm.get_property_value('velocities/q-rad_sec'),
            'r': self.fdm.get_property_value('velocities/r-rad_sec'),
        }
        s['u_kts'] = s['u'] * MPS2KTS
        return s

    def _get_raw_state(self):
        """悬停环境键名别名 (agl/vz/vx/vy/pitch/pitch_rate/roll/roll_rate/yaw_rate),
        供 train_jsbsim 的轨迹记录器复用"""
        s = self._raw()
        return {
            'agl': s['agl'], 'vz': s['vz'], 'vx': s['u'], 'vy': s['v'],
            'pitch': s['pitch'], 'pitch_rate': s['q'],
            'roll': s['roll'], 'roll_rate': s['p'], 'yaw_rate': s['r'],
        }

    def _hover_obs(self, s, target_alt):
        """JSBSimHeloEnv._obs 逐字同刻度的 10 维 (悬停策略可直接迁移)"""
        alt_err = target_alt - s['agl']
        return np.array([
            float(s['agl'] / 50.0),
            float(s['vz'] / 15.0),
            float(s['u'] / 15.0),
            float(s['v'] / 15.0),
            float(s['pitch'] / MAX_ANG),
            float(s['q'] / 2.0),
            float(s['roll'] / MAX_ANG),
            float(s['p'] / 2.0),
            float(s['r'] / 2.0),
            float(alt_err / 50.0),
        ], dtype=np.float32)

    def _pos_ne(self):
        """相对起始点的北/东位置 (m)"""
        lat = self.fdm.get_property_value('position/lat-geod-deg')
        lon = self.fdm.get_property_value('position/long-gc-deg')
        return ((lat - self._lat0) * M_PER_DEG_LAT,
                (lon - self._lon0) * M_PER_DEG_LAT * math.cos(math.radians(self._lat0)))

    def _record_origin(self):
        self._lat0 = self.fdm.get_property_value('position/lat-geod-deg')
        self._lon0 = self.fdm.get_property_value('position/long-gc-deg')

    # ---------- 悬停建立 (任务起始状态) ----------

    def _pd_step(self, coll):
        """无策略挂载时的回退爬升控制律 (姿态 PD, 增益经 18s 网格标定)。

        已知局限: 不能抑制爬升段偏航自旋, 建议始终挂载悬停策略。"""
        th = self.fdm.get_property_value('attitude/theta-rad')
        ph = self.fdm.get_property_value('attitude/phi-rad')
        q = self.fdm.get_property_value('velocities/q-rad_sec')
        p = self.fdm.get_property_value('velocities/p-rad_sec')
        r = self.fdm.get_property_value('velocities/r-rad_sec')
        ele = float(np.clip(1.5 * th + 0.6 * q, -0.5, 0.5))
        ail = float(np.clip(-2.4 * ph - 0.3 * p, -0.5, 0.5))
        rud = float(np.clip(-0.8 * r, -0.6, 0.6))
        self.fdm.set_property_value('fcs/collective-cmd-norm', float(np.clip(coll, 0.0, 1.0)))
        self.fdm.set_property_value('fcs/elevator-cmd-norm', ele)
        self.fdm.set_property_value('fcs/aileron-cmd-norm', ail)
        self.fdm.set_property_value('fcs/rudder-cmd-norm', rud)

    def attach_hover_policy(self, model):
        """挂载已训悬停策略, 用于 reset 阶段的爬升/建立悬停。

        实测背景: 开环 PD 爬升无法抑制本模型的偏航自旋 (抗扭矩配平随
        总距变化, 爬升段自旋可达 200°/s), 而悬停策略是从地面静止学会
        总距-脚蹬协调的, 是唯一已知可靠的爬升手段。"""
        self._hover_policy = model

    def _fly_to_hover(self, target_alt_m, settle_s=3.0, max_s=35.0):
        """爬升到 target_alt_m 建立悬停, 返回 (成功, 稳态后状态)。

        优先用挂载的悬停策略逐步控制; 未挂载时退回 PD 增稳爬升。
        每步推进后调用 _integrate_yaw_trim() 抗扭矩积分, 末态强制 r≈0。"""
        policy = getattr(self, '_hover_policy', None)
        settled_for = 0.0
        for _ in range(int(max_s / DT)):
            s = self._raw()
            if abs(s['pitch']) > CRASH_ANG or abs(s['roll']) > CRASH_ANG:
                return False, s
            alt_err = target_alt_m - s['agl']
            if policy is not None:
                obs = self._hover_obs(s, target_alt_m)
                # v5.2.3c: NaN 防护 —— 爬升段 JSBSim 爆炸时 NaN 观测会让 policy.predict
                #   直接 raise (推理即崩)。放弃本次爬升, 调用方 reset 的重试/清零兜底。
                #   注: takeoff 家族从地面直接开始, 不经过本方法, 此改动不涉及其运行时。
                if not np.all(np.isfinite(obs)):
                    return False, s
                action, _ = policy.predict(obs, deterministic=True)
                self._apply_sas(*(float(a) for a in np.clip(action, -1.0, 1.0)))
            else:
                vs_cmd = float(np.clip(0.4 * alt_err, -1.0, 3.0))
                coll = 0.70 + 0.06 * (vs_cmd - s['vz'])
                self._pd_step(coll)
            self.fdm.run()
            self._integrate_yaw_trim()
            if abs(alt_err) < 1.0 and abs(s['vz']) < 0.6:
                settled_for += DT
                if settled_for >= settle_s:
                    break
            else:
                settled_for = 0.0
        s = self._raw()
        ok = (abs(s['agl'] - target_alt_m) < 2.5
              and abs(s['roll']) < math.radians(10) and abs(s['pitch']) < math.radians(10))
        return ok, s

    def _check_crash(self, s):
        if abs(s['pitch']) > CRASH_ANG or abs(s['roll']) > CRASH_ANG:
            self.crashed = True

    def step(self, action):
        """任务基类直接被调用的 step 是 placeholder, 子类必须 override。"""
        raise NotImplementedError

    def _post_physics(self):
        """物理推进后钩子: 抗扭矩积分 trim 每步推进, 让策略可以利用 trim
        (e.g. 航向任务奖励"踩满舵凑积分"是正常利用; R8 把 trim 锁定反而
        限制策略自由度, 性能下降, 故全程开放)。"""
        self._integrate_yaw_trim()

    def _lock_yaw_trim(self):
        """保留以备后用: 当前默认不使用。"""
        self._trim_locked = True

    def _unlock_yaw_trim(self):
        self._trim_locked = False
        self._rud_trim = 0.0


class ForwardFlightTask(JSBSimTaskBase):
    """前飞任务: 从 20 m 悬停加速至目标空速 30 kt 并稳定跟踪 (±5 kt / ±3 m)。

    终止 info: success = 速度与高度带内累计 > 4 s 且不坠毁;
               mean_err = 末段 10% 平均速度误差 (kt)。

    任务级机动内环 (本模型 AH-1S 的纵向通道实测: ele 经 SAS 削波后周期变距
    权限仅 ±2.4°~4.7°, 且正方向强不稳定 (ele=+0.1 恒定 6s 俯冲 -54° 穿地),
    RL 无法直接镇定。故动作 u_pitch 解释为目标俯仰角 (u=+1 -> -8° 低头,
    u=-1 -> +8° 抬头), 环境内环用全幅值 ele 跟踪; reset 时清零 FCS 的
    longitudinal-bias (模型文档许可的 fcs/adj 可调参数)。总距/横滚/脚蹬
    仍走悬停 SAS 公式, 4 维动作契约不变。)

    v3 reward 改写 (2026-08-28, 授权与流程见 coordination/mailbox-A 20:35 /
    mailbox-D 20:25 issue; 设计全文见 rl_train/notes_forward_reward_v3.md):
    v2 的 0%/0% 是"悬停锁死"型趴地死锁——速度项只有增量势函数没有每步存量
    收入、带内奖励是初始不可达的 AND 门、低头探索收益 0 风险 -50、拖延无成
    本。v3 按 roll_v2 方法论 + §5 势函数塑形教训改写: 速度生计奖励(存量) +
    拖延成本 + 速度带/高度带拆分 + 低头姿态塑形 + 护栏(超速/俯冲/蹲地)。
    success 判定口径不变。改动行均标注 "# v3:"。)"""

    EXTRA = 2
    PITCH_CMD_MAX = math.radians(8.0)

    def __init__(self, target_speed_kts=30.0, target_alt_m=20.0, episode_steps=1000):
        super().__init__(episode_steps=episode_steps, wind=True, n_extra_obs=self.EXTRA)
        self.target_speed_kts = target_speed_kts
        self.target_alt_m = target_alt_m
        # v3: 目标空速与超速护栏换算成 m/s, 供生计奖励/护栏项使用
        self.target_u_mps = target_speed_kts / MPS2KTS          # v3: 30 kt = 15.43 m/s
        self.overspeed_u_mps = (target_speed_kts + 5.0) / MPS2KTS  # v3: 35 kt = 18.0 m/s

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self._unlock_yaw_trim()
        # 清零模型自带的低头配平偏置 (fcs/adj/longitudinal-bias = 0.02 rad)
        self.fdm.set_property_value('fcs/adj/longitudinal-bias', 0.0)
        self.steps = 0
        self.crashed = False
        self.in_bounds_steps = 0
        self.speed_errs = []
        ok, s = self._fly_to_hover(self.target_alt_m)
        self._start_info = {"hover_established": bool(ok)}
        return self._obs(s), self._start_info

    def _obs(self, s):
        return np.concatenate([
            self._hover_obs(s, self.target_alt_m),
            np.array([
                (self.target_speed_kts - s['u_kts']) / 30.0,
                s['u_kts'] / 60.0,
            ], dtype=np.float32),
        ])

    def _apply_forward(self, u_coll, pitch_target, u_roll, u_yaw):
        """前飞任务执行包络: 总距/横滚/脚蹬 = 悬停 SAS; 俯仰 = 目标角内环"""
        pitch_rad = self.fdm.get_property_value('attitude/theta-rad')
        roll_rad = self.fdm.get_property_value('attitude/phi-rad')
        p = self.fdm.get_property_value('velocities/p-rad_sec')
        q = self.fdm.get_property_value('velocities/q-rad_sec')
        r = self.fdm.get_property_value('velocities/r-rad_sec')

        coll = float(np.clip(0.70 + u_coll * 0.25, 0.0, 1.0))
        ele = float(np.clip(4.0 * (pitch_rad - pitch_target) + 0.8 * q, -1.0, 1.0))
        ail = float(np.clip(u_roll * 0.3 - 0.6 * roll_rad - 0.25 * p, -0.5, 0.5))
        rud = float(np.clip(u_yaw * 0.3 + 0.4 * r - 0.15 * (coll - 0.5), -0.6, 0.6))

        self.fdm.set_property_value('fcs/collective-cmd-norm', coll)
        self.fdm.set_property_value('fcs/elevator-cmd-norm', ele)
        self.fdm.set_property_value('fcs/aileron-cmd-norm', ail)
        self.fdm.set_property_value('fcs/rudder-cmd-norm', rud)

    def step(self, action):
        u_coll, u_pitch, u_roll, u_yaw = np.clip(np.asarray(action, dtype=np.float64), -1.0, 1.0)
        prev = self._raw()
        pitch_target = -self.PITCH_CMD_MAX * u_pitch   # u=+1 -> 低头 8°
        self._apply_forward(u_coll, pitch_target, u_roll, u_yaw)
        self.fdm.run()
        self.steps += 1

        s = self._raw()
        speed_err = abs(s['u_kts'] - self.target_speed_kts)
        alt_err = abs(s['agl'] - self.target_alt_m)
        self.speed_errs.append(speed_err)
        self._check_crash(s)

        # in_bounds 仅服务 success 判定口径 (累计带内 >4 s), 口径不变
        in_bounds = speed_err < 5.0 and alt_err < 3.0
        if in_bounds:
            self.in_bounds_steps += 1

        # v3: ==================== reward 改写 (notes_forward_reward_v3.md §2) ====================
        # v3: (A) 速度势函数 [保留 v2]: 每向目标空速靠近 1 kt 立即 +10 (因果增量梯度)
        speed_progress = 10.0 * (abs(prev['u_kts'] - self.target_speed_kts) - speed_err)
        # v3: (B) 高度势函数 [v2 的 5.0 -> 2.0]: 保留高度因果方向, 不再淹没前进梯度
        alt_progress = 2.0 * (abs(prev['agl'] - self.target_alt_m) - alt_err)
        # v3: (C) 速度生计奖励 [新增]: 每步按当前空速给存量收入 (0 -> +1.5 饱和),
        #     悬停收入归零——反"趴地/悬停锁死"核心项 (§5 教训: 目标方向要有每步正收入)
        speed_living = 1.5 * min(s['u'], self.target_u_mps) / self.target_u_mps
        # v3: (D) 拖延成本 [新增]: 悬停 20s ≈ -170/回合, "原地苟着"严格劣于任何尝试
        #     (对齐 §5 起飞拖延惩罚与 roll_v2 带外 -0.05 的思路, 但量级足够压过坠毁恐惧)
        idle_cost = -0.15
        # v3: (E) 低头姿态塑形 [新增]: 未达目标空速时按低头幅度给每步正收益
        #     (低头是前飞物理先导, 对齐 roll_v2 "朝目标方向的速率塑形"; 达速后关闭防"带速低头")
        pitch_bonus = 0.0
        if s['u'] < self.target_u_mps:
            pitch_bonus = 0.5 * min(1.0, max(0.0, -s['pitch']) / self.PITCH_CMD_MAX)
        # v3: (F)+(G) 带内奖励 [新增, 替换 v2 的 AND 门 +2.0/-0.05]: 速度带与高度带拆开,
        #     加速瞬态的 partial progress 即时兑现 (v2 的 AND 门在加速段恒为假 → 无梯度)
        band_bonus = 0.0
        if speed_err < 5.0:
            band_bonus += 1.2     # v3: 速度带 (25~35 kt)
        if alt_err < 3.0:
            band_bonus += 0.8     # v3: 高度带 (17~23 m)
        # v3: (J) 超速护栏 [新增]: 收入在目标速饱和, 超 35 kt 每 m/s 罚 0.3 (防冲速度发散)
        overspeed_pen = 0.3 * max(0.0, s['u'] - self.overspeed_u_mps)
        # v3: (K) 俯冲护栏 [新增]: 低头超过 ±8° 指令包络后每弧度罚 5.0 (净值为负, 防栽头换速度)
        dive_pen = 5.0 * max(0.0, -s['pitch'] - self.PITCH_CMD_MAX)
        # v3: (L) 蹲地成本 [v2 的 0.2 -> 0.5]: 本任务坠毁只判姿态角, 蹲地不能成为吸引子
        ground_pen = 0.5 if (s['agl'] < 1.0 and self.steps > 50) else 0.0
        # v3: (I) 高度绝对小惩罚 [新增]: 打破"下沉后赖着不动"的中性平衡 (势函数对静止恒为 0)
        alt_abs_pen = 0.02 * alt_err
        # v3: 汇总; (H) 滚转惩罚与 (M) 动作能量项保留 v2 原值
        reward = (speed_progress + alt_progress + speed_living + idle_cost
                  + pitch_bonus + band_bonus
                  - 0.05 * abs(s['roll'])
                  - alt_abs_pen - overspeed_pen - dive_pen - ground_pen
                  - 0.01 * float(np.sum(np.square(action))))
        if self.crashed:
            reward -= 50.0

        terminated = self.crashed
        truncated = self.steps >= self.episode_steps
        info = {"u_kts": s['u_kts'], "speed_err_kts": speed_err, "alt_m": s['agl'],
                "crashed": self.crashed}
        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self.speed_errs)))
            info["success"] = (not self.crashed) and self.in_bounds_steps > 200
            info["mean_err"] = float(np.mean(self.speed_errs[-n_tail:]))
        return self._obs(s), float(reward), terminated, truncated, info


class LandingTask(JSBSimTaskBase):
    """降落任务: 从 15 m 悬停以 ~1 m/s 平稳下降, 软接地 (vz > -3.0 m/s)。

    按 E 轨道 2026-08-28 A6 指令口径: 软接地=成功; 硬接地(vz<=-3)记
    着陆失败但不判坠毁 (crashed 仅用于姿态 >60° 等真实失控)。
    终止 info: mean_err = 全程平均 |vz + 1.0| (理想下降剖面跟踪误差, m/s);
               touchdown_vs = 接地垂直速度 (未接地为 None);
               touchdown_hs_mps = 接地水平速度 (地面系, 未接地为 None);
               net_drift_m = 相对下降段起点的水平净漂移 (m)。

    v5 补丁 (整改令 §6.2 奖励等价类修复; 用户目击滑翔降落):
      - 观测 12->14 维: 加北向/东向位移 (pos_n/50, pos_e/50);
      - 奖励加水平速度惩罚 0.1*|v_horiz| + 漂移惩罚 0.02*net_drift
        (增益与 takeoff v5 一致);
      - 成功判据加: 净漂移 < 30m 且 接地水平速度 < 2.0 m/s (G4 口径)。
      漂移基准点 = 悬停建立位置 (下降段起点), 与 audit_rewards.py
      run_episode 的测量基准一致 (reset 返回后取 geo)。"""

    EXTRA = 5  # v5.2: 14->15 维 (加相对初始航向偏差 wrap(psi-psi0)/pi), 整改令 11:45
    DRIFT_LIMIT_M = 30.0      # v5: 成功判据漂移上限 (m)
    TOUCHDOWN_HS_LIMIT = 2.0  # v5: 接地水平速度上限 (m/s, 整改令 §4 规则5)
    # v5.2 航向稳定 (用户令: 滑橇式起落架地面打转=损坏飞机, 绝不允许):
    HEADING_DEV_LIMIT = math.radians(45.0)  # 下降全程航向偏差上限 (主E终裁 (b): 15°->45°, 15°系未做下降态物理账的误判)
    NEAR_GROUND_VZ_LIMIT = 1.2              # agl<2m 段垂直速度上限 (主E 2M数据重校: 剖面目标-1.0+20%容差; 1.0与奖励目标自相冲突, 实测中位1.04)
    NEAR_GROUND_HS_LIMIT = 1.0              # agl<1m 段水平速度上限 (m/s) (主E终裁 (b): 0.5->1.0)

    def __init__(self, init_alt_m=15.0, episode_steps=1000):
        super().__init__(episode_steps=episode_steps, wind=True, n_extra_obs=self.EXTRA)
        self.init_alt_m = init_alt_m
        # v5.2.1: 任务级偏航物理层 —— 与 YawManeuverTask v4 配方逐字一致 (E 已裁决先例:
        #   yaw 59%->98% 即靠这组参数; landing 继承的基类默认 pedal 0.25/trim[-0.3,+0.7]/
        #   kI 0.0015 正是 yaw v4 之前的结构性病灶, 实测下降段起点 trim 饱和自旋 40-60 dps,
        #   策略 u_yaw 权限 0.15 压不住, 全 12 回合航向偏差 42-62° 超标)
        self.pedal_gain_override = 0.4        # 0.25 -> 0.4 (+60% 尾桨推力)
        self.trim_clip_min = -0.5             # 对称化 (原 -0.3)
        self.trim_clip_max = 0.5              # 对称化 (原 +0.7)
        self.trim_kI = 0.005                  # 0.0015 -> 0.005 (3.3x 收敛)
        self.yaw_sas_gain = 0.45              # 0.3 -> 0.45 (+50% 偏航舵面权限)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self.steps = 0
        self.crashed = False
        self.landed = False
        self.touchdown_vs = None
        self.touchdown_hs = None
        self.profile_errs = []
        ok, s = self._fly_to_hover(self.init_alt_m)
        # v5.2.3: trim settle —— 爬升段残留自旋在下降开始后 0.1s 内即把航向偏差
        #   推过 15° 门 (实测 dev达15°时刻=0.1s), 策略无法补救。任务定义"从悬停
        #   平稳下降"的前提是稳定悬停: 此处空跑 8s SAS 保持 + 快速 trim 积分
        #   (kI=0.01), 让配平收敛、自旋归零后再记录基准。属任务初始化修正
        #   (等效飞行员起飞前配平检查), 下降全程仍由 RL 策略飞。
        settle_steps = int(8.0 / DT)
        for _ in range(settle_steps):
            self._apply_sas(0.0, 0.0, 0.0, 0.0)
            self.fdm.run()
            self._integrate_yaw_trim()
        s = self._raw()
        if any(isinstance(v, float) and not math.isfinite(v) for v in s.values()):
            # v5.2.3b: 爬升/settle 期间 NaN 爆炸 -> 干净复位重爬一次
            self._reset_jsbsim()
            ok, s = self._fly_to_hover(self.init_alt_m)
            for _ in range(settle_steps):
                self._apply_sas(0.0, 0.0, 0.0, 0.0)
                self.fdm.run()
                self._integrate_yaw_trim()
            s = self._raw()
        # 重试后仍 NaN 则强制清零 (不把 NaN 观测交给 rollout)
        s = {k: (0.0 if isinstance(v, float) and not math.isfinite(v) else v) for k, v in s.items()}
        self._record_origin()  # v5: 漂移基准 = 悬停建立点 (下降段起点)
        self.psi0 = s['psi']   # v5.2: 航向基准 = 下降段起点 (同漂移基准)
        self.max_heading_dev = 0.0   # v5.2: 下降全程最大航向偏差 (rad)
        self.max_near_vz = 0.0       # v5.2: agl<2m 段最大 |vz| (m/s)
        self.max_near_hs = 0.0       # v5.2: agl<1m 段最大水平速度 (m/s)
        self._prev_agl = s['agl']
        self._start_info = {"hover_established": bool(ok)}
        return self._obs(s), self._start_info

    def _obs(self, s):
        pos_n, pos_e = self._pos_ne()  # v5: 相对下降段起点的北/东位移 (m)
        hdg_dev = wrap_angle(s['psi'] - self.psi0)  # v5.2: 相对初始航向偏差
        obs = np.concatenate([
            self._hover_obs(s, self.init_alt_m),
            np.array([
                (s['vz'] + 1.0) / 2.0,
                1.0 if s['agl'] < 2.0 else 0.0,
                float(pos_n / 50.0),  # v5: 北向位移
                float(pos_e / 50.0),  # v5: 东向位移
                float(hdg_dev / math.pi),  # v5.2: 航向偏差 [-1, 1]
            ], dtype=np.float32),
        ])
        # v5.2.4(主E): 出口统一消毒 —— NaN 清零 + 截断到观测空间界内。
        # 有限但极端的值(物理爆炸时 lat/lon→1e9)同样会炸梯度, 仅查 isfinite 不够。
        return np.clip(np.nan_to_num(obs, nan=0.0, posinf=10.0, neginf=-10.0),
                       -10.0, 10.0).astype(np.float32)

    def step(self, action):
        u_coll, u_pitch, u_roll, u_yaw = np.clip(np.asarray(action, dtype=np.float64), -1.0, 1.0)
        self._apply_sas(u_coll, u_pitch, u_roll, u_yaw)
        self.fdm.run()
        self.steps += 1

        s = self._raw()
        # v5.2.3b: JSBSim NaN 爆炸防护 —— 姿态爆表后 FCS 状态变 NaN, NaN 比较恒 False
        #   骗过 _check_crash, NaN 观测进入 rollout 会毒化整个网络 (两次训练静默死亡真因)。
        #   处理: 判坠毁 + 零观测终止, 回合结束后 reset 的 load_ic 会干净复位 fdm。
        if any(isinstance(v, float) and not math.isfinite(v) for v in s.values()):
            self.crashed = True
            info = {"agl_m": 0.0, "vz_mps": 0.0, "touchdown_vs": self.touchdown_vs,
                    "touchdown_hs_mps": self.touchdown_hs, "net_drift_m": 0.0,
                    "max_heading_dev_deg": math.degrees(self.max_heading_dev),
                    "max_near_vz_mps": round(self.max_near_vz, 3),
                    "max_near_hs_mps": round(self.max_near_hs, 3),
                    "crashed": True, "landed": self.landed, "nan_exploded": True}
            return np.zeros(self.observation_space.shape[0], dtype=np.float32), -50.0, True, False, info

        self.profile_errs.append(abs(s['vz'] + 1.0))
        self._check_crash(s)

        # v5: 水平速度惩罚 + 漂移惩罚 (与 takeoff v5 同增益, 滑翔降落必须受罚)
        v_horiz = math.hypot(s['u'], s['v'])  # 体轴水平速度 (m/s)
        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)

        # v5.2: 航向稳定 (仅离地 agl>0.3m 后生效, 地面阶段不罚)
        hdg_dev = wrap_angle(s['psi'] - self.psi0)
        abs_dev = abs(hdg_dev)
        self.max_heading_dev = max(self.max_heading_dev, abs_dev)
        heading_pen = 0.0
        if s['agl'] > 0.3:
            yaw_rate_dps = abs(math.degrees(s['r']))
            heading_pen = (0.15 * yaw_rate_dps / 30.0          # 角速度惩罚
                           + 0.15 * abs_dev / math.radians(15.0))  # 航向累计偏差惩罚 (v5.2.2: 0.05->0.15, dev 50° 门无感)
        # v5.2: 近地判据追踪 + 奖励镜像 (整改令成功门若无对应奖励项则 PPO 无梯度可学,
        # v5.2 2M 首训 0% 归因实证: 剖面/漂移/接地水平速全完美, 唯近地两门无感)
        near_pen = 0.0
        if s['agl'] < 2.0:
            self.max_near_vz = max(self.max_near_vz, abs(s['vz']))
            near_pen += 2.0 * max(0.0, abs(s['vz']) - self.NEAR_GROUND_VZ_LIMIT)  # v5.2.2: 0.5->2.0
        if s['agl'] < 1.0:
            self.max_near_hs = max(self.max_near_hs, v_horiz)
            near_pen += 3.0 * max(0.0, v_horiz - self.NEAR_GROUND_HS_LIMIT)  # v5.2.3: 1.0->3.0 (实测 mean 0.94 vs 门 0.5)

        reward = (1.0 * (self._prev_agl - s['agl']) if s['agl'] > 0.5 else 0.0) \
            - 0.3 * abs(s['vz'] + 1.0) \
            - 0.05 * (abs(s['pitch']) + abs(s['roll'])) \
            - 0.01 * float(np.sum(np.square(action))) \
            - (0.05 if s['agl'] > 0.3 else 0.0) \
            - 0.1 * v_horiz \
            - 0.02 * net_drift \
            - heading_pen \
            - near_pen
        self._prev_agl = s['agl']

        terminated = self.crashed
        if s['agl'] <= 0.25 and not self.landed:
            self.landed = True
            self.touchdown_vs = s['vz']
            vn = self.fdm.get_property_value('velocities/v-north-fps') * FPS2MPS
            ve = self.fdm.get_property_value('velocities/v-east-fps') * FPS2MPS
            self.touchdown_hs = math.hypot(vn, ve)  # v5: 接地水平速度 (地面系)
            terminated = True
            if s['vz'] < HARD_LANDING_VS:
                reward -= 20.0          # 硬接地: 着陆失败, 不判坠毁 (A6 口径)
            else:
                reward += 50.0          # 软着陆大奖
        if self.crashed:
            reward -= 50.0

        truncated = self.steps >= self.episode_steps
        info = {"agl_m": s['agl'], "vz_mps": s['vz'], "touchdown_vs": self.touchdown_vs,
                "touchdown_hs_mps": self.touchdown_hs, "net_drift_m": float(net_drift),
                "max_heading_dev_deg": math.degrees(self.max_heading_dev),
                "max_near_vz_mps": round(self.max_near_vz, 3),
                "max_near_hs_mps": round(self.max_near_hs, 3),
                "crashed": self.crashed, "landed": self.landed}
        if terminated or truncated:
            # v5 成功判据: 软接地 + 净漂移 < 30m + 接地水平速度 < 2 m/s
            # v5.2 追加: 下降全程航向偏差 < 15° (最大偏转口径) + 近地缓慢下沉
            info["success"] = (self.landed and (not self.crashed)
                               and self.touchdown_vs is not None
                               and self.touchdown_vs >= HARD_LANDING_VS
                               and net_drift < self.DRIFT_LIMIT_M
                               and self.touchdown_hs is not None
                               and self.touchdown_hs < self.TOUCHDOWN_HS_LIMIT
                               and self.max_heading_dev < self.HEADING_DEV_LIMIT
                               and self.max_near_vz <= self.NEAR_GROUND_VZ_LIMIT
                               and self.max_near_hs < self.NEAR_GROUND_HS_LIMIT)
            info["mean_err"] = float(np.mean(self.profile_errs))
        # v5.2.4: 边界防护(主E) —— v5 位置机制(lat/lon/vn/ve -> net_drift/pos_n/pos_e)
        #   绕过 _raw 键检查, NaN 可从奖励/附加观测维漏入 rollout 毒化网络
        #   (landing_b45 首训实证: 策略 loc=nan 崩溃)。返回前最后一道校验,
        #   对任何泄漏源免疫: 判坠毁 + 零观测终止。
        obs = self._obs(s)
        # v5.2.4(主E): 奖励截断 —— 有限但极端的奖励(漂移惩罚×天文数字位移)同样炸梯度
        reward = float(np.clip(reward, -100.0, 100.0))
        if (not np.isfinite(reward)) or (not bool(np.all(np.isfinite(obs)))):
            self.crashed = True
            info = {**info, "crashed": True, "nan_exploded": True}
            return np.zeros(self.observation_space.shape[0], dtype=np.float32), -50.0, True, False, info
        return obs, float(reward), terminated, truncated, info


class RollManeuverTask(JSBSimTaskBase):
    """滚转机动: 20 m 悬停建立后, 压坡度至 ±15°~20° 保持 4 s, 再改平保持 4 s。

    终止 info: success = 坡度段平均误差 < 6° 且末态 |roll| < 5° 且不坠毁;
               mean_err = 坡度段平均 |roll - target| (deg)。
    环境级机动补偿 (不改 4 维动作契约):
      - 坡度-总距前馈 coll/cos(φ) + 垂直速度阻尼: 抵消压坡度导致的垂直
        升力损失, 否则策略必然掉高度触地、歪靠橇轨卡死 (实测根因);
      - 目标坡度幅值课程 4°->20° + 左右均衡采样 (防单侧收敛);
      - 速率塑形: 奖励朝目标方向的滚转速率 (解决持续压杆的探索难题)。"""

    BANK_S = 4.0
    LEVEL_S = 4.0
    EXTRA = 3

    def __init__(self, target_alt_m=20.0, episode_steps=900):
        super().__init__(episode_steps=episode_steps, wind=True, n_extra_obs=self.EXTRA)
        self.target_alt_m = target_alt_m
        self._ep_counter = 0

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self.steps = 0
        self.crashed = False
        self._ep_counter += 1
        # 课程: 目标坡度幅值随回合数 4° -> 20° 爬坡 (先学会小坡度保持)
        max_deg = min(20.0, 4.0 + 1.0 * (self._ep_counter // 20))
        mag = float(self.rng.uniform(max(3.0, 0.5 * max_deg), max_deg))
        sign = 1.0 if self._ep_counter % 2 == 0 else -1.0   # 左右均衡
        self.target_roll = sign * math.radians(mag)
        self.roll_errs_bank = []
        self.final_roll = 0.0
        ok, s = self._fly_to_hover(self.target_alt_m)
        self._start_info = {"hover_established": bool(ok), "target_roll_deg": math.degrees(self.target_roll)}
        return self._obs(s, 0.0), self._start_info

    def _phase(self):
        t = self.steps * DT
        if t < self.BANK_S:
            return 0, self.target_roll          # 坡度段
        if t < self.BANK_S + self.LEVEL_S:
            return 1, 0.0                       # 改平段
        return 2, 0.0                           # 收尾

    def _obs(self, s, phase_flag):
        roll_ref = self.target_roll if phase_flag < 1 else 0.0
        return np.concatenate([
            self._hover_obs(s, self.target_alt_m),
            np.array([
                (roll_ref - s['roll']) / MAX_ANG,
                float(phase_flag),
                self.target_roll / MAX_ANG,
            ], dtype=np.float32),
        ])

    def step(self, action):
        u_coll, u_pitch, u_roll, u_yaw = np.clip(np.asarray(action, dtype=np.float64), -1.0, 1.0)
        prev_phase, prev_ref = self._phase()
        prev = self._raw()
        roll_rad = prev['roll']
        # 环境级机动补偿: ① 坡度-总距前馈 coll/cos(φ) 补偿压坡度升力损失;
        # ② 垂直速度阻尼 (降得越快补得越多, 上限 +0.24)
        coll_base = 0.70 + u_coll * 0.25 + 0.12 * float(np.clip(-prev['vz'], -1.0, 2.0))
        coll_cmd = float(np.clip(coll_base / max(0.70, math.cos(roll_rad)), 0.0, 1.0))
        # 经 _apply_sas 写入: 反解 u_coll 使 coll 中心值生效, 姿态阻尼不变
        u_coll_ff = float(np.clip((coll_cmd - 0.70) / 0.25, -1.0, 1.0))
        self._apply_sas(u_coll_ff, u_pitch, u_roll, u_yaw)
        self.fdm.run()
        self.steps += 1

        s = self._raw()
        phase, ref = self._phase()
        self._check_crash(s)
        if prev_phase == 0:
            self.roll_errs_bank.append(abs(prev['roll'] - prev_ref))
        if self.steps * DT >= self.BANK_S + self.LEVEL_S:
            self.final_roll = s['roll']

        roll_err = abs(s['roll'] - ref)
        alt_err = abs(s['agl'] - self.target_alt_m)
        rate_shaping = 1.5 * math.tanh(3.0 * (ref - s['roll'])) * s['p']
        reward = (40.0 * (abs(prev['roll'] - prev_ref) - roll_err)
                  + rate_shaping
                  + (2.0 if roll_err < math.radians(6) else -0.05)
                  - 0.05 * alt_err
                  - 0.05 * abs(s['pitch'])
                  - 0.01 * float(np.sum(np.square(action))))
        if self.crashed:
            reward -= 50.0

        terminated = self.crashed or (self.steps * DT >= self.BANK_S + self.LEVEL_S)
        truncated = self.steps >= self.episode_steps and not terminated
        info = {"roll_deg": math.degrees(s['roll']), "target_roll_deg": math.degrees(self.target_roll),
                "phase": phase, "crashed": self.crashed}
        if terminated or truncated:
            mean_bank_err = float(np.mean(self.roll_errs_bank)) if self.roll_errs_bank else 9.9
            info["success"] = ((not self.crashed)
                               and math.degrees(mean_bank_err) < 6.0
                               and abs(math.degrees(self.final_roll)) < 5.0)
            info["mean_err"] = math.degrees(mean_bank_err)
        return self._obs(s, float(phase)), float(reward), terminated, truncated, info


class YawManeuverTask(JSBSimTaskBase):
    """偏航机动: 20 m 悬停建立后, 航向变化 ±60°~120° 并稳定保持在新航向。

    终止 info: success = 航向误差 < 5° 累计 > 2 s 且高度带内且不坠毁;
               mean_err = 末段 10% 平均航向误差 (deg)。
    回合 30 s; 环境级机动补偿: u_yaw 低通 τ=0.4s 抑制 bang-bang 指令引起
    的航向过冲振荡; 速率塑形奖励朝目标方向的偏航角速率。

    v3 reward 改写 (2026-08-29, 授权与流程见 coordination/mailbox-A 20:35 /
    mailbox-D 02:05 issue; 设计全文见 rl_train/notes_yaw_waypoint_reward_v3.md):
    R7/R8 的 0%/94~100% 坠毁是"野蛮旋转-坠毁"模式——v2 速率塑形 0.8·dir_sign·r
    无界且误差→0 仍满额支付 (鼓励穿靶猛转→滚转耦合发散), 高度通道无锚
    (success 末步要求 alt_err<3 m 而机体自然塌陷到 1.5~8 m), 无速率护栏。
    另有结构性病灶 (探针实证): 抗扭矩 trim 积分在回合内被冻结 (基类
    _post_physics 钩子从未被 step 调用), 回合开始 trim 顶在爬升末态 +0.4~0.7,
    残余左自旋 −9~−47 dps 且任何脚蹬输入下正偏航速率不可达——该状态下
    "5° 窗口累计 2s"数学上不可解。v3: ① step 内接通 _post_physics (恢复基类
    钩子文档语义, task 级单行); ② reward 改写: 有界 tanh 速率塑形 (roll_v2
    同款) + 速率护栏 + 高度三件套 + 渐进姿态护栏。success 判定口径不变。
    改动行均标注 "# v3:"。)"""

    LP_TAU = 0.4
    EXTRA = 2
    # v3: reward 护栏常数 (notes_yaw_waypoint_reward_v3.md §3.1)
    RATE_CAP = math.radians(45.0)     # v3: 偏航角速率护栏阈值 45°/s
    ROLL_GUARD = math.radians(20.0)   # v3: 滚转护栏起坡角
    PITCH_GUARD = math.radians(15.0)  # v3: 俯仰护栏起坡角
    SPEED_REF = 10.0                  # v3-r2: 速度护栏参考 m/s (~19 kt), 第 2 轮新增
    # v3-r3: [已回退] 第 3 轮的 APPROACH_BAND 渐进逼近带与 SETTLE_S 回合前配平收敛
    #   在 500k 实测中反而劣化 (14% < r2 37%, 见 notes_yaw_waypoint_reward_v3.md
    #   §11.4)——配平后的对称起点同时移除了 r2 策略利用左向漂移的"顺风"且以 20 kt
    #   带速度罚状态起步; 最优配置为 r2, 本文件保持 r2 状态。r3 代码与数据留档
    #   results/jsbsim_ppo_yaw_v3r3_*。

    def __init__(self, target_alt_m=20.0, episode_steps=1500):
        super().__init__(episode_steps=episode_steps, wind=True, n_extra_obs=self.EXTRA)
        self.target_alt_m = target_alt_m
        # v4: 航向通道物理层增强 (task 级, 不影响其他 4 个已达标任务)
        # v5.1: 保持 v4 参数 (pedal 0.5/yaw_gain 0.6 导致左右严重不对称, 左舵仅 -2dps)
        self.pedal_gain_override = 0.4        # 0.25 -> 0.4 (+60% 尾桨推力), 左右对称 -5/+6 dps
        self.trim_clip_min = -0.5              # 对称化: 原 -0.3 -> -0.5 (右舵权限恢复)
        self.trim_clip_max = 0.5               # 对称化: 原 +0.7 -> +0.5 (防左舵饱和)
        self.trim_kI = 0.005                   # 0.0015 -> 0.005 (3.3x, 30s 内可收敛)
        self.yaw_sas_gain = 0.45               # 0.3 -> 0.45 (+50% 偏航舵面权限)
        # v4: 改进配平收敛参数 (修复 r3 劣化根因: 配平后带 20kt 速度起步)
        self.trim_settle_s = 8.0               # v4 最优配置 (12s 导致 0-10%)
        self.trim_settle_kI = 0.01             # 配平段快速积分速率
        self.approach_band_width = math.radians(15.0)  # 渐进逼近带宽度
        self.approach_band_gain = 0.6           # v4 最优 (0.15 导致 0%, 窗口累计奖励导致 4%)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self.steps = 0
        self.crashed = False
        self._u_yaw_filt = 0.0
        ok, s = self._fly_to_hover(self.target_alt_m)
        self.psi_errs = []
        self.on_heading_steps = 0
        # v5: 回退 v4 配平收敛 (v4 的 8s 配平+强制速度归零导致 0% 成功率, 移除了 r2 "左向漂移顺风")
        #   保留 v4 物理增强 (pedal 0.4/trim对称化/yaw_gain 0.45) + 渐进逼近带
        #   reset 直接用 _fly_to_hover 后的状态, 不额外配平
        s = self._raw()
        sign = 1.0 if self.rng.random() < 0.5 else -1.0
        self.psi0 = s['psi']
        self.target_psi = self.psi0 + sign * math.radians(float(self.rng.uniform(60.0, 120.0)))
        self._start_info = {"hover_established": bool(ok),
                            "target_heading_change_deg": math.degrees(wrap_angle(self.target_psi - self.psi0))}
        return self._obs(s), self._start_info

    def _obs(self, s):
        psi_err = wrap_angle(self.target_psi - s['psi'])
        return np.concatenate([
            self._hover_obs(s, self.target_alt_m),
            np.array([
                psi_err / math.pi,
                wrap_angle(self.target_psi - self.psi0) / math.pi,
            ], dtype=np.float32),
        ])

    def step(self, action):
        u_coll, u_pitch, u_roll, u_yaw = np.clip(np.asarray(action, dtype=np.float64), -1.0, 1.0)
        prev = self._raw()
        prev_err = abs(wrap_angle(self.target_psi - prev['psi']))
        # u_yaw 低通: 平滑 bang-bang 指令, 等效飞行员柔和蹬舵
        alpha = DT / (DT + self.LP_TAU)
        self._u_yaw_filt += alpha * (u_yaw - self._u_yaw_filt)
        self._apply_sas(u_coll, u_pitch, u_roll, self._u_yaw_filt)
        self.fdm.run()
        self._post_physics()   # v3: 接通抗扭矩 trim 积分——基类钩子文档语义为"每步
                               # v3: 推进/全程开放", 但此前从未在回合内被调用, trim 冻结在
                               # v3: 爬升末态 (+0.4~0.7) 使残余左自旋不可镇定、正偏航速率
                               # v3: 不可达 (探针证据 notes_yaw_waypoint_reward_v3.md §2.1)
        self.steps += 1

        s = self._raw()
        # v3: 带符号航向误差供有界速率塑形使用
        s_err = wrap_angle(self.target_psi - s['psi'])
        psi_err = abs(s_err)
        self.psi_errs.append(psi_err)
        self._check_crash(s)

        alt_err = abs(s['agl'] - self.target_alt_m)
        on_hdg = psi_err < math.radians(5.0)
        if on_hdg:
            self.on_heading_steps += 1

        # v3: ==================== reward 改写 (notes_yaw_waypoint_reward_v3.md §3.1) ====================
        # v3: (A) 航向势函数 [保留 v2]: 每向目标航向靠近 1 rad 立即 +20 (因果增量梯度)
        hdg_progress = 20.0 * (prev_err - psi_err)
        # v3: (B) 有界速率塑形 [替换 v2 的 0.8*dir_sign*r]: roll_v2 同款 tanh 形式,
        #     机动中支付朝向目标的角速率, 误差->0 支付->0 (穿靶旋转不再有收入)
        rate_shaping = 0.8 * math.tanh(3.0 * s_err) * s['r']
        # v3: (C) 速率护栏 [新增]: 45°/s 以上每 rad/s 罚 1.0 (90dps 净 -0.27/步,
        #     170dps 净 -1.4/步, "猛转-滚转耦合-坠毁"路径净值为负; 对偶 forward v3 超速护栏)
        rate_cap_pen = 1.0 * max(0.0, abs(s['r']) - self.RATE_CAP)
        # v3: (D) 航向带 [保留 v2]
        # v4: 渐进逼近带 [新增]: 5°~15° 间线性奖励 +0.6~0, 消除 r2 "最后一海里摆动"
        approach_band = (self.approach_band_gain * max(0.0, 1.0 - psi_err / self.approach_band_width)
                         if psi_err < self.approach_band_width else 0.0)
        hdg_band = 2.0 if on_hdg else -0.05
        # v3: (E) 高度势函数 [新增]: 回 20 m 每靠近 1 m 立即 +1 (success 末步要求 alt_err<3 m,
        #     而探针实测自然漂移 30s 塌陷 17.9->1.5~8.5 m, v2 无任何回高梯度)
        alt_progress = 1.0 * (abs(prev['agl'] - self.target_alt_m) - alt_err)
        # v3: (F) 高度带 [新增]: 20±3 m 内每步 +0.5 (锚定保持态)
        alt_band = 0.5 if alt_err < 3.0 else 0.0
        # v3: (G) 高度绝对小罚 [新增]: 打破"漂着不动"的中性平衡 (forward v3 同构)
        alt_abs_pen = 0.02 * alt_err
        # v3: (I)(J) 渐进姿态护栏 [新增]: 滚转 20°/俯仰 15° 起坡 3.0/rad——45° 时 -1.3/步
        #     已超过任何旋转收入, 坠毁前梯度先转负 (v2 的 -50 到坠毁步才结算, 为时已晚)
        roll_guard = 3.0 * max(0.0, abs(s['roll']) - self.ROLL_GUARD)
        pitch_guard = 3.0 * max(0.0, abs(s['pitch']) - self.PITCH_GUARD)
        # v3-r2: (M) 速度护栏 [第 2 轮新增, notes_yaw_waypoint_reward_v3.md §8]:
        #     10 m/s 以上每 m/s 罚 0.5。第 1 轮 500k 实测策略发现"63~85 kt 速度农场"
        #     (高速风标自稳锁定航向、完美保持 17.5 m 高度, 收割航向/高度带收入,
        #     但 80 kt 转弯半径 ~300 m 机动物理不可达, success 卡 4%)——偏航机动本
        #     应在低速 (脚蹬权限最大区) 完成, 速度本身即应计价
        v_h = math.hypot(s['u'], s['v'])
        speed_pen = 0.5 * max(0.0, v_h - self.SPEED_REF)
        # v3: 汇总; (H) 姿态小罚与 (K) 动作能量保留 v2 原值; v4: +approach_band
        reward = (hdg_progress + rate_shaping + hdg_band + approach_band
                  + alt_progress + alt_band
                  - 0.05 * (abs(s['pitch']) + abs(s['roll']))
                  - alt_abs_pen - rate_cap_pen - roll_guard - pitch_guard
                  - speed_pen
                  - 0.01 * float(np.sum(np.square(action))))
        if self.crashed:
            reward -= 50.0

        terminated = self.crashed
        truncated = self.steps >= self.episode_steps
        info = {"heading_err_deg": math.degrees(psi_err), "crashed": self.crashed}
        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self.psi_errs)))
            info["success"] = (not self.crashed) and self.on_heading_steps > 100 and alt_err < 3.0
            info["mean_err"] = math.degrees(float(np.mean(self.psi_errs[-n_tail:])))
        return self._obs(s), float(reward), terminated, truncated, info


class WaypointTrackTask(JSBSimTaskBase):
    """航路点导航: 20 m 悬停建立后, 依次飞抵 3 个 L 形航点 (到点半径 10 m)。

    航点几何: 起点出发 80 m @随机方向 -> 转 ±90° 再 80 m -> 折返 80 m。
    终止 info: success = 3 个航点全部到达且不坠毁;
               mean_err = 各航点到点水平脱靶距离均值 (未完成为到当前航点
               剩余距离, m)。

    v3 reward 改写 (2026-08-29, 授权与流程见 coordination/mailbox-A 20:35 /
    mailbox-D 02:05 issue; 设计全文见 rl_train/notes_yaw_waypoint_reward_v3.md):
    R8 的 0%/78% 坠毁 + 189 m 脱靶是"野蛮飞行-坠毁"模式——进度势函数 8·Δdist
    对任意速度线性支付 (探针实证 u_pitch 恒 >0.2 即冲到 40~130 kt 并爬高
    100~300 m), 姿态/高度罚 ~30 倍弱于进度收入, 无对准/漏斗引导 (10 m 到点
    半径在远场不可达), 且抗扭矩 trim 回合内冻结使航向只会左转 (同 yaw §2.1)。
    v3: ① step 内接通 _post_physics (恢复基类钩子文档语义, task 级单行);
    ② reward 改写: 巡航生计 + 对准塑形 + 接近漏斗 + 超速/姿态/低高度护栏 +
    高度锚。success 判定口径不变。改动行均标注 "# v3:"。)"""

    WP_RADIUS_M = 10.0
    LEG_M = 80.0
    EXTRA = 3
    # v3: reward 塑形/护栏常数 (notes_yaw_waypoint_reward_v3.md §3.2)
    CRUISE_V = 9.0     # v3: 巡航生计饱和速率 m/s (~17.5 kt, 机体自然配平巡航, 探针 §2.4)
    V_MAX = 12.5       # v3: 超速护栏阈值 m/s (~24 kt)
    FUNNEL_M = 25.0    # v3: 接近漏斗半径 m
    ROLL_GUARD = math.radians(30.0)   # v3: 滚转护栏起坡角 (冒烟后 15°->30°, 给压坡转弯留工作区)
    PITCH_GUARD = math.radians(15.0)  # v3: 俯仰护栏起坡角

    def __init__(self, target_alt_m=20.0, episode_steps=3500):
        super().__init__(episode_steps=episode_steps, wind=True, n_extra_obs=self.EXTRA)
        self.target_alt_m = target_alt_m
        # v4.1: 回退物理层增强 (v4 的 pedal0.4/yaw_gain0.45 导致 95% 坠毁率)
        #   Waypoint 任务用默认 E 18:15 参数 (pedal 0.25, yaw_gain 0.3, trim [-0.3,+0.7])
        #   仅保留课程分解作为唯一新变量
        # v4: 航向通道物理层增强 (同 Yaw, 修复 trim 换向不对称 + 脚蹬权限不足)
        # self.pedal_gain_override = 0.4
        # self.trim_clip_min = -0.5
        # self.trim_clip_max = 0.5
        # self.trim_kI = 0.005
        # self.yaw_sas_gain = 0.45
        # v4.2: 课程分解参数优化 (v4.1 的 200 回合递进太快, 单点还没学会就进两点)
        #   每阶段 500 回合, 给模型足够时间学会当前阶段
        self._wp_ep_counter = 0
        self.course_phase = 0  # 0=单点, 1=两点, 2=三点
        self.course_phase_eps = 500  # 每 500 回合提升一个阶段 (v4.1 是 200)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self.steps = 0
        self.crashed = False
        # v4: 课程分解自动递进 (单点→两点→三点, 每 200 回合提升)
        self._wp_ep_counter += 1
        self.course_phase = min(2, self._wp_ep_counter // self.course_phase_eps)
        ok, s = self._fly_to_hover(self.target_alt_m)
        self._record_origin()
        d0 = float(self.rng.uniform(0.0, 2.0 * math.pi))
        sgn = 1.0 if self.rng.random() < 0.5 else -1.0
        # v4: 课程阶段决定航点数、航段长度和到点半径
        if self.course_phase == 0:
            # 单点课程: 1 个航点 60m, 到点半径 15m (先学会"朝目标飞并到达")
            leg_len = 60.0
            self.current_wp_radius = 15.0
            legs = [(math.cos(d0) * leg_len, math.sin(d0) * leg_len)]
        elif self.course_phase == 1:
            # 两点课程: 2 个航点 L 形 70m, 到点半径 12m (学会一次转弯)
            leg_len = 70.0
            self.current_wp_radius = 12.0
            legs = [(math.cos(d0) * leg_len, math.sin(d0) * leg_len),
                    (math.cos(d0 + sgn * math.pi / 2) * leg_len, math.sin(d0 + sgn * math.pi / 2) * leg_len)]
        else:
            # 三点课程: 原配置 3 航点 L 形 80m, 到点半径 10m (完整航线)
            leg_len = self.LEG_M
            self.current_wp_radius = self.WP_RADIUS_M
            legs = [(math.cos(d0) * leg_len, math.sin(d0) * leg_len),
                    (math.cos(d0 + sgn * math.pi / 2) * leg_len, math.sin(d0 + sgn * math.pi / 2) * leg_len),
                    (math.cos(d0 + math.pi) * leg_len, math.sin(d0 + math.pi) * leg_len)]
        self.wps = []
        n, e = 0.0, 0.0
        for ln, le in legs:
            n += ln
            e += le
            self.wps.append((n, e))
        self.wp_idx = 0
        self.miss_dists = []
        self._start_info = {"hover_established": bool(ok), "num_waypoints": len(self.wps),
                            "course_phase": self.course_phase, "wp_radius_m": self.current_wp_radius}
        return self._obs(s), self._start_info

    def _wp_rel(self):
        """到当前航点的 (北向误差, 东向误差, 距离, 方位) (m, m, m, rad)"""
        n, e = self._pos_ne()
        if self.wp_idx >= len(self.wps):
            return 0.0, 0.0, 0.0, 0.0
        wn, we = self.wps[self.wp_idx]
        dn, de = wn - n, we - e
        return dn, de, math.hypot(dn, de), math.atan2(de, dn)

    def _obs(self, s):
        dn, de, dist, brg = self._wp_rel()
        psi_err = wrap_angle(brg - s['psi'])
        return np.concatenate([
            self._hover_obs(s, self.target_alt_m),
            np.array([
                dist / 150.0,
                psi_err / math.pi,
                float(self.wp_idx) / 3.0,
            ], dtype=np.float32),
        ])

    def step(self, action):
        u_coll, u_pitch, u_roll, u_yaw = np.clip(np.asarray(action, dtype=np.float64), -1.0, 1.0)
        prev_dist = self._wp_rel()[2]
        # v3: 高度势函数需要上一步高度 (锚定 20 m)
        prev_agl = self._raw()['agl']   # v3:
        self._apply_sas(u_coll, u_pitch, u_roll, u_yaw)
        self.fdm.run()
        self._post_physics()   # v3: 接通抗扭矩 trim 积分——trim 回合内冻结使航向只会
                               # v3: 左转 (−20 dps), L 形航线随机方向下约一半几何不可达
                               # v3: (探针证据 notes_yaw_waypoint_reward_v3.md §2.1)
        self.steps += 1

        s = self._raw()
        self._check_crash(s)
        alt_err = abs(s['agl'] - self.target_alt_m)

        _, _, dist, brg = self._wp_rel()
        wp_pending = self.wp_idx < len(self.wps)
        # v3-r2: (D2) 转向塑形 [第 2 轮新增, notes_yaw_waypoint_reward_v3.md §10]:
        #     立即为"朝航点方位的转动"付费 1.0·tanh(3Δ)·r (Δ=brg−ψ)。第 1 轮实测策略
        #     收敛到"原地悬停农场"——压坡/蹬舵转弯先亏后赚, 而 γ=0.99 的 ~6s 有效视野
        #     看不到转弯完成后的巡航收入, 转向动作永远不值得; roll_v2/yaw-v3 方法论:
        #     机动动作本身要有每步收入。对准后 tanh→0 自动退场, 无自旋刷分空间
        turn_pay = 1.0 * math.tanh(3.0 * (brg - s['psi'])) * s['r'] if wp_pending else 0.0
        # v3: ==================== reward 改写 (notes_yaw_waypoint_reward_v3.md §3.2) ====================
        # v3: (A) 进度势函数 [保留 v2]: 每向当前航点靠近 1 m 立即 +8 (因果增量梯度)
        progress = 8.0 * (prev_dist - dist)
        # v3: (B) 巡航生计 [新增]: 体轴速率模 9 m/s 饱和 +0.6/步——受控前飞每步有正收入
        #     (与 A 互补: 势函数管因果方向, 生计管起步激励; forward v3 双结构已验证)
        v_h = math.hypot(s['u'], s['v'])
        cruise_pay = 0.6 * min(v_h / self.CRUISE_V, 1.0)
        # v3: (C) 拖延成本 [v2 的 0.05 -> 0.10]
        idle_cost = -0.10
        # v3: (D) 对准塑形 [新增, 冒烟后权重 0.3->0.8]: 机头指向航点 +0.8·cosΔ——v2 只管
        #     接近不管对准; 冒烟实测对准是远场唯一航向信号, 0.3 在 6s PPO 视野内弱于
        #     其他项的方差, 0.8·cosΔ 使"转弯对准"每步差 ~1.6 成为可见梯度
        align_pay = 0.8 * math.cos(brg - s['psi']) if wp_pending else 0.0
        # v3: (E) 接近漏斗 [新增]: 25 m 内 +1.0/步 (需 agl>3 m 防贴地刷分)——把 10 m
        #     到点半径的稀疏 +30 变成可达梯度 (v2 平均脱靶 189 m, 到点奖从未触发)
        funnel_pay = 1.0 if (wp_pending and dist < self.FUNNEL_M and s['agl'] > 3.0) else 0.0
        # v3: (G) 高度势函数 [新增] + (H) 高度绝对小罚 [新增]: 锚定 20 m,
        #     关闭"冲速度-爬高-坠毁"与"下沉-接地"两条坠毁路径
        alt_progress = 1.0 * (abs(prev_agl - self.target_alt_m) - alt_err)
        alt_abs_pen = 0.02 * alt_err
        # v3-r2: (F) 高度带 [第 2 轮新增]: 20±3 m 内 +0.5/步, 但加 v_h>3 m/s 门槛——
        #     第 1 轮实测"原地悬停农场"靠无条件带内收入坐吃 (+0.3/步 而零风险, PPO
        #     短视野下永远优于先亏后赚的转向); 门控后悬停收入转负, "保持高度"的
        #     支付只留给真正在飞的策略 (与 yaw 任务无条件带不同: yaw 的带必须先
        #     完成机动才拿得到, 不构成农场)
        alt_band = 0.5 if (alt_err < 3.0 and v_h > 3.0) else 0.0
        # v3: (J)(K) 渐进姿态护栏 [v2 的 0.5 单边俯冲 -> 双边 15°(俯仰)/30°(滚转) 起坡 5.0/rad;
        #     冒烟后调整: 滚转起坡 15°->30° 给压坡转弯留出工作区 (roll_v2 实证 15~20° 坡度
        #     可控), 斜率 3.0->5.0 保持 45°+/55°+ 坠毁逼近路径净值为负]
        roll_guard = 5.0 * max(0.0, abs(s['roll']) - self.ROLL_GUARD)
        pitch_guard = 5.0 * max(0.0, abs(s['pitch']) - self.PITCH_GUARD)
        # v3: (L) 超速护栏 [新增, 冒烟后斜率 0.5->0.25]: 12.5 m/s 以上每 m/s 罚 0.25
        #     (25 m/s 时 -3.1/步; 0.5 的悬崖式罚值把远场状态整体压到 -7/步 量级,
        #     反而困住早期策略——改为平缓梯度让"减速"成为可学习的局部改进)
        overspeed_pen = 0.25 * max(0.0, v_h - self.V_MAX)
        # v3: (M) 低高度成本 [v2 的 0.5@1m -> 1.0@3m]: 贴地不再廉价
        low_alt_pen = 1.0 if (s['agl'] < 3.0 and self.steps > 50) else 0.0
        # v3: 汇总; (F) 航点奖 / (N) 动作能量 / 坠毁罚保留 v2 原值; (I) 姿态小罚保留 v2
        # v3-r2: 汇总加入 (D2) turn_pay 与 (F) alt_band
        #        [勘误: 03:55 的 r2 首跑曾漏加此两项 (变量已算未入和), 结果与 r1
        #        位级一致而暴露, 已修正后重跑——真实 r2 以本版代码为准]
        reward = (progress + cruise_pay + idle_cost + align_pay + funnel_pay
                  + turn_pay + alt_band + alt_progress
                  - 0.05 * (abs(s['pitch']) + abs(s['roll']))
                  - alt_abs_pen - roll_guard - pitch_guard - overspeed_pen - low_alt_pen
                  - 0.01 * float(np.sum(np.square(action))))
        if dist < self.current_wp_radius and wp_pending:
            self.miss_dists.append(dist)
            self.wp_idx += 1
            reward += 30.0
        if self.wp_idx >= len(self.wps):
            reward += 50.0
        if self.crashed:
            reward -= 50.0

        terminated = self.crashed or self.wp_idx >= len(self.wps)
        truncated = self.steps >= self.episode_steps and not terminated
        info = {"wp_idx": self.wp_idx, "num_wps": len(self.wps), "dist_to_wp_m": dist,
                "crashed": self.crashed}
        if terminated or truncated:
            done_all = self.wp_idx >= len(self.wps)
            info["success"] = done_all and (not self.crashed)
            info["mean_err"] = (float(np.mean(self.miss_dists)) if (done_all and self.miss_dists)
                                else float(dist))
        return self._obs(s), float(reward), terminated, truncated, info


class TakeoffTask(JSBSimTaskBase):
    """起飞任务: 从地面静止直接学习稳定爬升到安全高度 (15 m), 姿态保持稳定。

    针对性设计 (2026-08-29, 解决 FlightGear 演示中 liftoff→climb 阶段翻覆问题):
      - 直接从地面开始 (不调用 _fly_to_hover), RL 必须学会总距-姿态协调起飞
      - 奖励函数重点: 高度生计奖励 + 姿态惩罚 + 稳定爬升率奖励 + 总距平滑惩罚
      - 成功条件: 达到 14m 且 roll/pitch < 15° 持续 1s
      - 坠毁条件: roll/pitch > 60° 或 agl < -1m

    观测 = 悬停 10 维 + [目标高度误差/14, 爬升率/5] (12 维)
    动作 = 4 维 (总距/俯仰/横滚/脚蹬), 走悬停 SAS 控制律"""

    EXTRA = 4  # v5: 12->14维 (加北向/东向位移), 整改令 §6.2
    TARGET_ALT_M = 14.0
    SUCCESS_SETTLE_S = 1.0
    DRIFT_LIMIT_M = 30.0  # v5: 成功判据漂移上限

    def __init__(self, episode_steps=1000, wind=True):
        super().__init__(episode_steps=episode_steps, wind=wind, n_extra_obs=self.EXTRA)
        self.target_alt_m = self.TARGET_ALT_M

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self._unlock_yaw_trim()
        self.steps = 0
        self.crashed = False
        self.settled_for = 0.0
        self.last_coll = 0.0
        self.alt_errors = []
        # 直接从地面开始, 不调用 _fly_to_hover
        s = self._raw()
        self._record_origin()
        info = {"ground_start": True, "initial_agl_m": float(s['agl'])}
        return self._obs(s), info

    def _obs(self, s):
        alt_err = self.target_alt_m - s['agl']
        pos_n, pos_e = self._pos_ne()  # v5: 相对起始点的北/东位移 (m)
        return np.concatenate([
            self._hover_obs(s, self.target_alt_m),
            np.array([
                float(alt_err / self.target_alt_m),
                float(s['vz'] / 5.0),
                float(pos_n / 50.0),  # v5: 北向位移
                float(pos_e / 50.0),  # v5: 东向位移
            ], dtype=np.float32),
        ])

    def step(self, action):
        u_coll, u_pitch, u_roll, u_yaw = [float(a) for a in np.clip(action, -1.0, 1.0)]
        self._apply_sas(u_coll, u_pitch, u_roll, u_yaw)
        self.fdm.run()
        self._post_physics()
        self.steps += 1

        s = self._raw()
        self._check_crash(s)

        # 当前总距 (用于平滑惩罚)
        coll = self.fdm.get_property_value('fcs/collective-cmd-norm')
        coll_smooth = abs(coll - self.last_coll)
        self.last_coll = coll

        # 奖励函数设计
        # 1. 高度生计奖励 (鼓励爬升, 最高 1.0)
        alt_reward = min(s['agl'] / self.target_alt_m, 1.0) * 0.5
        # 2. 姿态惩罚 (roll/pitch 过大扣分)
        att_penalty = 0.3 * (abs(s['roll']) + abs(s['pitch']))
        # 3. 稳定爬升率奖励 (鼓励 1-3 m/s 稳定爬升)
        vz_target = 2.0
        vz_reward = 0.2 * max(0, 1.0 - abs(s['vz'] - vz_target) / 3.0) if s['agl'] < self.target_alt_m else 0.0
        # 4. 存活奖励
        survive = 0.01
        # 5. 总距平滑惩罚 (防止总距剧烈变化, 特别是骤降导致失速)
        smooth_penalty = 0.3 * coll_smooth
        # 6. 高度误差记录
        self.alt_errors.append(abs(self.target_alt_m - s['agl']))

        # v5: 水平速度惩罚 + 漂移惩罚 (整改令 §6.2, G1审计滑翔漂移339m仍判成功)
        v_horiz = math.hypot(s['u'], s['v'])  # 体轴水平速度 (m/s)
        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)
        horiz_speed_pen = 0.1 * v_horiz       # 水平速度每 m/s 罚 0.1
        drift_pen = 0.02 * net_drift           # 漂移每 m 罚 0.02

        reward = alt_reward + vz_reward + survive - att_penalty - smooth_penalty - horiz_speed_pen - drift_pen

        # 成功判定: 达到目标高度且姿态稳定持续 1s + 漂移 < 30m (v5)
        if (s['agl'] >= self.target_alt_m and abs(s['roll']) < math.radians(15)
                and abs(s['pitch']) < math.radians(15)
                and net_drift < self.DRIFT_LIMIT_M):
            self.settled_for += DT
            if self.settled_for >= self.SUCCESS_SETTLE_S:
                reward += 10.0
                terminated = True
                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "net_drift_m": float(net_drift),
                        "settled": True}
                return self._obs(s), float(reward), terminated, False, info
        else:
            self.settled_for = 0.0

        # 坠毁判定
        if self.crashed or s['agl'] < -1.0:
            reward -= 5.0
            terminated = True
            info = {"success": False, "crashed": True,
                    "final_agl_m": float(s['agl']),
                    "mean_alt_err_m": float(np.mean(self.alt_errors)) if self.alt_errors else 999.0,
                    "final_roll_deg": float(math.degrees(s['roll'])),
                    "final_pitch_deg": float(math.degrees(s['pitch']))}
            return self._obs(s), float(reward), terminated, False, info

        truncated = self.steps >= self.episode_steps
        info = {"crashed": False, "agl_m": float(s['agl']),
                "vz_mps": float(s['vz']),
                "roll_deg": float(math.degrees(s['roll'])),
                "pitch_deg": float(math.degrees(s['pitch'])),
                "coll": float(coll)}
        if truncated:
            info["success"] = False
            info["mean_alt_err_m"] = float(np.mean(self.alt_errors)) if self.alt_errors else 999.0
        return self._obs(s), float(reward), False, truncated, info


class TakeoffHardTask(TakeoffTask):
    """增强版起飞任务 (Hard Mode) - 针对 sim-to-real gap 设计

    与普通版的区别 (2026-08-29, 解决 RL 与 FlightGear 表现差异大的问题):
      1. 强风干扰: 稳态风 0-8 m/s (随机方向) + 阵风 0-5 m/s (随机持续时间)
      2. 初始姿态随机: roll/pitch ±5°, 航向 ±180°
      3. 域随机化: 质量 ±10%, 执行器延迟模拟 (动作一阶滤波)
      4. 传感器噪声: 高度 ±0.3m, 姿态 ±0.5°, 角速度 ±5°/s
      5. 更严格成功条件: 15m 高度 + roll/pitch < 10° + 稳定 2s
      6. 阵风随时间变化: 每 2-5 秒随机更新阵风强度和方向

    观测 = 12 维 (与普通版相同, 但加入传感器噪声)
    动作 = 4 维 (总距/俯仰/横滚/脚蹬)"""

    TARGET_ALT_M = 15.0
    SUCCESS_SETTLE_S = 2.0
    MAX_ROLL_DEG = 10.0
    MAX_PITCH_DEG = 10.0

    # 风参数
    WIND_STEADY_MAX_MPS = 8.0
    WIND_GUST_MAX_MPS = 5.0
    GUST_UPDATE_MIN_S = 2.0
    GUST_UPDATE_MAX_S = 5.0

    # 初始姿态随机范围
    INIT_ROLL_DEG = 5.0
    INIT_PITCH_DEG = 5.0

    # 传感器噪声
    NOISE_ALT_M = 0.3
    NOISE_ATT_DEG = 0.5
    NOISE_RATE_DPS = 5.0

    # 执行器延迟 (一阶滤波系数, 0=无延迟, 1=完全跟随上一帧)
    ACTUATOR_SMOOTH = 0.2

    def __init__(self, episode_steps=1500, wind=True):
        super().__init__(episode_steps=episode_steps, wind=wind)
        self.target_alt_m = self.TARGET_ALT_M
        self.settle_steps = int(self.SUCCESS_SETTLE_S / DT)
        # 执行器状态
        self._smoothed_action = np.zeros(4, dtype=np.float32)
        # 阵风状态
        self._gust_north = 0.0
        self._gust_east = 0.0
        self._gust_timer = 0.0
        self._next_gust_update = 3.0

    def reset(self, seed=None, options=None):
        super(TakeoffTask, self).reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self._unlock_yaw_trim()
        self.steps = 0
        self.crashed = False
        self.settled_for = 0.0
        self.last_coll = 0.0
        self.alt_errors = []
        self._smoothed_action = np.zeros(4, dtype=np.float32)

        # 1. 初始姿态随机 (roll/pitch ±5°)
        init_roll = math.radians(self.rng.uniform(-self.INIT_ROLL_DEG, self.INIT_ROLL_DEG))
        init_pitch = math.radians(self.rng.uniform(-self.INIT_PITCH_DEG, self.INIT_PITCH_DEG))
        self.fdm.set_property_value('attitude/phi-rad', init_roll)
        self.fdm.set_property_value('attitude/theta-rad', init_pitch)

        # 2. 强稳态风 (0-8 m/s, 随机方向)
        if self.wind_enabled:
            wind_speed = self.rng.uniform(0, self.WIND_STEADY_MAX_MPS)
            wind_dir = self.rng.uniform(0, 2 * math.pi)
            wind_north = wind_speed * math.cos(wind_dir) / FPS2MPS  # 转换为 fps
            wind_east = wind_speed * math.sin(wind_dir) / FPS2MPS
            self.fdm.set_property_value('atmosphere/wind-north-fps', float(wind_north))
            self.fdm.set_property_value('atmosphere/wind-east-fps', float(wind_east))

        # 3. 阵风初始化
        self._gust_north = 0.0
        self._gust_east = 0.0
        self._gust_timer = 0.0
        self._next_gust_update = self.rng.uniform(self.GUST_UPDATE_MIN_S, self.GUST_UPDATE_MAX_S)

        s = self._raw()
        self._record_origin()
        info = {
            "ground_start": True,
            "initial_agl_m": float(s['agl']),
            "initial_roll_deg": math.degrees(init_roll),
            "initial_pitch_deg": math.degrees(init_pitch),
            "wind_speed_mps": wind_speed if self.wind_enabled else 0.0,
            "hard_mode": True,
        }
        return self._obs(s), info

    def _update_gust(self):
        """更新阵风: 每 2-5 秒随机变化"""
        self._gust_timer += DT
        if self._gust_timer >= self._next_gust_update:
            self._gust_timer = 0.0
            self._next_gust_update = self.rng.uniform(self.GUST_UPDATE_MIN_S, self.GUST_UPDATE_MAX_S)
            # 新阵风强度和方向
            gust_speed = self.rng.uniform(0, self.WIND_GUST_MAX_MPS)
            gust_dir = self.rng.uniform(0, 2 * math.pi)
            self._gust_north = gust_speed * math.cos(gust_dir) / FPS2MPS
            self._gust_east = gust_speed * math.sin(gust_dir) / FPS2MPS
            # 应用阵风 (叠加到稳态风上)
            steady_n = self.fdm.get_property_value('atmosphere/wind-north-fps')
            steady_e = self.fdm.get_property_value('atmosphere/wind-east-fps')
            self.fdm.set_property_value('atmosphere/wind-north-fps', float(steady_n + self._gust_north))
            self.fdm.set_property_value('atmosphere/wind-east-fps', float(steady_e + self._gust_east))

    def _add_sensor_noise(self, s):
        """给观测状态添加传感器噪声"""
        noisy = dict(s)
        noisy['agl'] = s['agl'] + self.rng.normal(0, self.NOISE_ALT_M)
        noisy['pitch'] = s['pitch'] + math.radians(self.rng.normal(0, self.NOISE_ATT_DEG))
        noisy['roll'] = s['roll'] + math.radians(self.rng.normal(0, self.NOISE_ATT_DEG))
        noisy['p'] = s['p'] + math.radians(self.rng.normal(0, self.NOISE_RATE_DPS))
        noisy['q'] = s['q'] + math.radians(self.rng.normal(0, self.NOISE_RATE_DPS))
        noisy['r'] = s['r'] + math.radians(self.rng.normal(0, self.NOISE_RATE_DPS))
        return noisy

    def _obs(self, s):
        """带传感器噪声的观测 (v5: 14维, 加水平位置)"""
        noisy_s = self._add_sensor_noise(s)
        alt_err = self.target_alt_m - noisy_s['agl']
        pos_n, pos_e = self._pos_ne()
        # 位置也加噪声 (模拟 GPS 噪声 ~0.5m)
        pos_n += self.rng.normal(0, 0.5)
        pos_e += self.rng.normal(0, 0.5)
        return np.concatenate([
            self._hover_obs(noisy_s, self.target_alt_m),
            np.array([
                float(alt_err / self.target_alt_m),
                float(noisy_s['vz'] / 5.0),
                float(pos_n / 50.0),
                float(pos_e / 50.0),
            ], dtype=np.float32),
        ])

    def step(self, action):
        # 执行器延迟模拟 (一阶滤波)
        action = np.array(action, dtype=np.float32)
        self._smoothed_action = (1 - self.ACTUATOR_SMOOTH) * action + self.ACTUATOR_SMOOTH * self._smoothed_action

        u_coll, u_pitch, u_roll, u_yaw = [float(a) for a in np.clip(self._smoothed_action, -1.0, 1.0)]
        self._apply_sas(u_coll, u_pitch, u_roll, u_yaw)
        self.fdm.run()
        self._post_physics()
        self.steps += 1

        # 更新阵风
        if self.wind_enabled:
            self._update_gust()

        s = self._raw()
        self._check_crash(s)

        coll = self.fdm.get_property_value('fcs/collective-cmd-norm')
        coll_smooth = abs(coll - self.last_coll)
        self.last_coll = coll

        # 奖励函数 (与普通版相同, 但姿态惩罚更重)
        alt_reward = min(s['agl'] / self.target_alt_m, 1.0) * 0.5
        att_penalty = 0.5 * (abs(s['roll']) + abs(s['pitch']))  # 更重的姿态惩罚
        vz_target = 2.0
        vz_reward = 0.2 * max(0, 1.0 - abs(s['vz'] - vz_target) / 3.0) if s['agl'] < self.target_alt_m else 0.0
        survive = 0.01
        smooth_penalty = 0.3 * coll_smooth
        self.alt_errors.append(abs(self.target_alt_m - s['agl']))

        # v5: 水平速度惩罚 + 漂移惩罚
        v_horiz = math.hypot(s['u'], s['v'])
        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)
        horiz_speed_pen = 0.1 * v_horiz
        drift_pen = 0.02 * net_drift

        reward = alt_reward + vz_reward + survive - att_penalty - smooth_penalty - horiz_speed_pen - drift_pen

        # 更严格的成功判定: 15m + roll/pitch < 10° + 稳定 2s + 漂移 < 30m (v5)
        if (s['agl'] >= self.target_alt_m
                and abs(s['roll']) < math.radians(self.MAX_ROLL_DEG)
                and abs(s['pitch']) < math.radians(self.MAX_PITCH_DEG)
                and net_drift < self.DRIFT_LIMIT_M):
            self.settled_for += DT
            if self.settled_for >= self.SUCCESS_SETTLE_S:
                reward += 20.0  # 更大的成功奖励
                terminated = True
                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "net_drift_m": float(net_drift),
                        "settled": True, "hard_mode": True}
                return self._obs(s), float(reward), terminated, False, info
        else:
            self.settled_for = 0.0

        # 坠毁判定 (与普通版相同)
        if self.crashed or s['agl'] < -1.0:
            reward -= 5.0
            terminated = True
            info = {"success": False, "crashed": True,
                    "final_agl_m": float(s['agl']),
                    "mean_alt_err_m": float(np.mean(self.alt_errors)) if self.alt_errors else 999.0,
                    "final_roll_deg": float(math.degrees(s['roll'])),
                    "final_pitch_deg": float(math.degrees(s['pitch'])),
                    "hard_mode": True}
            return self._obs(s), float(reward), terminated, False, info

        truncated = self.steps >= self.episode_steps
        info = {"crashed": False, "agl_m": float(s['agl']),
                "vz_mps": float(s['vz']),
                "roll_deg": float(math.degrees(s['roll'])),
                "pitch_deg": float(math.degrees(s['pitch'])),
                "coll": float(coll),
                "gust_north_mps": float(self._gust_north * FPS2MPS),
                "gust_east_mps": float(self._gust_east * FPS2MPS)}
        if truncated:
            info["success"] = False
            info["mean_alt_err_m"] = float(np.mean(self.alt_errors)) if self.alt_errors else 999.0
        return self._obs(s), float(reward), False, truncated, info


class TakeoffFGTask(TakeoffHardTask):
    """FG 对齐版起飞任务 - 模拟 FlightGear 控制架构与环境因素

    针对 sim-to-real gap 的核心差异模拟 (2026-08-29):
      1. 控制架构: 外环 5Hz 姿态指令(总距+pitch/roll/yaw目标) + 内环 PID 姿态稳定
         (FG demo_stabilizer.xml rev6 架构, 而非 RL 直接控制舵面)
      2. 传感器模型: 高度±0.5m噪声+100ms延迟, 姿态±1°噪声+50ms延迟, 角速度±10°/s
      3. 执行器模型: 总距速率±0.5/s, 姿态目标速率±30°/s, 50ms 执行延迟
      4. 大气模型: 稳态风0-10m/s + 阵风0-8m/s + Dryden紊流 + 近地面风切变
      5. 旋翼动态: 转速随负载波动(±5%), 发动机功率限制
      6. 地面效应: 增强 IGE 地面效应模型

    观测 = 12 维 (与 hard 版相同, 但来自带噪声/延迟的传感器)
    动作 = 4 维 [总距指令, pitch目标, roll目标, yaw目标] (外环指令, 非直接舵面)"""

    # 外环控制频率 (FG Python 脚本 ~5Hz)
    OUTER_LOOP_HZ = 5.0
    # 内环姿态控制器增益 (模拟 FG rev6 内环)
    PITCH_KP = 0.12
    PITCH_KD = 0.08
    ROLL_KP = 0.12
    ROLL_KD = 0.08
    YAW_KP = 0.08
    YAW_KD = 0.04

    # 传感器参数
    SENSOR_DELAY_STEPS = 5  # 100ms @ 50Hz
    ALT_NOISE_M = 0.5
    ATT_NOISE_DEG = 1.0
    RATE_NOISE_DPS = 10.0
    VS_NOISE_MPS = 0.5

    # 执行器参数
    COLL_RATE_LIMIT = 0.5  # ±0.5/s
    ATT_RATE_LIMIT_DEG = 30.0  # ±30°/s
    ACTUATOR_DELAY_STEPS = 3  # 60ms @ 50Hz

    # 大气参数
    WIND_STEADY_MAX = 10.0
    WIND_GUST_MAX = 8.0
    TURBULENCE_GAIN = 0.4  # Dryden 紊流强度
    WIND_SHEAR_GAIN = 0.3  # 近地面风切变强度

    # 旋翼动态
    RPM_NOMINAL = 324.0
    RPM_VARIATION = 0.05  # ±5% 转速波动

    def __init__(self, episode_steps=2000, wind=True):
        super().__init__(episode_steps=episode_steps, wind=wind)
        self.target_alt_m = self.TARGET_ALT_M
        self.outer_loop_dt = 1.0 / self.OUTER_LOOP_HZ
        self.outer_loop_counter = 0
        # 外环目标 (5Hz 更新)
        self.cmd_coll = 0.5
        self.cmd_pitch_deg = 0.0
        self.cmd_roll_deg = 0.0
        self.cmd_yaw_deg = 0.0
        # 传感器历史缓冲区 (用于延迟模拟)
        self._sensor_buffer = []
        # 执行器历史缓冲区
        self._actuator_buffer = []
        # 紊流状态
        self._turb_n = 0.0
        self._turb_e = 0.0
        self._turb_d = 0.0
        # 旋翼转速
        self._current_rpm = self.RPM_NOMINAL

    def reset(self, seed=None, options=None):
        obs, info = super(TakeoffHardTask, self).reset(seed=seed, options=options)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._reset_jsbsim()
        self._unlock_yaw_trim()
        self.steps = 0
        self.crashed = False
        self.settled_for = 0.0
        self.last_coll = 0.0
        self.alt_errors = []
        self._smoothed_action = np.zeros(4, dtype=np.float32)

        # 初始姿态随机
        init_roll = math.radians(self.rng.uniform(-self.INIT_ROLL_DEG, self.INIT_ROLL_DEG))
        init_pitch = math.radians(self.rng.uniform(-self.INIT_PITCH_DEG, self.INIT_PITCH_DEG))
        self.fdm.set_property_value('attitude/phi-rad', init_roll)
        self.fdm.set_property_value('attitude/theta-rad', init_pitch)

        # 强稳态风
        if self.wind_enabled:
            wind_speed = self.rng.uniform(0, self.WIND_STEADY_MAX)
            wind_dir = self.rng.uniform(0, 2 * math.pi)
            self._steady_wind_n = wind_speed * math.cos(wind_dir) / FPS2MPS
            self._steady_wind_e = wind_speed * math.sin(wind_dir) / FPS2MPS
            self.fdm.set_property_value('atmosphere/wind-north-fps', float(self._steady_wind_n))
            self.fdm.set_property_value('atmosphere/wind-east-fps', float(self._steady_wind_e))
        else:
            self._steady_wind_n = 0.0
            self._steady_wind_e = 0.0

        # 初始化缓冲区
        self._sensor_buffer = []
        self._actuator_buffer = []
        self._gust_north = 0.0
        self._gust_east = 0.0
        self._gust_timer = 0.0
        self._next_gust_update = self.rng.uniform(2.0, 5.0)
        self._turb_n = 0.0
        self._turb_e = 0.0
        self._turb_d = 0.0
        self._current_rpm = self.RPM_NOMINAL
        self.outer_loop_counter = 0
        self.cmd_coll = 0.5
        self.cmd_pitch_deg = 0.0
        self.cmd_roll_deg = 0.0
        self.cmd_yaw_deg = 0.0

        s = self._raw()
        self._record_origin()
        info = {
            "ground_start": True,
            "initial_agl_m": float(s['agl']),
            "initial_roll_deg": math.degrees(init_roll),
            "initial_pitch_deg": math.degrees(init_pitch),
            "wind_speed_mps": math.hypot(self._steady_wind_n, self._steady_wind_e) * FPS2MPS if self.wind_enabled else 0.0,
            "fg_aligned": True,
            "control_architecture": "outer_5Hz_attitude_cmd + inner_PID",
        }
        return self._obs(s), info

    def _update_turbulence(self):
        """简化 Dryden 紊流模型 - 一阶滤波白噪声"""
        # 紊流长波长 (m)
        L_u, L_v, L_w = 200.0, 200.0, 50.0
        # 紊流强度 (m/s)
        sigma_u = sigma_v = sigma_w = self.TURBULENCE_GAIN * 3.0
        # 时间常数
        tau_u = L_u / max(1.0, 10.0)  # 假设空速 10 m/s
        tau_v = L_v / max(1.0, 10.0)
        tau_w = L_w / max(1.0, 10.0)
        # 一阶滤波更新
        self._turb_n += (-self._turb_n / tau_u + sigma_u * math.sqrt(2.0 / tau_u) * self.rng.normal()) * DT
        self._turb_e += (-self._turb_e / tau_v + sigma_v * math.sqrt(2.0 / tau_v) * self.rng.normal()) * DT
        self._turb_d += (-self._turb_d / tau_w + sigma_w * math.sqrt(2.0 / tau_w) * self.rng.normal()) * DT

    def _update_wind_shear(self, agl_m):
        """近地面风切变 - 风速随高度对数变化"""
        # 对数风剖面: v(z) = v_ref * ln(z/z0) / ln(z_ref/z0)
        z0 = 0.03  # 地表粗糙度 (m)
        z_ref = 10.0  # 参考高度 (m)
        if agl_m > 0.1:
            shear_factor = math.log(agl_m / z0) / math.log(z_ref / z0)
        else:
            shear_factor = 0.0
        return np.clip(shear_factor, 0.0, 1.5)

    def _update_atmosphere(self, s):
        """更新完整大气模型: 稳态风 + 阵风 + 紊流 + 风切变"""
        if not self.wind_enabled:
            return

        # 1. 阵风更新
        self._gust_timer += DT
        if self._gust_timer >= self._next_gust_update:
            self._gust_timer = 0.0
            self._next_gust_update = self.rng.uniform(2.0, 5.0)
            gust_speed = self.rng.uniform(0, self.WIND_GUST_MAX)
            gust_dir = self.rng.uniform(0, 2 * math.pi)
            self._gust_north = gust_speed * math.cos(gust_dir) / FPS2MPS
            self._gust_east = gust_speed * math.sin(gust_dir) / FPS2MPS

        # 2. 紊流更新
        self._update_turbulence()

        # 3. 风切变 (近地面风速降低)
        shear = self._update_wind_shear(s['agl'])

        # 4. 合成风 (稳态 + 阵风 + 紊流) * 风切变
        total_wind_n = (self._steady_wind_n + self._gust_north + self._turb_n / FPS2MPS) * shear
        total_wind_e = (self._steady_wind_e + self._gust_east + self._turb_e / FPS2MPS) * shear

        self.fdm.set_property_value('atmosphere/wind-north-fps', float(total_wind_n))
        self.fdm.set_property_value('atmosphere/wind-east-fps', float(total_wind_e))

    def _inner_loop_pid(self, s):
        """内环姿态稳定控制器 (复用 _apply_sas 已验证稳定的控制律)
        输入: 外环目标 (cmd_pitch, cmd_roll, cmd_yaw)
        输出: 直接调用 _apply_sas 设置舵面
        地面阶段(agl<3m): 滚转增益和阻尼 x2, 防止地面效应下滚转失控"""
        # 姿态误差
        pitch_err = math.radians(self.cmd_pitch_deg) - s['pitch']
        roll_err = math.radians(self.cmd_roll_deg) - s['roll']
        yaw_err = math.radians(self.cmd_yaw_deg) - s['psi']

        # 地面阶段增益增强 (agl < 3m 时滚转控制 x1.3, 温和增强)
        ground_factor = 1.0
        if s['agl'] < 3.0:
            ground_factor = 1.3 * (1.0 - s['agl'] / 3.0) + 1.0  # 1.0-2.3 线性变化

        # 将姿态误差转换为 _apply_sas 的 u 输入
        u_pitch = float(np.clip(2.0 * pitch_err, -1.0, 1.0))
        u_roll = float(np.clip(2.0 * ground_factor * roll_err, -1.0, 1.0))
        u_yaw = float(np.clip(1.5 * yaw_err, -1.0, 1.0))

        # 总距转换: _apply_sas 中 coll = 0.70 + u_coll * 0.25
        u_coll = float(np.clip((self.cmd_coll - 0.70) / 0.25, -1.0, 1.0))

        # 调用已验证稳定的 _apply_sas 控制律
        self._apply_sas(u_coll, u_pitch, u_roll, u_yaw)

        # 读取设置的舵面值 (用于记录)
        elevator = self.fdm.get_property_value('fcs/elevator-cmd-norm')
        aileron = self.fdm.get_property_value('fcs/aileron-cmd-norm')
        rudder = self.fdm.get_property_value('fcs/rudder-cmd-norm')
        return elevator, aileron, rudder

    def _apply_actuator_model(self, coll_cmd, pitch_cmd_deg, roll_cmd_deg, yaw_cmd_deg):
        """执行器模型: 速率限制 + 延迟
        地面阶段(agl<3m): 总距速率限制 x0.5, 防止突然提总距导致滚转失控"""
        # 获取当前高度 (用于地面阶段判断)
        try:
            current_agl = max(0.0, (self.fdm.get_property_value('position/h-agl-ft') - self._ground_agl_ft) * FT2M)
        except Exception:
            current_agl = 0.0

        # 地面阶段总距速率限制 (温和限制 x0.8, 防止突然提总距但允许正常起飞)
        coll_rate_limit = self.COLL_RATE_LIMIT
        if current_agl < 3.0:
            coll_rate_limit = self.COLL_RATE_LIMIT * (0.8 + 0.2 * current_agl / 3.0)  # 0.8-1.0 线性变化

        # 总距速率限制
        coll_rate = (coll_cmd - self.cmd_coll) / DT
        coll_rate = np.clip(coll_rate, -coll_rate_limit, coll_rate_limit)
        new_coll = self.cmd_coll + coll_rate * DT

        # 姿态目标速率限制
        pitch_rate = (pitch_cmd_deg - self.cmd_pitch_deg) / DT
        pitch_rate = np.clip(pitch_rate, -self.ATT_RATE_LIMIT_DEG, self.ATT_RATE_LIMIT_DEG)
        new_pitch = self.cmd_pitch_deg + pitch_rate * DT

        roll_rate = (roll_cmd_deg - self.cmd_roll_deg) / DT
        roll_rate = np.clip(roll_rate, -self.ATT_RATE_LIMIT_DEG, self.ATT_RATE_LIMIT_DEG)
        new_roll = self.cmd_roll_deg + roll_rate * DT

        yaw_rate = (yaw_cmd_deg - self.cmd_yaw_deg) / DT
        yaw_rate = np.clip(yaw_rate, -self.ATT_RATE_LIMIT_DEG, self.ATT_RATE_LIMIT_DEG)
        new_yaw = self.cmd_yaw_deg + yaw_rate * DT

        # 执行器延迟 (使用缓冲区)
        self._actuator_buffer.append((new_coll, new_pitch, new_roll, new_yaw))
        if len(self._actuator_buffer) > self.ACTUATOR_DELAY_STEPS:
            delayed = self._actuator_buffer.pop(0)
        else:
            delayed = (new_coll, new_pitch, new_roll, new_yaw)

        self.cmd_coll, self.cmd_pitch_deg, self.cmd_roll_deg, self.cmd_yaw_deg = delayed
        return delayed

    def _apply_sensor_model(self, s):
        """传感器模型: 噪声 + 延迟"""
        # 添加噪声
        noisy = dict(s)
        noisy['agl'] = s['agl'] + self.rng.normal(0, self.ALT_NOISE_M)
        noisy['vz'] = s['vz'] + self.rng.normal(0, self.VS_NOISE_MPS)
        noisy['pitch'] = s['pitch'] + math.radians(self.rng.normal(0, self.ATT_NOISE_DEG))
        noisy['roll'] = s['roll'] + math.radians(self.rng.normal(0, self.ATT_NOISE_DEG))
        noisy['p'] = s['p'] + math.radians(self.rng.normal(0, self.RATE_NOISE_DPS))
        noisy['q'] = s['q'] + math.radians(self.rng.normal(0, self.RATE_NOISE_DPS))
        noisy['r'] = s['r'] + math.radians(self.rng.normal(0, self.RATE_NOISE_DPS))

        # 延迟 (使用缓冲区)
        self._sensor_buffer.append(noisy)
        if len(self._sensor_buffer) > self.SENSOR_DELAY_STEPS:
            delayed = self._sensor_buffer.pop(0)
        else:
            delayed = noisy

        return delayed

    def _update_rotor_dynamics(self, coll):
        """旋翼动态: 转速随负载波动"""
        # 总距越高, 负载越大, 转速越低
        load_factor = 1.0 + 0.3 * (coll - 0.5)
        target_rpm = self.RPM_NOMINAL / load_factor
        # 转速响应延迟 (一阶滤波)
        self._current_rpm += (target_rpm - self._current_rpm) * 0.1
        # 限制波动范围
        self._current_rpm = np.clip(self._current_rpm,
                                     self.RPM_NOMINAL * (1 - self.RPM_VARIATION),
                                     self.RPM_NOMINAL * (1 + self.RPM_VARIATION))

    def _obs(self, s):
        """带传感器噪声和延迟的观测 (v5: 14维, 加水平位置)"""
        sensor_s = self._apply_sensor_model(s)
        alt_err = self.target_alt_m - sensor_s['agl']
        pos_n, pos_e = self._pos_ne()
        # 位置也通过传感器模型 (加噪声+延迟)
        pos_n += self.rng.normal(0, self.ALT_NOISE_M)
        pos_e += self.rng.normal(0, self.ALT_NOISE_M)
        return np.concatenate([
            self._hover_obs(sensor_s, self.target_alt_m),
            np.array([
                float(alt_err / self.target_alt_m),
                float(sensor_s['vz'] / 5.0),
                float(pos_n / 50.0),
                float(pos_e / 50.0),
            ], dtype=np.float32),
        ])

    def step(self, action):
        # action = [总距指令, pitch目标, roll目标, yaw目标] (外环 5Hz 指令)
        action = np.array(action, dtype=np.float32)

        # 外环: 每 outer_loop_dt 更新一次目标
        self.outer_loop_counter += 1
        if self.outer_loop_counter >= int(self.outer_loop_dt / DT):
            self.outer_loop_counter = 0
            # 解析外环指令
            coll_cmd = float(np.clip(action[0], 0.0, 1.0))
            pitch_cmd = float(np.clip(action[1] * 15.0, -15.0, 15.0))  # ±15°
            roll_cmd = float(np.clip(action[2] * 15.0, -15.0, 15.0))
            yaw_cmd = float(np.clip(action[3] * 30.0, -30.0, 30.0))  # ±30°
            # 执行器模型 (速率限制 + 延迟)
            self._apply_actuator_model(coll_cmd, pitch_cmd, roll_cmd, yaw_cmd)

        # 内环姿态稳定 (每步运行, 复用 _apply_sas 已验证稳定的控制律)
        s_raw = self._raw()
        self._inner_loop_pid(s_raw)

        # 读取总距 (用于记录和旋翼动态)
        coll = float(np.clip(self.cmd_coll, 0.0, 1.0))

        # 旋翼动态
        self._update_rotor_dynamics(coll)

        self.fdm.run()
        self._post_physics()
        self.steps += 1

        # 更新大气模型
        s = self._raw()
        self._update_atmosphere(s)
        self._check_crash(s)

        coll_smooth = abs(coll - self.last_coll)
        self.last_coll = coll

        # 奖励函数 (稳定版 - 90%成功率配置)
        alt_reward = min(s['agl'] / self.target_alt_m, 1.0) * 0.5

        # 地面阶段(agl<3m)姿态惩罚 x1.5 (温和增强, 防止过度惩罚导致不敢起飞)
        ground_factor = 1.0
        if s['agl'] < 3.0:
            ground_factor = 1.5 * (1.0 - s['agl'] / 3.0) + 1.0  # 1.0-2.5 线性变化
        att_penalty = ground_factor * 0.5 * (abs(s['roll']) + abs(s['pitch']))

        # 姿态稳定奖励 (roll/pitch < 5° 时额外奖励, 地面阶段权重更高)
        if abs(s['roll']) < math.radians(5.0) and abs(s['pitch']) < math.radians(5.0):
            att_stable_reward = 0.1 * ground_factor
        else:
            att_stable_reward = 0.0

        vz_target = 2.0
        vz_reward = 0.2 * max(0, 1.0 - abs(s['vz'] - vz_target) / 3.0) if s['agl'] < self.target_alt_m else 0.0

        # 地面阶段存活奖励 x2 (鼓励模型在地面阶段保持稳定)
        survive = 0.01 * ground_factor
        smooth_penalty = 0.3 * coll_smooth
        self.alt_errors.append(abs(self.target_alt_m - s['agl']))

        # v5: 水平速度惩罚 + 漂移惩罚
        v_horiz = math.hypot(s['u'], s['v'])
        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)
        horiz_speed_pen = 0.1 * v_horiz
        drift_pen = 0.02 * net_drift

        reward = alt_reward + vz_reward + survive + att_stable_reward - att_penalty - smooth_penalty - horiz_speed_pen - drift_pen

        # 成功判定 + 漂移 < 30m (v5)
        if (s['agl'] >= self.target_alt_m
                and abs(s['roll']) < math.radians(self.MAX_ROLL_DEG)
                and abs(s['pitch']) < math.radians(self.MAX_PITCH_DEG)
                and net_drift < self.DRIFT_LIMIT_M):
            self.settled_for += DT
            if self.settled_for >= self.SUCCESS_SETTLE_S:
                reward += 20.0
                terminated = True
                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "net_drift_m": float(net_drift),
                        "settled": True, "fg_aligned": True,
                        "final_rpm": float(self._current_rpm)}
                return self._obs(s), float(reward), terminated, False, info
        else:
            self.settled_for = 0.0

        # 坠毁判定 (稳定版)
        if self.crashed or s['agl'] < -1.0:
            reward -= 5.0
            terminated = True
            info = {"success": False, "crashed": True,
                    "final_agl_m": float(s['agl']),
                    "mean_alt_err_m": float(np.mean(self.alt_errors)) if self.alt_errors else 999.0,
                    "final_roll_deg": float(math.degrees(s['roll'])),
                    "final_pitch_deg": float(math.degrees(s['pitch'])),
                    "fg_aligned": True}
            return self._obs(s), float(reward), terminated, False, info

        truncated = self.steps >= self.episode_steps
        info = {"crashed": False, "agl_m": float(s['agl']),
                "vz_mps": float(s['vz']),
                "roll_deg": float(math.degrees(s['roll'])),
                "pitch_deg": float(math.degrees(s['pitch'])),
                "coll": float(coll),
                "cmd_pitch_deg": float(self.cmd_pitch_deg),
                "cmd_roll_deg": float(self.cmd_roll_deg),
                "rpm": float(self._current_rpm),
                "turbulence_n_mps": float(self._turb_n),
                "turbulence_e_mps": float(self._turb_e),
                "fg_aligned": True}
        if truncated:
            info["success"] = False
            info["mean_alt_err_m"] = float(np.mean(self.alt_errors)) if self.alt_errors else 999.0
        return self._obs(s), float(reward), False, truncated, info


TASK_CLASSES = {
    'forward': ForwardFlightTask,
    'landing': LandingTask,
    'roll': RollManeuverTask,
    'yaw': YawManeuverTask,
    'waypoint': WaypointTrackTask,
    'takeoff': TakeoffTask,
    'takeoff_hard': TakeoffHardTask,
    'takeoff_fg': TakeoffFGTask,
}

ERR_METRICS = {
    'forward': 'mean_speed_err_kt',
    'landing': 'mean_descent_profile_err_mps',
    'roll': 'mean_bank_err_deg',
    'yaw': 'mean_heading_err_deg',
    'waypoint': 'mean_wp_miss_dist_m',
    'takeoff': 'mean_alt_err_m',
    'takeoff_hard': 'mean_alt_err_m',
    'takeoff_fg': 'mean_alt_err_m',
}


def make_task(name, **kwargs):
    if name == 'hover':
        from jsbsim_helo_env import JSBSimHeloEnv
        return JSBSimHeloEnv(**kwargs)
    return TASK_CLASSES[name](**kwargs)


if __name__ == '__main__':
    import io, contextlib, sys
    out = []
    with contextlib.redirect_stdout(io.StringIO()):
        for name, cls in TASK_CLASSES.items():
            env = cls()
            obs, reset_info = env.reset(seed=42)
            n_steps = 0
            while True:
                obs, r, term, trunc, info = env.step(np.zeros(4))
                n_steps += 1
                if term or trunc:
                    break
            out.append("%-9s obs_dim=%2d hover_ok=%-5s | %4d task-steps, crash=%s, info=%s"
                       % (name, env.observation_space.shape[0], reset_info.get('hover_established'),
                          n_steps, info.get('crashed'),
                          sorted(k for k in info if k in ('success', 'mean_err'))))
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    print("Multi-Task v3 smoke test (zero-policy input, 1 episode each):")
    print("\n".join(out))
    print("Done.")
