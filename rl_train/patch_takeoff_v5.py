# -*- coding: utf-8 -*-
"""
patch_takeoff_v5.py — 整改令 §6.2 奖励等价类修复
修改 TakeoffTask/TakeoffHardTask/TakeoffFGTask:
  1. 观测 12->14维 (加北向/东向位移)
  2. 奖励加水平速度惩罚 + 漂移惩罚
  3. 成功判据加漂移上限 30m
"""
import re

FILE = r"rl_train\jsbsim_tasks.py"

with open(FILE, "r", encoding="utf-8") as f:
    src = f.read()

original = src

# ========== 1. TakeoffTask: EXTRA = 2 -> EXTRA = 4, 加 DRIFT_LIMIT_M ==========
src = src.replace(
    "    EXTRA = 2\n    TARGET_ALT_M = 14.0\n    SUCCESS_SETTLE_S = 1.0",
    "    EXTRA = 4  # v5: 12->14维 (加北向/东向位移), 整改令 §6.2\n    TARGET_ALT_M = 14.0\n    SUCCESS_SETTLE_S = 1.0\n    DRIFT_LIMIT_M = 30.0  # v5: 成功判据漂移上限"
)

# ========== 2. TakeoffTask._obs: 加 pos_north/50, pos_east/50 ==========
old_obs_takeoff = """    def _obs(self, s):
        alt_err = self.target_alt_m - s['agl']
        return np.concatenate([
            self._hover_obs(s, self.target_alt_m),
            np.array([
                float(alt_err / self.target_alt_m),
                float(s['vz'] / 5.0),
            ], dtype=np.float32),
        ])"""

new_obs_takeoff = """    def _obs(self, s):
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
        ])"""

src = src.replace(old_obs_takeoff, new_obs_takeoff, 1)

# ========== 3. TakeoffTask.step: 奖励加水平惩罚, 成功判据加漂移上限 ==========
# 3a. 在 reward 计算前加水平速度和漂移
old_reward_takeoff = """        reward = alt_reward + vz_reward + survive - att_penalty - smooth_penalty

        # 成功判定: 达到目标高度且姿态稳定持续 1s
        if (s['agl'] >= self.target_alt_m and abs(s['roll']) < math.radians(15)
                and abs(s['pitch']) < math.radians(15)):"""

new_reward_takeoff = """        # v5: 水平速度惩罚 + 漂移惩罚 (整改令 §6.2, G1审计滑翔漂移339m仍判成功)
        v_horiz = math.hypot(s['u'], s['v'])  # 体轴水平速度 (m/s)
        pos_n, pos_e = self._pos_ne()
        net_drift = math.hypot(pos_n, pos_e)
        horiz_speed_pen = 0.1 * v_horiz       # 水平速度每 m/s 罚 0.1
        drift_pen = 0.02 * net_drift           # 漂移每 m 罚 0.02

        reward = alt_reward + vz_reward + survive - att_penalty - smooth_penalty - horiz_speed_pen - drift_pen

        # 成功判定: 达到目标高度且姿态稳定持续 1s + 漂移 < 30m (v5)
        if (s['agl'] >= self.target_alt_m and abs(s['roll']) < math.radians(15)
                and abs(s['pitch']) < math.radians(15)
                and net_drift < self.DRIFT_LIMIT_M):"""

src = src.replace(old_reward_takeoff, new_reward_takeoff, 1)

# 3b. 成功 info 加 net_drift
old_success_info_takeoff = """                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "settled": True}"""
new_success_info_takeoff = """                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "net_drift_m": float(net_drift),
                        "settled": True}"""
src = src.replace(old_success_info_takeoff, new_success_info_takeoff, 1)

# ========== 4. TakeoffHardTask._obs: 加 pos_north/50, pos_east/50 (带噪声) ==========
old_obs_hard = """    def _obs(self, s):
        \"\"\"带传感器噪声的观测\"\"\"
        noisy_s = self._add_sensor_noise(s)
        alt_err = self.target_alt_m - noisy_s['agl']
        return np.concatenate([
            self._hover_obs(noisy_s, self.target_alt_m),
            np.array([
                float(alt_err / self.target_alt_m),
                float(noisy_s['vz'] / 5.0),
            ], dtype=np.float32),
        ])"""

new_obs_hard = """    def _obs(self, s):
        \"\"\"带传感器噪声的观测 (v5: 14维, 加水平位置)\"\"\"
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
        ])"""

src = src.replace(old_obs_hard, new_obs_hard, 1)

# ========== 5. TakeoffHardTask.step: 奖励加水平惩罚, 成功判据加漂移上限 ==========
old_reward_hard = """        reward = alt_reward + vz_reward + survive - att_penalty - smooth_penalty

        # 更严格的成功判定: 15m + roll/pitch < 10° + 稳定 2s
        if (s['agl'] >= self.target_alt_m
                and abs(s['roll']) < math.radians(self.MAX_ROLL_DEG)
                and abs(s['pitch']) < math.radians(self.MAX_PITCH_DEG)):"""

new_reward_hard = """        # v5: 水平速度惩罚 + 漂移惩罚
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
                and net_drift < self.DRIFT_LIMIT_M):"""

src = src.replace(old_reward_hard, new_reward_hard, 1)

# hard 成功 info 加 net_drift
old_success_info_hard = """                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "settled": True, "hard_mode": True}"""
new_success_info_hard = """                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "net_drift_m": float(net_drift),
                        "settled": True, "hard_mode": True}"""
src = src.replace(old_success_info_hard, new_success_info_hard, 1)

# ========== 6. TakeoffFGTask._obs: 加 pos_north/50, pos_east/50 (带传感器模型) ==========
old_obs_fg = """    def _obs(self, s):
        \"\"\"带传感器噪声和延迟的观测\"\"\"
        sensor_s = self._apply_sensor_model(s)
        alt_err = self.target_alt_m - sensor_s['agl']
        return np.concatenate([
            self._hover_obs(sensor_s, self.target_alt_m),
            np.array([
                float(alt_err / self.target_alt_m),
                float(sensor_s['vz'] / 5.0),
            ], dtype=np.float32),
        ])"""

new_obs_fg = """    def _obs(self, s):
        \"\"\"带传感器噪声和延迟的观测 (v5: 14维, 加水平位置)\"\"\"
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
        ])"""

src = src.replace(old_obs_fg, new_obs_fg, 1)

# ========== 7. TakeoffFGTask.step: 奖励加水平惩罚, 成功判据加漂移上限 ==========
old_reward_fg = """        reward = alt_reward + vz_reward + survive + att_stable_reward - att_penalty - smooth_penalty

        # 成功判定
        if (s['agl'] >= self.target_alt_m
                and abs(s['roll']) < math.radians(self.MAX_ROLL_DEG)
                and abs(s['pitch']) < math.radians(self.MAX_PITCH_DEG)):"""

new_reward_fg = """        # v5: 水平速度惩罚 + 漂移惩罚
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
                and net_drift < self.DRIFT_LIMIT_M):"""

src = src.replace(old_reward_fg, new_reward_fg, 1)

# fg 成功 info 加 net_drift
old_success_info_fg = """                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "settled": True, "fg_aligned": True,
                        "final_rpm": float(self._current_rpm)}"""
new_success_info_fg = """                info = {"success": True, "crashed": False,
                        "final_agl_m": float(s['agl']),
                        "mean_alt_err_m": float(np.mean(self.alt_errors)),
                        "net_drift_m": float(net_drift),
                        "settled": True, "fg_aligned": True,
                        "final_rpm": float(self._current_rpm)}"""
src = src.replace(old_success_info_fg, new_success_info_fg, 1)

# ========== 验证修改 ==========
changes = 0
checks = [
    ("EXTRA = 4", "TakeoffTask EXTRA=4"),
    ("DRIFT_LIMIT_M = 30.0", "DRIFT_LIMIT_M"),
    ("float(pos_n / 50.0)", "pos_n in obs"),
    ("horiz_speed_pen = 0.1 * v_horiz", "horiz_speed_pen"),
    ("drift_pen = 0.02 * net_drift", "drift_pen"),
    ("net_drift < self.DRIFT_LIMIT_M", "drift limit in success"),
]
for pattern, name in checks:
    count = src.count(pattern)
    print(f"  [{name}] '{pattern}': {count} occurrences")
    if count > 0:
        changes += 1

if src == original:
    print("ERROR: No changes made!")
else:
    with open(FILE, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"\nSUCCESS: {changes}/7 patterns found, file written.")
    print(f"Original length: {len(original)}, New length: {len(src)}")
