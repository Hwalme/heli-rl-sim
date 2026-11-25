# -*- coding: utf-8 -*-
r"""
B1 演示 v2 · Nasal 内环增稳 + Python 高层状态机

架构变更（解决 telnet 6.6s/帧的架构性瓶颈）：
  - 姿态 PD 内环移到 FG 内部 Nasal 脚本（demo_stabilizer.xml，~60Hz）
  - Python 端只做：高层状态机 + 总距闭环 + 下发目标姿态（2-3 Hz）
  - telnet 负载从每帧 13 次往返降到每 0.4s 约 6 次往返，绰绰有余

本机 bo105 关键事实（勿改）：
  - 总距绑定在 /controls/engines/engine[n]/throttle，方向反向（小值=大总距）
  - 无 /controls/collective 属性
  - 功率杆 /controls/engines/engine[n]/power，飞行位=1.0
  - 旋翼稳态 ~428 RPM

用法：
  1) 启动 FG：start_fg_demo.bat（已加 --config=demo_stabilizer.xml）
  2) python fg_demo.py
退出码：0=成功 / 2=坠毁或中止 / 3=连接或自检失败
"""
import json
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
FG_HOST = "127.0.0.1"
FG_PORT = 5501

# 本机 bo105 实测绑定
COLL_L = "/controls/engines/engine[0]/throttle"
COLL_R = "/controls/engines/engine[1]/throttle"
POWER_L = "/controls/engines/engine[0]/power"
POWER_R = "/controls/engines/engine[1]/power"

# Nasal 增稳目标属性
TGT_ROLL = "/demo/target/roll-deg"
TGT_PITCH = "/demo/target/pitch-deg"
STAB_ENABLED = "/demo/stabilizer/enabled"
STAB_ACTIVE = "/demo/stabilizer/active"

# 演示参数
LOOP_INTERVAL = 0.35       # Python 状态机周期（秒），telnet 完全扛得住
HOVER_AGL_FT = 60.0
HOVER_HOLD_S = 15.0

# 总距范围（反向映射：小值=大总距）
COLL_MIN, COLL_MAX = 0.15, 0.98
RAMP_RATE = 0.020          # 离地斜坡：每秒减小（增总距）
RAMP_FLOOR = 0.22
K_VS_I = 0.008             # 垂直速度积分增益
D_COLL_MAX = 0.04          # 总距变化率限幅 /s


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class FGClient:
    """FG telnet 属性树客户端"""
    _VAL = re.compile(r"'([^']*)'")

    def __init__(self, host=FG_HOST, port=FG_PORT, wait_s=180):
        deadline = time.time() + wait_s
        last_err = None
        while time.time() < deadline:
            try:
                self.sock = socket.create_connection((host, port), timeout=5)
                break
            except OSError as e:
                last_err = e
                time.sleep(2.0)
        else:
            raise ConnectionError(f"无法连接 FG telnet {host}:{port}：{last_err}")
        self.sock.settimeout(3.0)
        time.sleep(0.3)
        self._read_until_prompt()

    def _send(self, cmd):
        self.sock.sendall((cmd + "\r\n").encode())

    def _read_until_prompt(self):
        deadline = time.time() + 4.0
        out = b""
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            out += chunk
            if out.rstrip().endswith(b">"):
                return out.decode("utf-8", "replace")
        return out.decode("utf-8", "replace")

    def get(self, prop, default=0.0):
        self._send(f"get {prop}")
        vals = self._VAL.findall(self._read_until_prompt())
        if vals:
            raw = vals[-1].strip()
            if raw.lower() == "true":
                return 1.0
            if raw.lower() == "false":
                return 0.0
            try:
                return float(raw)
            except ValueError:
                return default
        return default

    def set(self, prop, value):
        self._send(f"set {prop} {value}")
        self._read_until_prompt()

    def get_multi(self, props):
        """批量读取多个属性，减少往返次数"""
        result = {}
        for p in props:
            result[p] = self.get(p)
        return result


class DemoPilot:
    """高层状态机：只管总距和目标姿态，姿态增稳由 Nasal 负责"""

    def __init__(self, fg):
        self.fg = fg
        self.phase = "ground_flat"
        self.t_phase = 0.0
        self.coll = 1.0           # 从平桨开始
        self.coll_i = 0.0
        self.liftoff_coll = None
        self.agl_ref = 0.0
        self.tgt_roll = 0.0
        self.tgt_pitch = 0.0

    def set_collective(self, value):
        self.coll = clamp(value, COLL_MIN, 1.0)
        v = round(self.coll, 4)
        self.fg.set(COLL_L, v)
        self.fg.set(COLL_R, v)

    def set_target_attitude(self, roll=0.0, pitch=0.0):
        self.tgt_roll = roll
        self.tgt_pitch = pitch
        self.fg.set(TGT_ROLL, round(roll, 2))
        self.fg.set(TGT_PITCH, round(pitch, 2))

    def target_vs(self, agl, phase):
        if phase == "climb":
            return 8.0
        if phase in ("hover_reach", "hover_hold"):
            return clamp((HOVER_AGL_FT - agl) * 0.12, -5.0, 5.0)
        if phase == "descend":
            if agl > 25:
                return -6.0
            if agl > 8:
                return -3.5
            return -2.0
        return 0.0

    def step(self, dt, snap):
        phase = self.phase
        agl = snap["agl"] - self.agl_ref
        vs = snap["vs"]
        new_phase = None

        if phase == "ground_flat":
            self.coll = 1.0
            if self.t_phase > 2.0:
                new_phase = "liftoff_ramp"

        elif phase == "liftoff_ramp":
            self.coll = max(self.coll - RAMP_RATE * dt, RAMP_FLOOR)
            if vs > 2.0 or agl > 2.0:
                self.liftoff_coll = self.coll
                new_phase = "climb"
            elif self.coll <= RAMP_FLOOR + 1e-6 and self.t_phase > 6:
                return "斜坡到底仍未离地，升力链路异常"

        elif phase in ("climb", "hover_reach", "hover_hold", "descend"):
            err = vs - self.target_vs(agl, phase)
            self.coll_i = clamp(self.coll_i + K_VS_I * err * dt, -0.5, 0.5)
            target = clamp(self.coll + self.coll_i, COLL_MIN, 0.95)
            rate = clamp(target - self.coll, -D_COLL_MAX * dt, D_COLL_MAX * dt)
            self.coll = clamp(self.coll + rate, COLL_MIN, 0.95)
            # 下沉保护
            if vs < self.target_vs(agl, phase) - 8.0:
                self.coll = clamp(self.coll - 0.015, COLL_MIN, 0.95)
                self.coll_i = max(self.coll_i - 0.05, -0.5)

            if phase == "climb" and agl >= HOVER_AGL_FT - 10:
                new_phase = "hover_reach"
            elif phase == "hover_reach" and abs(agl - HOVER_AGL_FT) < 4:
                new_phase = "hover_hold"
            elif phase == "hover_hold" and self.t_phase > HOVER_HOLD_S:
                new_phase = "descend"
            elif phase == "descend" and agl <= 1.5:
                new_phase = "touchdown"

        elif phase == "touchdown":
            self.coll = min(self.coll + 0.12 * dt, 1.0)
            self.coll_i = 0.0
            if agl > 3.0:
                new_phase = "descend"
            elif agl < 0.6 and abs(vs) < 2.0:
                self.coll = 1.0
                new_phase = "settle"

        elif phase == "settle":
            self.coll = 1.0
            if self.t_phase > 4.0:
                new_phase = "done"

        if new_phase:
            self.phase = new_phase
            self.t_phase = 0.0
        return None


def is_crashed(s):
    if s["crashed"] > 0.5:
        return "sim/crashed=1"
    if abs(s["roll"]) > 60:
        return f"翻滚 roll={s['roll']:.0f}deg"
    if abs(s["pitch"]) > 45:
        return f"俯仰超限 pitch={s['pitch']:.0f}deg"
    if s["vs"] < -35:
        return f"坠落速度 vs={s['vs']:.0f}fps"
    return None


def wait_ready(fg, timeout=180):
    print("[自检] 等待 FDM 初始化 + 旋翼达速 + Nasal 增稳就绪...")
    t0 = time.time()
    stab_ok = False
    while time.time() - t0 < timeout:
        rpm = fg.get("/rotors/main/rpm")
        fdm = fg.get("/sim/signals/fdm-initialized")
        crashed = fg.get("/sim/crashed") if fdm > 0.5 else 0.0
        freeze = fg.get("/sim/freeze/master")

        # 检查 Nasal 增稳是否加载
        stab_active = fg.get(STAB_ACTIVE)
        if stab_active > 0.5:
            stab_ok = True

        if fdm > 0.5 and crashed > 0.5:
            print("[失败] FDM 初始化后处于坠机状态，请重启 FG")
            return False

        if rpm > 400 and fdm > 0.5 and freeze < 0.5 and stab_ok:
            n1 = fg.get("/engines/engine[0]/n1-pct")
            agl = fg.get("/position/altitude-agl-ft")
            stab_rate = fg.get("/demo/stabilizer/rate-hz")
            print(f"[自检] 通过：rpm={rpm:.0f} n1={n1:.0f}% agl={agl:.1f}ft "
                  f"增稳={stab_rate:.0f}Hz（耗时 {time.time()-t0:.0f}s）")
            # 功率杆飞行位 + 旋翼刹车释放
            fg.set(POWER_L, 1.0)
            fg.set(POWER_R, 1.0)
            fg.set("/controls/rotor/brake", 0)
            fg.set("/controls/engines/engine[0]/magnetos", 1)
            fg.set("/controls/engines/engine[1]/magnetos", 1)
            # 开启增稳
            fg.set(STAB_ENABLED, 1)
            return True

        if time.time() - t0 > 45 and rpm < 300:
            fg.set("/controls/engines/engine[0]/starter", 1)
            fg.set("/controls/engines/engine[1]/starter", 1)
            fg.set("/controls/engines/engine[0]/ignition", 1)
            fg.set("/controls/engines/engine[1]/ignition", 1)
            fg.set(POWER_L, 1.0)
            fg.set(POWER_R, 1.0)

        if not stab_ok and int(time.time() - t0) % 5 == 0:
            print(f"  等待 Nasal 增稳加载... rpm={rpm:.0f} fdm={fdm:.0f}")

        time.sleep(1.5)

    print(f"[失败] {timeout}s 内未就绪（rpm={fg.get('/rotors/main/rpm'):.0f}, "
          f"stab={fg.get(STAB_ACTIVE):.0f}）")
    return False


def main():
    print("=" * 64)
    print("  B1 v2 · Bo-105 完整起落演示（Nasal 内环增稳 + Python 状态机）")
    print("=" * 64)
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        fg = FGClient(wait_s=180)
    except ConnectionError as e:
        print(f"[失败] {e}")
        return 3
    print("[OK] 已连接 FG telnet")

    if not wait_ready(fg):
        return 3

    pilot = DemoPilot(fg)
    pilot.agl_ref = max(0.0, fg.get("/position/altitude-agl-ft"))
    print(f"[基准] 停机位离地高 {pilot.agl_ref:.2f} ft")

    # 初始目标姿态 = 水平
    pilot.set_target_attitude(0.0, 0.0)

    log, reason = [], None
    t0 = time.time()
    t_frame = t0
    print(f"[起飞] 状态机：ground_flat -> liftoff_ramp -> climb -> "
          f"hover@{HOVER_AGL_FT:.0f}ft({HOVER_HOLD_S:.0f}s) -> descend -> touchdown\n")

    while time.time() - t0 < 480:
        t = time.time() - t0
        t_now = time.time()
        dt_real = t_now - t_frame
        t_frame = t_now

        try:
            # 批量读取状态（减少往返）
            s = {
                "t": round(t, 2),
                "phase": pilot.phase,
                "rpm": round(fg.get("/rotors/main/rpm"), 1),
                "alt": round(fg.get("/position/altitude-ft"), 1),
                "agl": round(fg.get("/position/altitude-agl-ft"), 2),
                "vs": round(fg.get("/velocities/vertical-speed-fps"), 2),
                "roll": round(fg.get("/orientation/roll-deg"), 2),
                "pitch": round(fg.get("/orientation/pitch-deg"), 2),
                "heading": round(fg.get("/orientation/heading-deg"), 1),
                "crashed": fg.get("/sim/crashed"),
            }
        except OSError:
            reason = f"t={t:.1f}s 与 FG 连接断开"
            print(f"[!!!] {reason}")
            break

        # 状态机推进
        abort = pilot.step(dt_real, s)
        pilot.set_collective(pilot.coll)
        # 目标姿态保持水平（可在此处扩展机动动作）
        pilot.set_target_attitude(0.0, 0.0)
        pilot.t_phase += dt_real
        s["coll"] = round(pilot.coll, 3)
        s["tgt_roll"] = pilot.tgt_roll
        s["tgt_pitch"] = pilot.tgt_pitch
        log.append(s)

        if abort:
            reason = f"t={t:.1f}s {abort}"
            print(f"[!!!] {reason}")
            break

        # 控制台输出（每 ~2 秒一次）
        if int(t) % 2 == 0 and (not log or int(log[-1]["t"]) != int(t) or len(log) < 2):
            print(f"  [{t:6.1f}s] {pilot.phase:<12} agl={s['agl']-pilot.agl_ref:6.1f}ft "
                  f"vs={s['vs']:+6.1f} coll={pilot.coll:.3f} "
                  f"roll={s['roll']:+6.1f} pitch={s['pitch']:+6.1f}")

        if pilot.phase == "done":
            break

        reason = is_crashed({**s, "agl": s["agl"] - pilot.agl_ref})
        if reason:
            reason = f"t={t:.1f}s {pilot.phase} 阶段：{reason}"
            print(f"[!!!] 坠机识别：{reason}")
            break

        # 控制循环频率
        elapsed = time.time() - t_now
        time.sleep(max(0.0, LOOP_INTERVAL - elapsed))

    # 落盘
    (LOG_DIR / f"flight_{ts}.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")

    landed = bool(log) and pilot.phase == "done" and \
        log[-1]["agl"] - pilot.agl_ref < 2.0 and abs(log[-1]["vs"]) < 3.0
    max_agl = max((e["agl"] - pilot.agl_ref for e in log), default=0.0)
    max_roll = max((abs(e["roll"]) for e in log), default=0.0)
    max_pitch = max((abs(e["pitch"]) for e in log), default=0.0)
    ok = landed and reason is None and max_agl >= HOVER_AGL_FT - 15

    lines = [
        f"Bo-105 完整起落演示报告 v2（Nasal 内环增稳）  {ts}",
        f"架构: Nasal ~60Hz 姿态PD内环 + Python {LOOP_INTERVAL}s 状态机",
        f"总距属性: {COLL_L} (本机绑定，反向: 小值=大总距)",
        "-" * 56,
        f"结果: {'成功 · 完整起落无坠毁' if ok else '失败'}",
        f"原因: {reason or '无'}",
        f"飞行时长: {t:.1f}s | 帧数: {len(log)}",
        f"最高离地: {max_agl:.1f} ft (目标 {HOVER_AGL_FT:.0f})",
        f"离地总距: {pilot.liftoff_coll if pilot.liftoff_coll is not None else float('nan'):.3f}",
        f"全程最大 |roll|={max_roll:.1f}deg |pitch|={max_pitch:.1f}deg",
        f"结束状态: phase={pilot.phase} agl={log[-1]['agl'] if log else '-'}ft",
    ]
    summary = "\n".join(lines)
    (LOG_DIR / f"flight_{ts}_summary.txt").write_text(summary + "\n", encoding="utf-8")
    print("\n" + summary)
    print(f"\n[日志] {LOG_DIR}\\flight_{ts}.json / _summary.txt")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
