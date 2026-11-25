# -*- coding: utf-8 -*-
"""
patch_hover_v5.py — 整改令 §6.2 hover 奖励等价类修复
修改 JSBSimHeloEnv:
  1. 加水平位置记录 (_lat0/_lon0 + _pos_ne)
  2. 奖励加水平速度惩罚 + 漂移惩罚
  3. 成功判据加漂移上限 20m
"""
import re

FILE = r"rl_train\jsbsim_helo_env.py"

with open(FILE, "r", encoding="utf-8") as f:
    src = f.read()

original = src

# ========== 1. _reset_state: 加起始点记录 ==========
old_reset = """    def _reset_state(self):
        self._reset_jsbsim()
        self.target_alt = float(self.rng.uniform(*self.target_range))
        self.t = 0.0
        self.steps = 0
        self.crashed = False
        self._alt_err_hist = []"""

new_reset = """    def _reset_state(self):
        self._reset_jsbsim()
        self.target_alt = float(self.rng.uniform(*self.target_range))
        self.t = 0.0
        self.steps = 0
        self.crashed = False
        self._alt_err_hist = []
        # v5: 记录起始点经纬度 (用于漂移计算)
        self._lat0 = self.fdm.get_property_value('position/lat-geod-deg')
        self._lon0 = self.fdm.get_property_value('position/long-gc-deg')"""

src = src.replace(old_reset, new_reset, 1)

# ========== 2. 加 _pos_ne 方法 (在 _get_raw_state 之后) ==========
old_raw_end = """            'yaw_rate': r_rad_s,
        }

    def _obs(self, s):"""

new_raw_end = """            'yaw_rate': r_rad_s,
        }

    def _pos_ne(self):
        \"\"\"v5: 相对起始点的北/东位置 (m)\"\"\"
        lat = self.fdm.get_property_value('position/lat-geod-deg')
        lon = self.fdm.get_property_value('position/long-gc-deg')
        M_PER_DEG_LAT = 111320.0
        return ((lat - self._lat0) * M_PER_DEG_LAT,
                (lon - self._lon0) * M_PER_DEG_LAT * math.cos(math.radians(self._lat0)))

    def _obs(self, s):"""

src = src.replace(old_raw_end, new_raw_end, 1)

# ========== 3. step: 奖励加水平惩罚 ==========
old_reward = """        # ---- 势函数奖励 + 悬停奖励 ----
        progress = 2.0 * (prev_alt_err - alt_err)  # 势函数梯度
        att_pen = 0.3 * (abs(s['pitch']) + abs(s['roll']))
        ctrl_pen = 0.01 * float(np.sum(np.square(action)))
        grounded_pen = 0.1 if (s['agl'] < 0.5 and self.t > 2.0) else 0.0

        hover_bonus = 0.0
        if alt_err < 2.0 and abs(s['pitch']) < math.radians(10) and abs(s['roll']) < math.radians(10):
            hover_bonus = 2.0
        elif alt_err < 4.0:
            hover_bonus = 0.8

        reward = progress - att_pen - ctrl_pen - grounded_pen + hover_bonus + 0.1"""

new_reward = """        # ---- 势函数奖励 + 悬停奖励 ----
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

        reward = progress - att_pen - ctrl_pen - grounded_pen + hover_bonus + 0.1 - horiz_speed_pen - drift_pen"""

src = src.replace(old_reward, new_reward, 1)

# ========== 4. 成功判据加漂移上限 ==========
old_success = """        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self._alt_err_hist)))
            tail = self._alt_err_hist[-n_tail:]
            info["success"] = (not self.crashed) and (np.mean(tail) < 3.0)
            info["mean_alt_err"] = float(np.mean(self._alt_err_hist))"""

new_success = """        if terminated or truncated:
            n_tail = max(1, int(0.1 * len(self._alt_err_hist)))
            tail = self._alt_err_hist[-n_tail:]
            # v5: 成功判据加漂移上限 20m (整改令 §5 G4: 悬停净漂移<20m)
            info["success"] = (not self.crashed) and (np.mean(tail) < 3.0) and (net_drift < 20.0)
            info["mean_alt_err"] = float(np.mean(self._alt_err_hist))
            info["net_drift_m"] = float(net_drift)"""

src = src.replace(old_success, new_success, 1)

# ========== 验证修改 ==========
checks = [
    ("self._lat0 = self.fdm.get_property_value", "起始点记录"),
    ("def _pos_ne(self):", "_pos_ne 方法"),
    ("horiz_speed_pen = 0.1 * v_horiz", "水平速度惩罚"),
    ("drift_pen = 0.02 * net_drift", "漂移惩罚"),
    ("net_drift < 20.0", "漂移上限 20m"),
    ("info[\"net_drift_m\"]", "drift info"),
]
for pattern, name in checks:
    count = src.count(pattern)
    print(f"  [{name}] '{pattern}': {count} occurrences")

if src == original:
    print("ERROR: No changes made!")
else:
    with open(FILE, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"\nSUCCESS: file written.")
    print(f"Original length: {len(original)}, New length: {len(src)}")
