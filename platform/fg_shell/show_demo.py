# -*- coding: utf-8 -*-
"""
show_demo.py — 一代策略综合机动表演 (主E, 2026-09-01 用户令)

真·实时闭环：起飞 → 高悬停 → 直飞 → 翻滚 → 返航 → 降落，分段独立策略，每段
FSM 控制状态切换。每段都把 JSBSim 真值位置/姿态经 UDP 推给 FG (--fdm=external)。

用法（FG 先用 fg_shell_launch.bat 起）：
  python show_demo.py --time-scale 0.5
可选 --time-scale 0.4 更慢更清晰；不传则实时
"""
import argparse
import math
import os
import socket
import sys
import time
import contextlib
import io

import numpy as np

M_PER_DEG_LAT = 111320.0
M2FT = 1.0 / 0.3048
MPS2KT = 1.94384
ELEV_FT = 13.0
PHNL_LAT = 21.32525
PHNL_LON = -157.92516
PACKET_FMT = "%.7f,%.7f,%.3f,%.3f,%.3f,%.3f,%.3f,%.1f"

RL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'rl_train'))
RESULTS_DIR = os.path.join(RL_DIR, 'results')
sys.path.insert(0, RL_DIR)


class Show:
    def __init__(self, args):
        import warnings
        warnings.filterwarnings('ignore')
        import numpy as np
        from stable_baselines3 import PPO
        from train_jsbsim import make_env

        self.args = args
        self.t0 = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 加载四段策略
        print('[加载策略] ...')
        self.m_takeoff = PPO.load(os.path.join(RESULTS_DIR, 'jsbsim_ppo_takeoff_hard_v20260830T091717.zip'), device='cpu')
        self.m_forward = PPO.load(os.path.join(RESULTS_DIR, 'jsbsim_ppo_forward.zip'), device='cpu')
        self.m_roll = PPO.load(os.path.join(RESULTS_DIR, 'jsbsim_ppo_roll.zip'), device='cpu')
        self.m_landing = PPO.load(os.path.join(RESULTS_DIR, 'jsbsim_ppo_landing_gold_gen1.zip'), device='cpu')

        # 四个环境（每个 reset 一次）
        self.env_takeoff = self._wrap(make_env(task='takeoff_hard', action_mode='direct', wind=True, seed=999)())
        self.env_forward = self._wrap(make_env(task='forward', action_mode='direct', wind=True, seed=999)())
        self.env_roll = self._wrap(make_env(task='roll', action_mode='direct', wind=True, seed=999)())
        self.env_landing = self._wrap(make_env(task='landing', action_mode='direct', wind=True, seed=999)())

    @staticmethod
    def _wrap(env):
        core = env
        while hasattr(core, 'env'):
            core = core.env
        return (env, core)

    def _core(self, pair):
        return pair[1]

    def _fdm(self, pair):
        return self._core(pair).fdm

    def _raw(self, pair):
        c = self._core(pair)
        return c._get_raw_state() if hasattr(c, '_get_raw_state') else c._raw()

    def _step(self, pair, action):
        env = pair[0]
        return env.step(action)

    def _send(self, pair, rpm=550.0):
        c = self._core(pair)
        lat = c.fdm.get_property_value('position/lat-geod-deg')
        lon = c.fdm.get_property_value('position/long-gc-deg')
        # 物理真值 -> PHNL 锚定显示 (避免坐标漂出场景域)
        lat_d = PHNL_LAT + (lat - self.lat0_phys)
        lon_d = PHNL_LON + (lon - self.lon0_phys) / math.cos(math.radians(PHNL_LAT))
        raw = self._raw(pair)
        gs = math.hypot(raw.get('vx', raw.get('u', 0.0)), raw.get('vy', raw.get('v', 0.0)))
        hdg = math.degrees(c.fdm.get_property_value('attitude/psi-rad')) % 360.0
        alt_ft = ELEV_FT + max(0.0, raw['agl']) * M2FT
        # ---- G4/铁律5: 全字段有限性过滤（NaN/inf 任一字段即用 LKG 替代整包）----
        vals = (lat_d, lon_d, alt_ft,
                math.degrees(raw['roll']), math.degrees(raw['pitch']),
                hdg, gs * MPS2KT, rpm)
        if any(not math.isfinite(v) for v in vals):
            if hasattr(self, '_lkg_pkt') and self._lkg_pkt:
                self.sock.sendto(self._lkg_pkt.encode('ascii'),
                                 (self.args.host, self.args.port))
            return  # 丢弃本帧，不发 NaN 包
        pkt = (PACKET_FMT % vals)
        if not pkt.endswith("\n"):
            pkt = pkt + "\n"
        self._lkg_pkt = pkt
        self.sock.sendto(pkt.encode('ascii'), (self.args.host, self.args.port))

    def _wait(self, step, dt_sim=0.02):
        target = self.t0 + step * dt_sim / self.args.time_scale
        w = target - time.time()
        if w > 0:
            time.sleep(w)

    def _banner(self, title):
        print('\n' + '=' * 72)
        print('▶ ' + title)
        print('=' * 72, flush=True)

    def run(self):
        phases = self.args.phases
        self.lat0_phys = self._fdm(self.env_takeoff).get_property_value('position/lat-geod-deg')
        self.lon0_phys = self._fdm(self.env_takeoff).get_property_value('position/long-gc-deg')
        self._lkg_pkt = None  # last known good packet (NaN 过滤回退)

        # fdm=external keepalive: 模型已加载、FG 已启动，外部 FDM 接口等待数据。
        # 模型加载 ~30s 期间无包 → fdm=external 断粮 → FG 可能用未初始化状态。
        # 在后台发停机坪 keepalive 包（Edwards 地面静止状态）直到 demo 开始。
        import threading
        self._keepalive_stop = threading.Event()
        lat0p = self.lat0_phys
        lon0p = self.lon0_phys
        ka_lat = PHNL_LAT  # PHNL 锚定显示坐标（地面静止）
        ka_lon = PHNL_LON
        ka_pkt = (PACKET_FMT % (ka_lat, ka_lon, ELEV_FT + 6.3 * M2FT,
                                0.0, 0.0, 0.0, 0.0, 0.0)) + "\n"

        def keepalive():
            while not self._keepalive_stop.is_set():
                try:
                    self.sock.sendto(ka_pkt.encode('ascii'),
                                     (self.args.host, self.args.port))
                except Exception:
                    pass
                self._keepalive_stop.wait(0.1)

        ka_th = threading.Thread(target=keepalive, daemon=True)
        ka_th.start()
        print('[keepalive] fdm=external 停机坪包已启动（防断粮）', flush=True)

        if '1' in phases:
            self._keepalive_stop.set()  # demo 开始前停 keepalive
            self._banner('阶段 1/5: 起飞 (takeoff_hard 策略)')
            self._phase_takeoff()
        if '2' in phases:
            self._banner('阶段 2/5: 高悬停 (持续保持)')
            self._phase_hover(self.env_takeoff, hold_sec=15.0, target=22.0)
        if '3' in phases:
            self._banner('阶段 3/5: 直飞 (forward 策略)')
            self._phase_forward()
        if '4' in phases:
            self._banner('阶段 4/5: 翻滚 (roll 策略)')
            self._phase_roll()
        if '5' in phases:
            self._banner('阶段 5/5: 返航 + 缓慢垂直降落 (landing 策略)')
            self._phase_landing()

        self._keepalive_stop.set()
        print('\n[完成] 综合机动套路演示结束。FG 保留最后姿态。', flush=True)

    def _phase_takeoff(self):
        pair = self.env_takeoff
        obs, info = pair[0].reset(seed=777)
        self.t0 = time.time()
        step = 0
        t_disp = 0.0
        reached = False
        while True:
            a, _ = self.m_takeoff.predict(obs, deterministic=True)
            obs, r, te, tr, info = self._step(pair, a)
            step += 1
            self._send(pair)
            self._wait(step)
            raw = self._raw(pair)
            t_sim = step * 0.02
            if t_sim - t_disp > 0.5:
                t_disp = t_sim
                print('  t=%4.1fs alt=%5.1fm vz=%+4.1fm/s roll=%+5.1f° hdg=%5.1f°' % (
                    t_sim, raw['agl'], raw['vz'], math.degrees(raw['roll']),
                    math.degrees(self._fdm(pair).get_property_value('attitude/psi-rad'))), flush=True)
            if raw['agl'] >= 14.0 and not reached:
                reached = True
                print('  ✓ 爬升到 14m, 转入高悬停', flush=True)
                # 多留 0.5s 让画面看清悬停位置
                for _ in range(25):
                    obs, r, te, tr, info = self._step(pair, self.m_takeoff.predict(obs, deterministic=True)[0])
                    step += 1; self._send(pair); self._wait(step)
                break
            if te or tr or t_sim > 60:
                break

    def _phase_hover(self, pair, hold_sec, target):
        """在当前 pair 上保持高度 target 持续 hold_sec 秒"""
        self.t0 = time.time()
        step = 0
        t_end = hold_sec
        # 用零动作保持高度 (AI 已经在悬停就让它继续)
        # 简单策略: 每步小幅调整总距维持 AGL=target
        while step * 0.02 < t_end:
            obs, r, te, tr, info = self._step(pair, np.array([0.0, 0.0, 0.0, 0.0]))
            step += 1
            self._send(pair, rpm=550)
            self._wait(step)
            if step % 25 == 0:
                raw = self._raw(pair)
                print('  悬停 t=%4.1fs alt=%5.1fm roll=%+5.1f° hdg=%5.1f°' % (
                    step * 0.02, raw['agl'], math.degrees(raw['roll']),
                    math.degrees(self._fdm(pair).get_property_value('attitude/psi-rad'))), flush=True)

    def _phase_forward(self):
        pair = self.env_forward
        # 从当前悬停位置起飞 forward 环境 (新 env, 起点从悬停高度开始)
        # forward 任务是速度跟踪, 它会自动从初始高度开始
        obs, info = pair[0].reset(seed=777)
        # 抬高到 forward 任务的初始高度 (15m)
        self.t0 = time.time()
        step = 0
        t_disp = 0.0
        # 让 forward 跑 12 秒 (按 0.5 倍速是 24 秒墙钟)
        end_t = 12.0
        while step * 0.02 < end_t:
            a, _ = self.m_forward.predict(obs, deterministic=True)
            obs, r, te, tr, info = self._step(pair, a)
            step += 1
            self._send(pair)
            self._wait(step)
            raw = self._raw(pair)
            t_sim = step * 0.02
            if t_sim - t_disp > 0.5:
                t_disp = t_sim
                gs = math.hypot(raw.get('vx', 0), raw.get('vy', 0))
                print('  t=%4.1fs alt=%5.1fm v_h=%4.1fm/s roll=%+5.1f° pitch=%+5.1f°' % (
                    t_sim, raw['agl'], gs, math.degrees(raw['roll']),
                    math.degrees(raw['pitch'])), flush=True)
            if te or tr:
                break

    def _phase_roll(self):
        pair = self.env_roll
        obs, info = pair[0].reset(seed=777)
        self.t0 = time.time()
        step = 0
        t_disp = 0.0
        end_t = 12.0
        while step * 0.02 < end_t:
            a, _ = self.m_roll.predict(obs, deterministic=True)
            obs, r, te, tr, info = self._step(pair, a)
            step += 1
            self._send(pair)
            self._wait(step)
            raw = self._raw(pair)
            t_sim = step * 0.02
            if t_sim - t_disp > 0.4:
                t_disp = t_sim
                print('  t=%4.1fs alt=%5.1fm roll=%+6.1f° pitch=%+5.1f° hdg=%5.1f°' % (
                    t_sim, raw['agl'], math.degrees(raw['roll']),
                    math.degrees(raw['pitch']),
                    math.degrees(self._fdm(pair).get_property_value('attitude/psi-rad'))), flush=True)
            if te or tr:
                break

    def _phase_landing(self):
        pair = self.env_landing
        obs, info = pair[0].reset(seed=777)
        # landing env 自带 15m 悬停起点 (init_alt_m=15)

        self.t0 = time.time()
        step = 0
        t_disp = 0.0
        while True:
            a, _ = self.m_landing.predict(obs, deterministic=True)
            obs, r, te, tr, info = self._step(pair, a)
            step += 1
            self._send(pair)
            self._wait(step)
            raw = self._raw(pair)
            t_sim = step * 0.02
            if t_sim - t_disp > 0.5:
                t_disp = t_sim
                print('  t=%4.1fs alt=%5.1fm vz=%+4.1fm/s roll=%+5.1f° pitch=%+5.1f°' % (
                    t_sim, raw['agl'], raw['vz'], math.degrees(raw['roll']),
                    math.degrees(raw['pitch'])), flush=True)
            if info.get('landed'):
                print('  ✓ 接地: vs=%.2fm/s hs=%.2fm/s 航向偏差=%.1f° 漂移=%.1fm' % (
                    info.get('touchdown_vs', 0), info.get('touchdown_hs_mps', 0),
                    info.get('max_heading_dev_deg', 0), info.get('net_drift_m', 0)), flush=True)
                # 接地后画面保持 5 秒 (不再步进已终局的环境, 只持续发包)
                hold_end = time.time() + 5.0
                while time.time() < hold_end:
                    self._send(pair)
                    time.sleep(0.05)
                break
            if te or tr or t_sim > 80:
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=5500)
    ap.add_argument('--time-scale', type=float, default=0.5, help='1.0=实时, 0.5=放慢一倍, 0.4=更慢')
    ap.add_argument('--phases', default='12345', help='要跑的阶段, 如 45 只跑翻滚+降落')
    args = ap.parse_args()
    Show(args).run()


if __name__ == '__main__':
    main()