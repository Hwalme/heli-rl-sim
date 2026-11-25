#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fly_path.py — 二代 helispike · 一条连续完成路径（白天场景 · 纯外环控制）

起飞 → 爬升 → 悬停 → 前飞 → 滚转 → 偏航转向 ×3 → 返航缓降 → 接地停稳
全程同一 env（不重置），外环吊车（姿态/高度/航向）直接驱动，策略模型不参与。
白天由 bat --time-of-day=12:00 控制。

用法（FG 先用 fg_shell_launch.bat 起，fdm=external）：
  python fly_path.py
"""
import argparse
import json
import math
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

import jsbsim

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs"
OUT.mkdir(exist_ok=True)
DT = 0.005
D2R = 57.29578
M2FT = 1.0 / 0.3048
# PHNL 檀香山机场跑道坐标（与 FG --airport=PHNL 同步）
# JSBSim reset00 的起点 = 这里相同的 lat/lon/alt，UDP 发绝对坐标即可不偏移。
PHNL_LAT, PHNL_LON, PHNL_ELEV_FT = 21.3187, -157.9224, 13.0
PACKET_FMT = "%.7f,%.7f,%.3f,%.3f,%.3f,%.3f,%.3f,%.1f"

# 外环符号（0.2 实测标定）
ROLL_SIGN = -1.0
PITCH_SIGN = +1.0
HDG_SIGN = +1.0

# 科目参数
R22_HOVER_COLL = 0.70          # 悬停总距基准（inverse mapping: 越小升力越大）
HOVER_ALT_FT = 60.0
HOVER_HOLD_S = 12.0
FWD_S = 15.0
ROLL_S = 8.0
YAW_LEG_S = 10.0
N_YAW_LEGS = 3


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def wrap180(x):
    return (x + 180.0) % 360.0 - 180.0


class NaNAbort(Exception):
    pass


class FlightPath:
    def __init__(self, args):
        self.args = args
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.fdm = jsbsim.FGFDMExec(jsbsim.get_default_root_dir(), None)
        if not self.fdm.load_model("helispike", True):
            raise RuntimeError("load_model helispike failed")
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.fdm.set_dt(DT)
        self.t = 0.0
        self.coll = 1.0          # 平桨（inverse mapping）
        self.coll_pi = 0.0
        self.hdg_ref = None
        self.heli_alt = 0.0
        self.agl_disagree = 0
        self.n_pkt = 0
        self.last_roll = self.last_pitch = self.last_hdg = None
        self.nan_t = None
        # 一阶低通初始化（send() 内首帧自初始化）
        self._send_last_t = None
        self._smooth_lat = self._smooth_lon = None
        self._smooth_alt = self._smooth_roll = None
        self._smooth_pitch = self._smooth_hdg = None
        # 启动：引擎+离合器+油门+AFCS
        self.fdm.set_property_value('propulsion/engine[0]/set-running', 1)
        self.fdm.set_property_value('propulsion/engine[1]/set-running', 1)
        self.fdm.set_property_value('propulsion/engine[0]/clutch', 1)
        self.fdm.set_property_value('fcs/throttle-cmd-norm', 1.0)
        self.fdm.set_property_value('fcs/hs/afcs-enable', 1.0)
        self.fdm.set_property_value('fcs/collective-cmd-norm', 0.0)
        # 记下 JSBSim reset00 的实际起点（helispike init 默认 lat≈21.449，与 PHNL 差 14km）。
        # 用此作偏移基准，让 UDP 包 = PHNL + (JSBSim 真值 - 此基准)，
        # 即 JSBSim 起点会被映射到 PHNL，跑道上一致。
        self.lat0_phys = self.fdm.get_property_value('position/lat-geod-deg') or 21.4494
        self.lon0_phys = self.fdm.get_property_value('position/long-gc-deg') or -157.9214
        print(f"[起点] JSBSim 起点 lat0={self.lat0_phys:.4f} lon0={self.lon0_phys:.4f} "
              f"(将映射到 PHNL {PHNL_LAT:.4f}, {PHNL_LON:.4f})")
        self.heli_alt = 0.0

    def _update_alt(self):
        """h-dot-fps 积分为主（探针验证过的口径）；agl-ft 真值可用且偏差小时优先。"""
        hdot = self.fdm.get_property_value('velocities/h-dot-fps') or 0.0
        self.heli_alt += hdot * DT
        raw = self.fdm.get_property_value('position/agl-ft')
        if raw is not None and math.isfinite(raw) and raw > 0.5:
            if abs(raw - self.heli_alt) < 30.0:
                self.heli_alt = raw
            else:
                self.agl_disagree += 1

    def read(self):
        f = self.fdm
        return {
            "t": round(self.t, 2),
            "agl_ft": round(self.heli_alt, 2),
            "vs_fpm": round((f.get_property_value('velocities/h-dot-fps') or 0) * 60.0, 1),
            "ve_kts": round(f.get_property_value('velocities/ve-kts') or 0, 1),
            "roll": round(f.get_property_value('attitude/phi-deg') or 0.0, 2),
            "pitch": round(f.get_property_value('attitude/theta-deg') or 0.0, 2),
            "hdg": round(f.get_property_value('attitude/psi-deg') or 0.0, 1),
            "coll": round(self.coll, 3),
            "rpm": round(f.get_property_value('propulsion/engine[0]/rotor-rpm') or 0, 0),
            "agl_raw": round(f.get_property_value('position/agl-ft') or -1.0, 2),
            "lat": f.get_property_value('position/lat-geod-deg'),
            "lon": f.get_property_value('position/long-gc-deg'),
        }

    def send(self, s):
        """JSBSim 真值 → FG 显示壳（fdm=external UDP 5500）。
        **关键修复**：fdm=external + 高频 UDP 包会让 FG 渲染线程的插值外推出大
        位移/姿态跳变（"飞机飞速移动+自旋+穿地"）。两处保险：
          1) 30Hz 节流（每帧 0.033s 发一次，匹配 FG ~30Hz 渲染频率）；
          2) 一阶低通 α=0.3（每帧只让 FG 看到 30% 新值），让 FG 有平滑时间。
        """
        # 节流: 30Hz
        if self._send_last_t is not None and (self.t - self._send_last_t) < (1.0 / 30.0):
            return
        self._send_last_t = self.t
        if s['lat'] is None or s['lon'] is None:
            return
        lat_d = PHNL_LAT + (s['lat'] - self.lat0_phys)
        lon_d = PHNL_LON + (s['lon'] - self.lon0_phys) / math.cos(math.radians(PHNL_LAT))
        alt_ft = PHNL_ELEV_FT + s['agl_ft'] * M2FT
        # 一阶低通 — 缓动到目标值的 30%/帧
        alpha = 0.30
        if self._smooth_lat is None:
            self._smooth_lat, self._smooth_lon = lat_d, lon_d
            self._smooth_alt, self._smooth_roll = alt_ft, s['roll']
            self._smooth_pitch, self._smooth_hdg = s['pitch'], s['hdg']
        else:
            self._smooth_lat = self._smooth_lat + alpha * (lat_d - self._smooth_lat)
            self._smooth_lon = self._smooth_lon + alpha * (lon_d - self._smooth_lon)
            self._smooth_alt = self._smooth_alt + alpha * (alt_ft - self._smooth_alt)
            self._smooth_roll = self._smooth_roll + alpha * self._wrap_diff(s['roll'] - self._smooth_roll)
            self._smooth_pitch = self._smooth_pitch + alpha * (s['pitch'] - self._smooth_pitch)
            self._smooth_hdg = self._smooth_hdg + alpha * self._wrap_diff(s['hdg'] - self._smooth_hdg)
        vals = (self._smooth_lat, self._smooth_lon, self._smooth_alt,
                self._smooth_roll, self._smooth_pitch, self._smooth_hdg,
                s['ve_kts'], 428.0)
        if any(not math.isfinite(v) for v in vals):
            return
        pkt = (PACKET_FMT % vals) + "\n"
        self.sock.sendto(pkt.encode("ascii"), (self.args.host, self.args.port))
        self.n_pkt += 1

    @staticmethod
    def _wrap_diff(d):
        """角度差分回卷到 [-180, 180]"""
        return ((d + 180.0) % 360.0) - 180.0

    def _set_view_chase(self):
        """切到 Chase View (view-number=2) 并拉到 -150m，让画面里背景变化可见
        （chase 默认 -25m 距离太近，看不出全局运动）。同时加 15m 仰角看下方地面。
        通过 telnet 5503 的 set 命令。
        """
        try:
            c = socket.socket(); c.settimeout(2); c.connect(('127.0.0.1', 5503))
            for cmd in [
                'set /sim/current-view/view-number 2',
                'set /sim/view[2]/config/z-offset-m -150',
                'set /sim/view[2]/config/y-offset-m 15',
                'set /sim/current-view/field-of-view 75',
            ]:
                c.sendall((cmd + '\r\n').encode())
                time.sleep(0.25)
                c.recv(256)
            c.close()
        except Exception as e:
            print(f"[视图切换失败] {e}")

    def tick(self, cmd_roll=0.0, cmd_pitch=0.0, target_coll=None, target_alt=None):
        """单步外环：姿态 P + 航向 P + 可选高度 PI 或固定总距；顺带 UDP 发包"""
        if target_alt is not None:
            vs_fpm = (self.fdm.get_property_value('velocities/h-dot-fps') or 0.0) * 60.0
            err = clamp((target_alt - self.heli_alt) * 0.3 - vs_fpm * 0.1, -3.0, 3.0)
            self.coll_pi = clamp(self.coll_pi + 0.00004 * err * DT * 200.0, -0.08, 0.08)
            self.coll = clamp(R22_HOVER_COLL + self.coll_pi, 0.55, 0.85)
        elif target_coll is not None:
            self.coll = target_coll
        f = self.fdm
        roll = f.get_property_value('attitude/phi-deg') or 0.0
        pitch = f.get_property_value('attitude/theta-deg') or 0.0
        hdg = f.get_property_value('attitude/psi-deg') or 0.0
        if not (math.isfinite(roll) and math.isfinite(pitch)
                and math.isfinite(hdg) and math.isfinite(self.heli_alt)):
            if self.nan_t is None:
                self.nan_t = self.t
                raise NaNAbort(f"NaN 熔断 t={self.t:.1f}s coll={self.coll:.3f}")
        # 本构建 p/q/r-radps 属性是死的 → 姿态角速度用差分估计（200Hz，无噪声）
        roll_rate = (roll - self.last_roll) / DT if self.last_roll is not None else 0.0
        pitch_rate = (pitch - self.last_pitch) / DT if self.last_pitch is not None else 0.0
        hdg_rate = wrap180(hdg - self.last_hdg) / DT if self.last_hdg is not None else 0.0
        self.last_roll, self.last_pitch, self.last_hdg = roll, pitch, hdg
        ail = clamp(ROLL_SIGN * (0.05 * (roll - cmd_roll) + 0.010 * roll_rate), -0.30, 0.30)
        ele = clamp(PITCH_SIGN * (0.05 * (pitch - cmd_pitch) + 0.012 * pitch_rate), -0.30, 0.30)
        if self.hdg_ref is not None:
            rud = clamp(HDG_SIGN * (0.08 * wrap180(hdg - self.hdg_ref) + 0.005 * hdg_rate),
                        -0.60, 0.60)
        else:
            rud = 0.0
        f.set_property_value('fcs/collective-cmd-norm', self.coll)
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.set_property_value('fcs/rudder-cmd-norm', rud)
        self._update_alt()
        f.run()
        self.t += DT
        if int(self.t / DT) % 2 == 0:          # 100Hz UDP
            self.send(self.read())

    def run(self):
        target_alt = HOVER_ALT_FT
        self.hdg_ref = self.read()['hdg']
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.frames = []
        self.ok = False

        def snap(phase):
            s = self.read()
            s['phase'] = phase
            self.frames.append(s)
            return s

        # ---- 阶段 0: 桨叶起转 ----
        print("[spool] 总距 0 等转速 ...")
        spool_t0 = self.t
        while self.t - spool_t0 < 40.0:
            self.tick(target_coll=0.0)
            if int(self.t / DT) % 200 == 0:
                s = snap('spool')
                print(f"  t={self.t:5.1f}s rpm={s['rpm']:.0f}")
                if s['rpm'] > 460:
                    break
        s = snap('spool')
        print(f"  起转完成 rpm={s['rpm']:.0f}")

        # 切换到 Chase View（第三人称尾追），让画面变化可见
        self._set_view_chase()

        # ---- 阶段 1: 垂直起飞爬升到 60ft ----
        # 总距映射实测为标准方向（值越大升力越大，0.2-rc1 探针与 run2 数据双确认）
        print("[1] 垂直起飞爬升 ...")
        self.coll = 0.30
        t_guard = self.t + 120.0
        while self.heli_alt < 10.0 and self.t < t_guard:
            self.coll = min(self.coll + 0.04 * DT, 0.80)
            self.tick(target_coll=self.coll)
            if int(self.t / DT) % 200 == 0:
                snap('climb')
                print(f"  coll={self.coll:.3f} alt={self.heli_alt:5.1f}ft "
                      f"vs={(self.fdm.get_property_value('velocities/h-dot-fps') or 0)*60:+5.0f}fpm")
        while self.heli_alt < HOVER_ALT_FT and self.t < t_guard + 90.0:
            self.tick(target_alt=target_alt)
            if int(self.t / DT) % 200 == 0:
                snap('climb')
                print(f"  alt={self.heli_alt:5.1f}ft "
                      f"vs={(self.fdm.get_property_value('velocities/h-dot-fps') or 0)*60:+5.0f}fpm")
        print(f"  爬升完成 alt={self.heli_alt:.1f}ft")

        # ---- 阶段 2: 悬停保持 ----
        print(f"[2] 悬停 {HOVER_HOLD_S:.0f}s ...")
        t0 = self.t
        while self.t - t0 < HOVER_HOLD_S:
            self.tick(target_alt=target_alt)
            if int(self.t / DT) % 200 == 0:
                s = snap('hover')
                print(f"  alt={self.heli_alt:5.1f}ft roll={s['roll']:+4.1f} "
                      f"pitch={s['pitch']:+4.1f} coll={s['coll']:.3f}")

        # ---- 阶段 3: 前飞（3s 缓低头至 -6°，防姿态瞬态）----
        print(f"[3] 前飞 {FWD_S:.0f}s（pitch_cmd 0→-6°）...")
        t0 = self.t
        while self.t - t0 < FWD_S:
            k = min((self.t - t0) / 3.0, 1.0)
            self.tick(cmd_pitch=-6.0 * k, target_alt=target_alt)
            if int(self.t / DT) % 200 == 0:
                s = snap('forward')
                print(f"  alt={self.heli_alt:5.1f}ft pitch={s['pitch']:+5.1f} ve={s['ve_kts']:4.1f}kt")

        # ---- 阶段 4: 滚转（左→右）----
        print(f"[4] 滚转 {ROLL_S:.0f}s（左 5° → 右 5°）...")
        t0 = self.t
        while self.t - t0 < ROLL_S / 2:
            self.tick(cmd_roll=-5.0, target_alt=target_alt)
            if int(self.t / DT) % 200 == 0:
                s = snap('roll-L')
                print(f"  roll={s['roll']:+5.1f}° alt={self.heli_alt:5.1f}ft")
        t0 = self.t
        while self.t - t0 < ROLL_S / 2:
            self.tick(cmd_roll=+5.0, target_alt=target_alt)
            if int(self.t / DT) % 200 == 0:
                s = snap('roll-R')
                print(f"  roll={s['roll']:+5.1f}° alt={self.heli_alt:5.1f}ft")

        # ---- 阶段 5: 偏航转向 ×3（方形返航）----
        print(f"[5] 偏航转向 ×{N_YAW_LEGS}（方形返航）...")
        for leg in range(N_YAW_LEGS):
            self.hdg_ref = wrap180(self.hdg_ref - 90.0)
            print(f"  转向 → hdg_ref={self.hdg_ref:.0f}°")
            t0 = self.t
            while self.t - t0 < YAW_LEG_S:
                self.tick(target_alt=target_alt)
                if int(self.t / DT) % 200 == 0:
                    s = snap(f'yaw-{leg}')
                    hdg_err = wrap180(s['hdg'] - self.hdg_ref)
                    print(f"  hdg={s['hdg']:5.1f}° (err={hdg_err:+5.1f}°) alt={self.heli_alt:4.0f}ft")

        # ---- 阶段 6: 返航缓降（减总距 = 减升力；带沉降率保护）----
        print("[6] 返航缓降 ...")
        t_guard = self.t + 150.0
        while self.heli_alt > 8.0 and self.t < t_guard:
            vs_fpm = (self.fdm.get_property_value('velocities/h-dot-fps') or 0.0) * 60.0
            if vs_fpm < -700:
                self.coll = clamp(self.coll + 0.01 * DT, 0.35, 0.85)   # 沉降过大，提总距
            else:
                self.coll = clamp(self.coll - 0.004 * DT, 0.35, 0.85)
            self.tick(target_coll=self.coll)
            if int(self.t / DT) % 200 == 0:
                s = snap('descend')
                print(f"  alt={self.heli_alt:5.1f}ft vs={s['vs_fpm']:+5.0f}fpm coll={s['coll']:.3f}")
        print("[6] 末段缓降 → 接地 ...")
        touchdown = None
        t_guard = self.t + 150.0
        while self.t < t_guard:
            vs_fpm = (self.fdm.get_property_value('velocities/h-dot-fps') or 0.0) * 60.0
            if vs_fpm < -400:
                self.coll = clamp(self.coll + 0.01 * DT, 0.35, 0.85)
            else:
                self.coll = clamp(self.coll - 0.020 * DT, 0.35, 0.85)
            self.tick(target_coll=self.coll)
            if int(self.t / DT) % 200 == 0:
                s = snap('touchdown')
                print(f"  alt={self.heli_alt:5.2f}ft vs={s['vs_fpm']:+5.0f}fpm coll={s['coll']:.3f}")
            if self.heli_alt <= 0.30 or (self.heli_alt < 5.0 and s['vs_fpm'] > -60):
                touchdown = s
                break

        # ---- 阶段 7: 接地平桨（coll=0）+ 地面锁杆 ----
        print("[7] 接地平桨 + 地面锁杆 ...")
        self.coll = 0.0
        t0 = self.t
        while self.t - t0 < 6.0:
            f = self.fdm
            f.set_property_value('fcs/collective-cmd-norm', 0.0)
            f.set_property_value('fcs/aileron-cmd-norm', 0.0)
            f.set_property_value('fcs/elevator-cmd-norm', 0.0)
            f.set_property_value('fcs/rudder-cmd-norm', 0.0)
            self._update_alt()
            f.run()
            self.t += DT
            if int(self.t / DT) % 2 == 0:
                self.send(self.read())
        self.ok = True
        self.finalize(touchdown)
        return 0

    def finalize(self, touchdown=None, err=None):
        s = self.frames[-1] if self.frames else self.read()
        lat, lon = s.get('lat'), s.get('lon')
        drift = 0.0
        if lat is not None and lon is not None and math.isfinite(lat) and math.isfinite(lon):
            dn = (lat - self.lat0_phys) * 111320.0
            de = (lon - self.lon0_phys) * 111320.0 * math.cos(math.radians(self.lat0_phys))
            drift = math.hypot(dn, de)
        if self.ok:
            print(f"[完成] 接地 vs={touchdown['vs_fpm'] if touchdown else 'n/a'} "
                  f"末态 alt={self.heli_alt:.2f}ft roll={s['roll']:+.1f} "
                  f"pitch={s['pitch']:+.1f} hdg={s['hdg']:.1f} 净漂移={drift:.1f}m "
                  f"UDP包={self.n_pkt}")
        else:
            print(f"[失败] {err or ''} 末帧 alt={s.get('agl_ft')} roll={s.get('roll')} "
                  f"pitch={s.get('pitch')} 净漂移={drift:.1f}m UDP包={self.n_pkt}")

        out = OUT / f"fly_path_{self.stamp}.json"
        out.write_text(json.dumps({
            "stamp": self.stamp, "ok": self.ok, "nan_t": self.nan_t,
            "total_s": round(self.t, 1), "udp_pkts": self.n_pkt,
            "net_drift_m": round(drift, 1), "agl_disagree": self.agl_disagree,
            "touchdown_vs_fpm": touchdown['vs_fpm'] if touchdown else None,
            "frames": self.frames,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[产物]", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=5500)
    args = ap.parse_args()
    fp = FlightPath(args)
    try:
        return fp.run()
    except NaNAbort as e:
        fp.ok = False
        print("[失败]", e)
        fp.finalize()
        return 2


if __name__ == "__main__":
    sys.exit(main())
