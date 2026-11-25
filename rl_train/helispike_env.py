# -*- coding: utf-8 -*-
"""
helispike_env.py — 二代机型 (helispike) 环境适配器 [契约 v1.1 完整任务套件]

包含任务:
  - HelispikeHoverEnv (heli_hover)
  - HelispikeTakeoffEnv (heli_takeoff)
  - HelispikeLandingEnv (heli_landing)
  - HelispikeForwardEnv (heli_forward)
  - HelispikeRollEnv (heli_roll)
  - HelispikeYawEnv (heli_yaw)
  - HelispikeWaypointEnv (heli_waypoint)

关键参数与契约:
  - dt=0.005 (200Hz 物理, 4步控制 = 50Hz)
  - HOVER_COLLECTIVE = 0.70 (E-fix 校准稳态悬停点)
  - COLLECTIVE_RANGE = 0.20 (u_coll [-1,1] -> 总距 0.50~0.90)
  - ROLL_SIGN = +1.0, PITCH_SIGN = +1.0, HDG_SIGN = +1.0
  - 角速度 p/q/r 经姿态数值差分提取 (克服 r-radps 死属性)
  - 严格出口消毒: obs nan_to_num + clip±10, reward nan_to_num + clip±100
"""
import math
import numpy as np
import gymnasium as gym
from gymnasium import spaces

import jsbsim

DT_PHYSICS = 0.005          # 契约 §1: 200Hz
ACTION_REPEAT = 4           # 控制周期 50Hz (0.02s)
DT_CONTROL = DT_PHYSICS * ACTION_REPEAT
EP_SECONDS = 20.0
EP_STEPS = int(EP_SECONDS / DT_CONTROL)   # 1000 控制步

FT2M = 0.3048
FPS2MPS = 0.3048
KTS2MPS = 0.514444
MAX_ANG = math.radians(45.0)
CRASH_ANG = math.radians(60.0)

RPM_BAND = (495.0, 565.0)   # 额定转速带 (中心 530 RPM)

# 控制通道限幅 (契约 §2)
ELE_LIMIT = 0.42
AIL_LIMIT = 0.30
RUD_LIMIT = 0.60
HOVER_COLLECTIVE = 0.70     # E-fix 2026-09-05: true hover point ~0.706 (probe02b
                            # bisection calibration + E 2026-09-05 probes; 0.55
                            # descends, 0.76 climbs). Old 0.50 was a descent value
                            # and the policy band [0.50,0.70] could never hover.
SPOOL_RPM = 495.0           # E-fix 2026-09-05: spool exits when rotor enters the
SPOOL_MAX_STEPS = 4000      # contract band [495,565] (governor needs 12-20 s)

# --- SAS inner loop (D3 contract) -------------------------------------------
# Helispike aerodynamics has ZERO damping moments (only flat-plate drag), and
# velocities/{p,q,r}-radps are DEAD (const 0) in this JSBSim build -- so the
# -2.5p/-2.5q/-2.87r damper channels inside hs_afcs*.xml are inert. The inner
# loop therefore runs here, at physics rate, on attitude + reconstructed rates.
# Sign physics (E static probes): +aileron raises phi, +elevator lowers theta,
# hence roll law is NEGATIVE on phi and pitch law is POSITIVE on theta.
# Gain sweep (rl_train/_e_sas_refine.py): stable for K_ATT >= 10; best 40-60.
SAS_K_ATT = 40.0            # attitude gain (per radian of error)
SAS_K_RATE = 0.05           # rate gain on reconstructed p/q (small on purpose:
                            # larger values add phase lag and tip the vehicle)
SAS_K_ALT = 0.25            # altitude-error -> climb-rate command (m/s per m)
SAS_K_VS = 0.010            # vertical-speed integrator gain. VERY small on purpose:
                            # measured d(vz)/d(coll) ~ 52 m/s per unit collective in
                            # the air (_e_coll_vz.py), so a large gain oscillates the
                            # collective between the stops within a second.
CMD_ATT_MAX_DEG = 12.0      # outer loop authority: policy asks for up to +/-12 deg
CMD_YAW_RATE_MAX = 0.25     # outer loop yaw-rate authority (normalized pedal units)
COLLECTIVE_RANGE = 0.20     # u_coll [-1, 1] -> [0.30, 0.70]

ROLL_SIGN = +1.0
PITCH_SIGN = +1.0
HDG_SIGN = +1.0


import os

class SuppressJSBSimOutput:
    """C-level stdout/stderr suppression to eliminate JSBSim terminal noise and I/O lag."""
    def __enter__(self):
        try:
            self.null_fd = os.open(os.devnull, os.O_RDWR)
            self.save_stdout = os.dup(1)
            self.save_stderr = os.dup(2)
            os.dup2(self.null_fd, 1)
            os.dup2(self.null_fd, 2)
            self._active = True
        except Exception:
            self._active = False
        return self

    def __exit__(self, *_):
        if getattr(self, '_active', False):
            try:
                os.dup2(self.save_stdout, 1)
                os.dup2(self.save_stderr, 2)
                os.close(self.null_fd)
                os.close(self.save_stdout)
                os.close(self.save_stderr)
            except Exception:
                pass

def _find_jsbsim_root():
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    spike_root = os.path.abspath(os.path.join(cur_dir, "..", "platform", "fdm_spike", "jsbsim_root"))
    if os.path.exists(os.path.join(spike_root, "aircraft", "helispike", "helispike.xml")):
        return spike_root
    return jsbsim.get_default_root_dir()

def wrap_pi(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class HelispikeHoverEnv(gym.Env):
    """二代机型悬停环境 (heli_hover)。动作 4 维 [-1,1]; 观测 12 维。"""

    metadata = {"render_modes": []}

    def __init__(self, action_mode="direct", wind=True, target_range=(10.0, 30.0),
                 episode_steps=EP_STEPS):
        super().__init__()
        assert action_mode in ("direct", "arm"), \
            "helispike action_mode must be 'direct' (4 通道) or 'arm' (7 关节)"
        self.action_mode = action_mode
        self.wind_enabled = wind
        self.target_range = target_range
        self.episode_steps = episode_steps
        # 关节 P 控制器状态 (arm 模式): 与 helo_env.FastHeloEnv 第 86 行同款
        self.joints = np.zeros(7)
        self.joint_targets = np.zeros(7)
        if action_mode == "direct":
            self.action_space = spaces.Box(-1.0, 1.0, shape=(4,), dtype=np.float32)
        else:
            self.action_space = spaces.Box(-1.5, 1.5, shape=(7,), dtype=np.float32)
        self.observation_space = spaces.Box(-10.0, 10.0, shape=(12,), dtype=np.float32)
        self.rng = np.random.default_rng()
        self._init_fdm()

    def _init_fdm(self):
        root = _find_jsbsim_root()
        with SuppressJSBSimOutput():
            self.fdm = jsbsim.FGFDMExec(root, None)
            self.fdm.set_property_value('simulation/notify-level', 0)
            self.fdm.set_property_value('simulation/log-level', 0)
            self.fdm.disable_output()
            if not self.fdm.load_model("helispike", True):
                raise RuntimeError(f"load_model helispike failed at root={root}")
            self.fdm.set_property_value('simulation/notify-level', 0)
            self.fdm.set_property_value('simulation/log-level', 0)
            self.fdm.disable_output()

    def _start_engine(self):
        f = self.fdm
        f.set_property_value("propulsion/engine[0]/set-running", 1.0)
        f.set_property_value("propulsion/engine[1]/set-running", 1.0)
        f.set_property_value("propulsion/engine[0]/clutch", 1.0)
        f.set_property_value("fcs/throttle-cmd-norm[0]", 1.0)
        f.set_property_value("fcs/collective-cmd-norm", 0.0)

    def _raw(self):
        f = self.fdm
        s = {
            'agl': max(0.0, self._agl_integrated),
            'vz': f.get_property_value('velocities/h-dot-fps') * FPS2MPS,
            'u': 0.0,
            'v': 0.0,
            'pitch': math.radians(f.get_property_value('attitude/theta-deg')),
            'roll': math.radians(f.get_property_value('attitude/phi-deg')),
            'psi': math.radians(f.get_property_value('attitude/psi-deg')),
            'p': self._p_diff,
            'q': self._q_diff,
            'r': self._r_diff,
            'rpm': f.get_property_value('propulsion/engine[0]/rotor-rpm'),
            'wow': f.get_property_value('gear/unit[0]/WOW'),
        }
        s['u_kts'] = f.get_property_value('velocities/ve-kts')
        return s

    def _update_diffs(self):
        psi = math.radians(self.fdm.get_property_value('attitude/psi-deg'))
        phi = math.radians(self.fdm.get_property_value('attitude/phi-deg'))
        theta = math.radians(self.fdm.get_property_value('attitude/theta-deg'))
        dt = 1.0 / 120.0 if getattr(self, '_dt_deviation', False) else DT_PHYSICS
        self._r_diff = float(np.clip(wrap_pi(psi - self._psi_prev) / dt, -10.0, 10.0))
        self._p_diff = float(np.clip(wrap_pi(phi - self._phi_prev) / dt, -10.0, 10.0))
        self._q_diff = float(np.clip(wrap_pi(theta - self._theta_prev) / dt, -10.0, 10.0))
        self._psi_prev, self._phi_prev, self._theta_prev = psi, phi, theta
        hdot = self.fdm.get_property_value('velocities/h-dot-fps') * FPS2MPS
        if not math.isfinite(hdot):
            hdot = 0.0
        self._agl_integrated = float(np.clip(self._agl_integrated + hdot * dt, 0.0, 500.0))


    def _pos_ne(self):
        lat = self.fdm.get_property_value('position/lat-gc-deg')
        lon = self.fdm.get_property_value('position/long-gc-deg')
        return ((lat - self._lat0) * 111320.0,
                (lon - self._lon0) * 111320.0 * math.cos(math.radians(self._lat0)))

    def _obs(self, s):
        alt_err = self.target_alt - s['agl']
        pos_n, pos_e = self._pos_ne()
        raw_obs = np.array([
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
            float(pos_n / 50.0),
            float(pos_e / 50.0),
        ], dtype=np.float32)
        return self._finite_obs(raw_obs)

    def _arm_to_controls(self, action):
        """7 关节目标角 -> 4 维控制量 (与 jsbsim_helo_env.step 的 arm 分支同款语义).

        关节一阶 P 跟踪: 每控制步衰减 2.0*DT_CONTROL=0.04 (τ≈0.5s), 与一代
        jsbsim_helo_env (DT=0.02, 每控制步一步) 逐点一致 —— 一代 arm 策略向二代
        迁移 (D1/D2) 依赖这一点; 此前误用 DT_PHYSICS 导致等效 τ 慢 4 倍 (≈2s).
        归一化分母与 jsbsim_helo_env 一致:
            j[0] 总距 [-1.5, 1.5] -> [-1, 1] (分母 1.5, 零动作=悬停配平 0.70)
            j[1..3] 周期变距/脚蹬 [-0.8, 0.8] (分母 0.8)
            j[4..6] 暂未使用 (不进映射, 也不进 ctrl_pen).
        """
        self.joint_targets = np.asarray(action, dtype=np.float32).copy()
        self.joints += (self.joint_targets - self.joints) * 2.0 * DT_CONTROL
        j = self.joints
        collective = float(np.clip(j[0] / 1.5, -1.0, 1.0))
        cyclic_pitch = float(np.clip(j[1] / 0.8, -1.0, 1.0))
        cyclic_roll = float(np.clip(j[2] / 0.8, -1.0, 1.0))
        pedals = float(np.clip(j[3] / 0.8, -1.0, 1.0))
        return collective, cyclic_pitch, cyclic_roll, pedals

    def _apply_controls(self, u_coll, u_pitch, u_roll, u_yaw):
        """D3 cascade: the RL policy commands ATTITUDE TARGETS (outer loop), and the
        SAS inner loop tracks them every physics step. The policy must NOT drive the
        cyclic surfaces directly -- at +/-0.30 rad of raw surface authority a random
        or early-policy action destroys the hover within ~1 s (measured), which is
        why every previous run collapsed to -50/step-1 crashes.
        Collective stays a direct policy channel (it IS the altitude/vertical-speed
        actuator, harmless in comparison).
        """
        self._cmd_roll = float(math.radians(CMD_ATT_MAX_DEG) * u_roll)
        self._cmd_pitch = float(math.radians(CMD_ATT_MAX_DEG) * u_pitch)
        self._cmd_yaw_rate = float(u_yaw * CMD_YAW_RATE_MAX)
        coll = float(np.clip(HOVER_COLLECTIVE + u_coll * COLLECTIVE_RANGE, 0.0, 1.0))
        self._coll_cmd = coll
        self.fdm.set_property_value('fcs/collective-cmd-norm', coll)

    def _sas_substep(self, cmd_roll, cmd_pitch, cmd_yaw_rate, coll):
        """One SAS inner-loop update (writes cyclic + rudder, runs nothing).

        Rates come from attitude differences because velocities/{p,q,r}-radps are
        DEAD (const 0) in this JSBSim build -- see the SAS_K_* block for the
        calibration evidence and gain provenance.
        """
        f = self.fdm
        phi = f.get_property_value('attitude/phi-deg')
        theta = f.get_property_value('attitude/theta-deg')
        psi = f.get_property_value('attitude/psi-deg')
        roll_err = math.radians(phi) - cmd_roll
        pitch_err = math.radians(theta) - cmd_pitch
        ail = float(np.clip(ROLL_SIGN * (-SAS_K_ATT * roll_err
                                         - SAS_K_RATE * self._p_diff),
                            -AIL_LIMIT, AIL_LIMIT))
        ele = float(np.clip(PITCH_SIGN * (SAS_K_ATT * pitch_err
                                          - SAS_K_RATE * self._q_diff),
                            -ELE_LIMIT, ELE_LIMIT))
        # heading: track the commanded yaw rate around the current heading hold
        hdg_err = wrap_pi(math.radians(psi) - self._hdg_ref)
        rud = float(np.clip(HDG_SIGN * (0.08 * hdg_err) + cmd_yaw_rate,
                            -RUD_LIMIT, RUD_LIMIT))
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.set_property_value('fcs/rudder-cmd-norm', rud)
        f.set_property_value('fcs/collective-cmd-norm', coll)

    def _effective_dt(self):
        return 1.0 / 120.0 if getattr(self, '_dt_deviation', False) else DT_PHYSICS

    def _effective_dt_raw(self, use_contract_dt):
        return DT_PHYSICS if use_contract_dt else 1.0 / 120.0

    def _finite_obs(self, obs):
        clean = np.nan_to_num(obs, nan=0.0, posinf=10.0, neginf=-10.0)
        return np.clip(clean, -10.0, 10.0).astype(np.float32)

    def _sanitize_reward(self, r):
        clean = float(np.nan_to_num(r, nan=-50.0, posinf=100.0, neginf=-100.0))
        return float(np.clip(clean, -100.0, 100.0))

    def _reset_attempt(self, use_contract_dt):
        f = self.fdm
        with SuppressJSBSimOutput():
            f.load_ic('reset00.xml', True)
            f.run_ic()
            if use_contract_dt:
                f.set_dt(DT_PHYSICS)
            self._start_engine()
            f.disable_output()
            f.set_property_value('simulation/notify-level', 0)
            f.set_property_value('simulation/log-level', 0)
            if self.wind_enabled:
                f.set_property_value('atmosphere/wind-north-fps', float(self.rng.uniform(-6.0, 6.0)))
                f.set_property_value('atmosphere/wind-east-fps', float(self.rng.uniform(-6.0, 6.0)))

            self.t = 0.0
            self.steps = 0
            self.crashed = False
            self._alt_err_hist = []
            self._was_airborne = False

            self._psi_prev = math.radians(f.get_property_value('attitude/psi-deg'))
            self._phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
            self._theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
            self._p_diff = self._q_diff = self._r_diff = 0.0
            self._agl_integrated = 0.0
            self._lat0 = f.get_property_value('position/lat-gc-deg')
            self._lon0 = f.get_property_value('position/long-gc-deg')
            # outer-loop command state (defaults = wings-level hover hold)
            self._cmd_roll = 0.0
            self._cmd_pitch = 0.0
            self._cmd_yaw_rate = 0.0
            self._coll_cmd = HOVER_COLLECTIVE
            self._hdg_ref = math.radians(f.get_property_value('attitude/psi-deg'))

            # Phase1 spool (E-fix 2026-09-05: run at governor spool rate until the
            # rotor actually enters the contract band [495,565] -- 12-20 s sim.
            # The old fixed 1100 steps @dt=0.01 (5.5 s) left rpm at 60-170, so the
            # climb below flew at a fraction of rotor thrust and rolled over.)
            f.set_dt(0.01)
            f.set_property_value('fcs/collective-cmd-norm', 0.0)
            for _ in range(SPOOL_MAX_STEPS):
                f.run()
                if f.get_property_value('propulsion/engine[0]/rotor-rpm') >= SPOOL_RPM:
                    break
            f.set_dt(DT_PHYSICS)
            self._update_diffs()

            # Phase2: E-fix 2026-09-05 (v2) -- attitude-hold inner loop (D3 contract:
            # "SAS 内环 + RL 外环"). Gains from the E gain-sweep: ail = -K_ATT*phi,
            # ele = +K_ATT*theta with K_ATT=40, plus a LIGHT rate term K_RATE=0.05 on
            # rates REBUILT FROM ATTITUDE DIFFERENCES (p/q/r-radps are DEAD const-0 in
            # this JSBSim build, so the XML dampers in hs_afcs*.xml do nothing).
            # Validated by _e_sas_refine.py: 60 s real hover, max|att| 9.6 deg,
            # agl 17.6 m, vz -0.03 m/s; recovers from a 0.5 s full-aileron gust.
            hdg0 = math.radians(f.get_property_value('attitude/psi-deg'))
            phase2_steps = 1200  # 6 s at 200 Hz (attitude capture + altitude arrest)
            alt_hold = HOVER_COLLECTIVE
            for i in range(phase2_steps):
                phi = f.get_property_value('attitude/phi-deg')
                theta = f.get_property_value('attitude/theta-deg')
                psi = f.get_property_value('attitude/psi-deg')
                s = self._raw()
                ail = float(np.clip(ROLL_SIGN * (-SAS_K_ATT * math.radians(phi)
                                                 - SAS_K_RATE * self._p_diff),
                                    -AIL_LIMIT, AIL_LIMIT))
                ele = float(np.clip(PITCH_SIGN * (SAS_K_ATT * math.radians(theta)
                                                  - SAS_K_RATE * self._q_diff),
                                    -ELE_LIMIT, ELE_LIMIT))
                rud = float(np.clip(HDG_SIGN * 0.08 * wrap_pi(math.radians(psi) - hdg0),
                                    -RUD_LIMIT, RUD_LIMIT))
                # altitude: climb toward target_alt, then ARREST vertical speed so the
                # policy is handed a trimmed hover (vz~0) instead of a +5 m/s climb.
                alt_err = self.target_alt - s['agl']
                vs_cmd = float(np.clip(SAS_K_ALT * alt_err, -2.0, 2.5))
                alt_hold += SAS_K_VS * (vs_cmd - s['vz']) * DT_PHYSICS
                alt_hold = float(np.clip(alt_hold, 0.30, 0.95))
                self._coll_cmd = alt_hold
                self._sas_substep(0.0, 0.0, 0.0, alt_hold)
                f.run()
                self._update_diffs()
                if not math.isfinite(s['agl']) or abs(math.degrees(s['roll'])) > 45.0:
                    break
                # hand over only once the hover is actually trimmed: on target
                # altitude AND vertical speed arrested (else the policy inherits a
                # runaway climb and every episode ends in a crash).
                if (i > 400 and abs(self.target_alt - s['agl']) < 2.0
                        and abs(s['vz']) < 0.5):
                    break

            s = self._raw()
            if any(isinstance(v, float) and not math.isfinite(v) for v in s.values()):
                s = {k: (0.0 if isinstance(v, float) and not math.isfinite(v) else v) for k, v in s.items()}
                return False, s
            # E-fix 2026-09-05: health gate must reject TIPPED handover states.
            # The old gate only checked the rpm band, so a phi=84 deg wreck was
            # delivered to the policy and step-1 crashed (all sweeps 0%/100%).
            att_ok = (abs(s['pitch']) < MAX_ANG and abs(s['roll']) < MAX_ANG)
            healthy = att_ok and (RPM_BAND[0] - 95.0 <= s['rpm'] <= RPM_BAND[1] + 35.0)
            return healthy, s

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self.target_alt = float(self.rng.uniform(*self.target_range))
        self._prev_home_dist = 0.0  # v2.3: 回位势函数基准 (reset 在原点)
        # arm 模式: 执行器状态随回合清零 (对齐 jsbsim_helo_env._reset_state).
        # 不清零则上一回合末的关节角会以 τ≈0.5s 残留进下一回合初始段,
        # 污染 on-policy 数据并破坏同 seed 可复现性.
        self.joints = np.zeros(7)
        self.joint_targets = np.zeros(7)

        self._dt_deviation = False
        healthy, s = self._reset_attempt(True)
        if not healthy:
            self._dt_deviation = True
            healthy, s = self._reset_attempt(False)
        if not healthy:
            s = {k: (0.0 if isinstance(v, float) and not math.isfinite(v) else v) for k, v in s.items()}
        return self._obs(s), {"target_alt": self.target_alt}

    def step(self, action):
        action = np.asarray(action, dtype=np.float64).flatten()
        if self.action_mode == "arm":
            u_coll, u_pitch, u_roll, u_yaw = self._arm_to_controls(action)
        else:
            u_coll = float(np.clip(action[0], -1.0, 1.0))
            u_pitch = float(np.clip(action[1], -1.0, 1.0))
            u_roll = float(np.clip(action[2], -1.0, 1.0))
            u_yaw = float(np.clip(action[3], -1.0, 1.0))

        prev_alt_err = abs(self._agl_integrated - self.target_alt)

        self._apply_controls(u_coll, u_pitch, u_roll, u_yaw)
        nan_hit = False
        for _ in range(ACTION_REPEAT):
            # SAS inner loop runs at physics rate, tracking the policy's attitude
            # targets (D3: "SAS 内环 + RL 外环"). Without this the policy's raw cyclic
            # commands tear the hover apart within ~1 s.
            self._sas_substep(self._cmd_roll, self._cmd_pitch, self._cmd_yaw_rate,
                              self._coll_cmd)
            self.fdm.run()
            self._update_diffs()
            if not math.isfinite(self.fdm.get_property_value('attitude/theta-deg')):
                nan_hit = True
                break
        self.t += self._effective_dt() * ACTION_REPEAT
        self.steps += 1

        s = self._raw()
        if nan_hit or any(isinstance(v, float) and not math.isfinite(v) for v in s.values()):
            self.crashed = True
            info = {"crashed": True, "nan_exploded": True, "agl": 0.0, "success": False, "mean_alt_err": 99.0}
            return np.zeros(self.observation_space.shape[0], dtype=np.float32), -50.0, True, False, info

        alt_err = abs(s['agl'] - self.target_alt)
        self._alt_err_hist.append(alt_err)

        if abs(s['pitch']) > CRASH_ANG or abs(s['roll']) > CRASH_ANG:
            self.crashed = True
        if self._was_airborne and bool(s['wow'] > 0.5) and s['vz'] < -3.0:
            self.crashed = True
        if s['agl'] > 1.0:
            self._was_airborne = True

        progress = 2.0 * (prev_alt_err - alt_err)
        att_pen = 0.3 * (abs(s['pitch']) + abs(s['roll']))
        # 只罚接入映射的通道 (direct 4 维 / arm 的 j0..j3); j4..j6 预留不罚,
        # 否则策略会为省奖励而压制与飞行无关的预留关节
        ctrl_pen = 0.01 * float(np.sum(np.square(action[:4])))
        grounded_pen = 0.1 if (s['agl'] < 0.5 and self.t > 2.0) else 0.0
        v_horiz_mps = s['u_kts'] * KTS2MPS
        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)
        hover_bonus = 0.0
        if alt_err < 2.0 and abs(s['pitch']) < math.radians(10) and abs(s['roll']) < math.radians(10):
            hover_bonus = 2.0
        elif alt_err < 4.0:
            hover_bonus = 0.8

        # E 轨 2026-09-13 单变量定标起点: 恢复"已证最优"的漂移直罚 0.02、无势函数。
        #   定标实验已证: 0.02=57%(平台) / 0.05=37% / 0.1=17% —— 罚款单调有害;
        #   v2.3 逐步式势函数引入 reward hacking 崩塌(std 1.84);
        #   v2.4 净值式势函数+熵约束更差(23%, 且压熵后成绩同步下滑→熵非主因)。
        #   故回到此干净起点, 后续每个变量单独验证、单变量通过才续训。
        reward = (progress - att_pen - ctrl_pen - grounded_pen + hover_bonus + 0.1
                  - 0.1 * v_horiz_mps - 0.02 * net_drift)

        terminated = False
        if self.crashed:
            reward -= 50.0
            terminated = True
        truncated = self.steps >= self.episode_steps

        reward = self._sanitize_reward(reward)


        info = {"agl": s['agl'], "vz": s['vz'], "target_alt": self.target_alt,
                "alt_err": alt_err, "crashed": self.crashed, "rpm": s['rpm'],
                "wow": s['wow'], "net_drift_m": float(net_drift)}
        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self._alt_err_hist)))
            tail = self._alt_err_hist[-n_tail:]
            info["success"] = ((not self.crashed) and (np.mean(tail) < 3.0)
                               and (net_drift < 20.0))
            info["mean_alt_err"] = float(np.mean(self._alt_err_hist))
        return self._obs(s), reward, terminated, truncated, info


class HelispikeTakeoffEnv(HelispikeHoverEnv):
    """二代机型起飞科目 (heli_takeoff)。地面冷车/起转直接交付 RL 起飞。"""

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        f = self.fdm
        f.load_ic('reset00.xml', True)
        f.run_ic()
        if not getattr(self, '_dt_deviation', False):
            f.set_dt(DT_PHYSICS)
        self._start_engine()
        for _ in range(int(25.0 / self._effective_dt_raw(not getattr(self, '_dt_deviation', False)))):
            f.set_property_value('fcs/collective-cmd-norm', 0.0)
            f.run()
            rpm = f.get_property_value('propulsion/engine[0]/rotor-rpm')
            if RPM_BAND[0] <= rpm <= RPM_BAND[1]:
                break
        self._psi_prev = math.radians(f.get_property_value('attitude/psi-deg'))
        self._phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
        self._theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
        self._p_diff = self._q_diff = self._r_diff = 0.0
        self._agl_integrated = 0.0
        self._lat0 = f.get_property_value('position/lat-gc-deg')
        self._lon0 = f.get_property_value('position/long-gc-deg')
        self.t = 0.0
        self.steps = 0
        self.crashed = False
        self._alt_err_hist = []
        self._was_airborne = False
        s = self._raw()
        if any(isinstance(v, float) and not math.isfinite(v) for v in s.values()):
            s = {k: (0.0 if isinstance(v, float) and not math.isfinite(v) else v) for k, v in s.items()}
        return self._obs(s), {"target_alt": self.target_alt}

    def step(self, action):
        obs, reward, terminated, truncated, info = super().step(action)
        if (not terminated and not truncated) and self._agl_integrated >= self.target_alt:
            terminated = True
            pos_n, pos_e = self._pos_ne()
            net_drift = math.hypot(pos_n, pos_e)
            s = self._raw()
            info["success"] = (not self.crashed
                               and abs(s['pitch']) < math.radians(15)
                               and abs(s['roll']) < math.radians(15)
                               and net_drift < 30.0)
            info["net_drift_m"] = float(net_drift)
            reward = self._sanitize_reward(reward + 50.0)
        return obs, reward, terminated, truncated, info


class HelispikeLandingEnv(HelispikeHoverEnv):
    """二代机型定点软着陆科目 (heli_landing)。"""

    def __init__(self, action_mode="direct", wind=True, initial_alt=20.0, episode_steps=1200):
        super().__init__(action_mode=action_mode, wind=wind, target_range=(initial_alt, initial_alt), episode_steps=episode_steps)
        self.initial_alt = initial_alt
        self.landed = False
        self.touchdown_vs = None
        self.profile_errs = []

    def reset(self, *, seed=None, options=None):
        obs, info = super().reset(seed=seed, options=options)
        self.landed = False
        self.touchdown_vs = None
        self.profile_errs = []
        return obs, info

    def step(self, action):
        action = np.asarray(action, dtype=np.float64).flatten()
        if self.action_mode == "arm":
            u_coll, u_pitch, u_roll, u_yaw = self._arm_to_controls(action)
        else:
            u_coll = float(np.clip(action[0], -1.0, 1.0))
            u_pitch = float(np.clip(action[1], -1.0, 1.0))
            u_roll = float(np.clip(action[2], -1.0, 1.0))
            u_yaw = float(np.clip(action[3], -1.0, 1.0))

        self._apply_controls(u_coll, u_pitch, u_roll, u_yaw)
        nan_hit = False
        for _ in range(ACTION_REPEAT):
            self._sas_substep(self._cmd_roll, self._cmd_pitch, self._cmd_yaw_rate,
                              self._coll_cmd)
            self.fdm.run()
            self._update_diffs()
            if not math.isfinite(self.fdm.get_property_value('attitude/theta-deg')):
                nan_hit = True
                break
        self.t += self._effective_dt() * ACTION_REPEAT
        self.steps += 1

        s = self._raw()
        if nan_hit or any(isinstance(v, float) and not math.isfinite(v) for v in s.values()):
            self.crashed = True
            info = {"crashed": True, "nan_exploded": True, "agl": 0.0, "success": False, "mean_alt_err": 99.0}
            return np.zeros(self.observation_space.shape[0], dtype=np.float32), -50.0, True, False, info

        if abs(s['pitch']) > CRASH_ANG or abs(s['roll']) > CRASH_ANG:
            self.crashed = True

        # 着陆期望下沉率剖面
        agl = s['agl']
        des_vz = -float(np.clip(0.12 * agl + 0.3, 0.4, 1.5))
        vz_err = abs(s['vz'] - des_vz)
        self.profile_errs.append(vz_err)

        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)
        v_horiz_mps = s['u_kts'] * KTS2MPS

        # 接地判定: AGL < 0.25m 或 WOW > 0.5
        terminated = False
        if (s['wow'] > 0.5 or agl < 0.25) and not self.landed:
            self.landed = True
            self.touchdown_vs = s['vz']
            terminated = True
            if self.touchdown_vs < -3.0:
                self.crashed = True

        reward = (- 2.0 * vz_err
                  - 0.3 * (abs(s['pitch']) + abs(s['roll']))
                  - 0.1 * v_horiz_mps
                  - 0.02 * net_drift)

        if self.landed:
            if not self.crashed and self.touchdown_vs >= -1.2 and net_drift < 30.0:
                reward += 50.0
            elif not self.crashed and self.touchdown_vs >= -3.0:
                reward += 20.0
            else:
                reward -= 50.0

        if self.crashed:
            reward -= 50.0
            terminated = True

        reward = self._sanitize_reward(reward)
        truncated = self.steps >= self.episode_steps

        info = {"agl": s['agl'], "vz": s['vz'], "landed": self.landed,
                "touchdown_vs": self.touchdown_vs, "crashed": self.crashed,
                "net_drift_m": float(net_drift)}
        if terminated or truncated:
            info["success"] = (self.landed and (not self.crashed)
                               and (self.touchdown_vs is not None and self.touchdown_vs >= -1.2)
                               and net_drift < 30.0 and v_horiz_mps < 1.0)
            info["mean_alt_err"] = float(np.mean(self.profile_errs)) if self.profile_errs else 0.0
        return self._obs(s), reward, terminated, truncated, info


class HelispikeForwardEnv(HelispikeHoverEnv):
    """二代机型前飞速度跟踪 (heli_forward)。按 0.2-WIP 包络目标 8.0 kt。"""

    def __init__(self, action_mode="direct", wind=True, target_speed_kt=8.0, episode_steps=1000):
        super().__init__(action_mode=action_mode, wind=wind, target_range=(20.0, 20.0), episode_steps=episode_steps)
        self.target_speed_kt = target_speed_kt
        self.speed_errs = []

    def reset(self, *, seed=None, options=None):
        obs, info = super().reset(seed=seed, options=options)
        self.speed_errs = []
        return obs, info

    def step(self, action):
        obs, reward, terminated, truncated, info = super().step(action)
        s = self._raw()
        spd = s['u_kts']
        spd_err = abs(spd - self.target_speed_kt)
        self.speed_errs.append(spd_err)

        speed_reward = -1.0 * spd_err + (2.0 if spd_err < 2.0 else 0.0)
        reward = self._sanitize_reward(reward + speed_reward)

        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self.speed_errs)))
            tail = self.speed_errs[-n_tail:]
            info["success"] = (not self.crashed) and (np.mean(tail) < 2.5) and (info.get("alt_err", 0) < 4.0)
            info["mean_alt_err"] = float(np.mean(self.speed_errs))
        return obs, reward, terminated, truncated, info


def make_helispike_env(task="heli_hover", action_mode="direct", wind=True, target_range=(10.0, 30.0), seed=0):
    t = task.lower()
    if t in ["heli_hover", "helispike_hover"]:
        return HelispikeHoverEnv(action_mode=action_mode, wind=wind, target_range=target_range)
    elif t in ["heli_takeoff", "helispike_takeoff"]:
        return HelispikeTakeoffEnv(action_mode=action_mode, wind=wind, target_range=target_range)
    elif t in ["heli_landing", "helispike_landing"]:
        return HelispikeLandingEnv(action_mode=action_mode, wind=wind)
    elif t in ["heli_forward", "helispike_forward"]:
        return HelispikeForwardEnv(action_mode=action_mode, wind=wind)
    else:
        # 默认回退 hover
        return HelispikeHoverEnv(action_mode=action_mode, wind=wind, target_range=target_range)
