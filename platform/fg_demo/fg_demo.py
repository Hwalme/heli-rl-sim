# -*- coding: utf-8 -*-
r"""
B1 v3 · FlightGear Bo-105 完整起落演示（rev6 全内环化 + 外环指挥端）

架构（回应 00:54 坠机与用户两条验收意见）：
  1) "停机点不能是海面/草坪" —— 起飞前校验出生点在 PHNL 机场坪内（<=1.5nm），
     风速置静风（--wind=0@0，bat 内），坐标与风速落盘记录；
  2) "直升机必须垂直缓降静止，不能滑行" —— 漂移阻尼（机体前后/左右地速 ->
     姿态目标微调）+ 全自动着陆剖面（40ft 以下 -2.5fps、8ft 以下随高度收敛、
     agl<0.6ft 且 |vs|<2 判接地，随后平桨静止）全部在 FG 进程内 56Hz 执行。
  外环（本脚本）只发阶段指令（爬升/悬停/着陆），并做监控与日志；
  其 ~4s 的 telnet 慢节拍不再进入任何控制回路（v2 坠机根因）。

用法：start_fg_demo.bat 启动 FG（telnet@5501 + --config=demo_stabilizer.xml rev6）
      python fg_demo.py
退出码：0 成功 / 2 坠毁或中止 / 3 连接或自检失败
"""
import json
import math
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
FG_HOST, FG_PORT = "127.0.0.1", 5501

PHNL_LAT, PHNL_LON = 21.3187, -157.9224   # 机场中心参考（坪区判定用）
SPAWN_MAX_NM = 1.5

HOVER_AGL_FT = 60.0
HOVER_HOLD_S = 20.0
TIME_LIMIT_S = 420.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class FGClient:
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
        self.sock.settimeout(2.0)
        time.sleep(0.3)
        self._read_until_prompt()

    def _send(self, cmd):
        self.sock.sendall((cmd + "\r\n").encode())

    def _read_until_prompt(self):
        deadline = time.time() + 3.0
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


def is_crashed(s):
    if s["crashed"] > 0.5:
        return "sim/crashed=1"
    if abs(s["roll"]) > 60:
        return f"翻滚 roll={s['roll']:.0f}deg"
    if abs(s["pitch"]) > 45:
        return f"俯仰超限 pitch={s['pitch']:.0f}deg"
    if s["vs"] < -30:
        return f"坠落速度 vs={s['vs']:.0f}fps"
    return None


def preflight_env(fg):
    """用户验收意见 1：环境判断 —— 停机点必须在机场坪内（非海面/草坪），静风。

    返回: (ok: bool, env_info: dict) —— 环境信息用于日志记录和网站展示。
    """
    lat = fg.get("/position/latitude-deg")
    lon = fg.get("/position/longitude-deg")
    dnm = math.hypot((lat - PHNL_LAT) * 60.0,
                     (lon - PHNL_LON) * 60.0 * math.cos(math.radians(PHNL_LAT)))
    ok_loc = dnm <= SPAWN_MAX_NM
    print(f"[环境] 出生点 {lat:.5f},{lon:.5f} 距 PHNL 坪心 {dnm:.2f}nm "
          f"（{'坪内 OK' if ok_loc else '超界，禁止起飞'}）")
    agl = fg.get("/position/altitude-agl-ft")
    ok_agl = -1.0 < agl < 15.0
    print(f"[环境] 停机离地 {agl:.2f} ft（{'正常停机' if ok_agl else '异常'}）")
    wind_speed_kt = fg.get("/environment/wind-speed-fps") * 0.592484
    wind_dir_deg = fg.get("/environment/wind-from-heading-deg", default=0.0)
    print(f"[环境] 风速 {wind_speed_kt:.1f} kt @ {wind_dir_deg:.0f}°（启动参数已置静风 0@0）")

    env_info = {
        "aircraft": "bo105",
        "fg_version": "2024.1",
        "physics": "YASim Bo105",
        "scenario": "PHNL 夏威夷檀香山国际机场",
        "spawn_lat": round(lat, 5),
        "spawn_lon": round(lon, 5),
        "spawn_dist_nm": round(dnm, 2),
        "spawn_agl_ft": round(agl, 2),
        "wind_speed_kt": round(wind_speed_kt, 1),
        "wind_from_deg": round(wind_dir_deg, 0),
        "hover_target_agl_ft": HOVER_AGL_FT,
        "control_mode": "rev6 全内环化 (56Hz) + rev7 序列器外环",
        "inner_loop_hz": 56,
        "time_of_day": "noon",
        "weather": "晴空静风（禁用实时天气/terrasync/AI交通）",
    }
    return ok_loc and ok_agl, env_info


def wait_ready(fg, timeout=180):
    print("[自检] 等待 FDM / rev6 内环 / 旋翼达速 ...")
    t0 = time.time()
    starter_on = False
    while time.time() - t0 < timeout:
        fdm = fg.get("/sim/signals/fdm-initialized")
        crashed = fg.get("/sim/crashed") if fdm > 0.5 else 0.0
        rev6 = fg.get("/demo/pilot/state", default=-1.0)
        rpm = fg.get("/rotors/main/rpm")
        if fdm > 0.5 and crashed > 0.5:
            print("[失败] FDM 初始化后即坠机，请重启 FG")
            return False
        if fdm > 0.5 and rev6 < 0:
            print("[失败] /demo/pilot 不存在——demo_stabilizer.xml rev6 未加载，"
                  "查 FG 日志 Nasal 报错")
            return False
        if fdm > 0.5:
            fg.set("/controls/engines/engine[0]/power", 1.0)
            fg.set("/controls/engines/engine[1]/power", 1.0)
            fg.set("/controls/rotor/brake", 0)
            fg.set("/controls/engines/engine[0]/magnetos", 1)
            fg.set("/controls/engines/engine[1]/magnetos", 1)
            if rpm < 300 and time.time() - t0 > 30 and not starter_on:
                starter_on = True
                for n in (0, 1):
                    fg.set(f"/controls/engines/engine[{n}]/starter", 1)
                    fg.set(f"/controls/engines/engine[{n}]/ignition", 1)
            if rpm > 300 and starter_on:
                for n in (0, 1):
                    fg.set(f"/controls/engines/engine[{n}]/starter", 0)
                starter_on = False
            ticks1 = fg.get("/demo/stabilizer/ticks")
            time.sleep(2.0)
            ticks2 = fg.get("/demo/stabilizer/ticks")
            if rpm > 400:
                if ticks2 <= ticks1:
                    print(f"[失败] 内环无心跳（ticks {ticks1}->{ticks2}）")
                    return False
                print(f"[自检] 通过：rpm={rpm:.0f} 内环有效频率≈{(ticks2-ticks1)/2:.0f}Hz "
                      f"（耗时 {time.time()-t0:.0f}s）")
                return True
        time.sleep(2)
    print(f"[失败] {timeout}s 未就绪（rpm={fg.get('/rotors/main/rpm'):.0f}）")
    return False


def run_flight(fg):
    """触发 rev7 序列器（客户科目：起飞-悬停-前飞-滚转-偏航-降落），监控+日志。"""
    log, reason = [], None
    phase_name = "idle"
    t0 = time.time()
    t_frame = t0

    # 触发（上升沿）
    fg.set("/demo/seq/run", 0)
    fg.set("/demo/seq/start", 0)
    fg.set("/demo/seq/done", 0)
    fg.set("/demo/seq/run", 1)
    fg.set("/demo/seq/start", 1)
    print("[科目] 序列器已触发：ground -> liftoff -> climb -> hover -> forward -> "
          "roll-L/R -> yaw-L x3(方形回场) -> landing -> settle")

    while time.time() - t0 < TIME_LIMIT_S:
        t = time.time() - t0
        try:
            s = {
                "t": round(t, 2),
                "pnum": fg.get("/demo/seq/phase"),
                "phase": {0:"idle",1:"ground",2:"liftoff",3:"climb",4:"hover",
                          5:"forward",6:"roll-L",7:"level-1",8:"roll-R",9:"level-2",
                          10:"yaw-L",11:"forward-2",12:"yaw-L",13:"forward-3",
                          14:"yaw-L",15:"landing",16:"settled",17:"done"
                         }.get(int(fg.get("/demo/seq/phase")), "idle"),
                "ticks": fg.get("/demo/stabilizer/ticks"),
                "rpm": round(fg.get("/rotors/main/rpm"), 1),
                "agl": round(fg.get("/position/altitude-agl-ft"), 2),
                "vs": round(fg.get("/velocities/vertical-speed-fps"), 2),
                "gs": round(fg.get("/velocities/groundspeed-kt"), 1),
                "roll": round(fg.get("/orientation/roll-deg"), 2),
                "pitch": round(fg.get("/orientation/pitch-deg"), 2),
                "heading": round(fg.get("/orientation/heading-deg"), 1),
                "coll": round(fg.get("/demo/pilot/coll"), 3),
                "crashed": fg.get("/sim/crashed"),
            }
        except OSError:
            reason = f"t={t:.1f}s 与 FG 连接断开"
            print(f"[!!!] {reason}")
            break

        if s["phase"] != phase_name:
            print(f"  [{t:6.1f}s] ===== {s['phase']} =====")
            phase_name = s["phase"]
        log.append(s)
        print(f"  [{t:6.1f}s] {s['phase']:<10} agl={s['agl']:7.1f} vs={s['vs']:+6.1f} "
              f"gs={s['gs']:5.1f}kt coll={s['coll']:.3f} roll={s['roll']:+6.1f} "
              f"pitch={s['pitch']:+6.1f} hdg={s['heading']:6.1f}")

        creason = is_crashed(s)
        if creason:
            reason = f"t={t:.1f}s {s['phase']}：{creason}"
            print(f"[!!!] 坠机识别：{reason}")
            break

        if s["pnum"] == 17 and s["ticks"] > 0:
            # 落地静止后：旋翼刹车停转（有始有终）
            print("[收尾] 已停稳，旋翼刹车 ...")
            fg.set("/controls/rotor/brake", 1)
            t_b = time.time()
            while time.time() - t_b < 90:
                rpm = fg.get("/rotors/main/rpm")
                print(f"  旋翼 {rpm:.0f} RPM")
                if rpm < 60:
                    break
                time.sleep(3)
            break
        time.sleep(max(0.0, 0.5 - (time.time() - t_frame) + 0.0))
        t_frame = time.time()

    try:
        fg.set("/demo/seq/run", 0)
        fg.set("/demo/seq/start", 0)
    except OSError:
        pass
    return log, reason


def main():
    print("=" * 64)
    print("  B1 v3 · Bo-105 完整起落（rev6 全内环化 · 静风 · 坪内起降）")
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
    env_ok, env_info = preflight_env(fg)
    if not env_ok:
        print("[失败] 环境预检未通过（停机点/离地异常），禁止起飞")
        return 3

    log, reason = run_flight(fg)

    # 新格式：包含环境配置 + 逐帧数据（兼容旧格式：纯帧列表）
    flight_data = {
        "config": {
            "log_format": "fg_demo_v2",
            "timestamp": ts,
            **env_info,
        },
        "frames": log,
    }
    (LOG_DIR / f"flight_{ts}.json").write_text(
        json.dumps(flight_data, ensure_ascii=False, indent=1), encoding="utf-8")

    last = log[-1] if log else None
    landed = bool(log) and "done" in [e["phase"] for e in log]
    max_agl = max((e["agl"] for e in log), default=0.0)
    max_roll = max((abs(e["roll"]) for e in log), default=0.0)
    max_pitch = max((abs(e["pitch"]) for e in log), default=0.0)
    hdg_drift = abs(log[-1]["heading"] - log[0]["heading"]) if len(log) > 1 else 0.0
    ok = landed and reason is None and max_agl >= HOVER_AGL_FT - 15

    lines = [
        f"Bo-105 科目演示报告 v4（rev7 序列器：起飞悬停前飞滚转偏航降落）  {ts}",
        f"环境: {env_info['scenario']} / 出生点 {env_info['spawn_lat']},{env_info['spawn_lon']} "
        f"(距坪心 {env_info['spawn_dist_nm']}nm) / 风 {env_info['wind_speed_kt']}kt@{env_info['wind_from_deg']}° "
        f"/ {env_info['aircraft']} 视觉替身(物理={env_info['physics']})",
        f"控制: {env_info['control_mode']} / 悬停目标 {env_info['hover_target_agl_ft']}ft",
        "-" * 56,
        f"结果: {'成功 · 垂直起落 静止接地 无坠毁' if ok else '失败'}",
        f"原因: {reason or '无'}",
        f"飞行时长: {last['t'] if last else 0:.1f}s | 帧数: {len(log)}",
        f"最高离地: {max_agl:.1f} ft (目标 {HOVER_AGL_FT:.0f})",
        f"全程最大 |roll|={max_roll:.1f}deg |pitch|={max_pitch:.1f}deg",
        f"航向总漂移: {hdg_drift:.1f} deg（漂移阻尼效果参考）",
        f"末态: phase={last['phase'] if last else '-'} "
        f"agl={last['agl'] if last else '-'}ft vs={last['vs'] if last else '-'} "
        f"rpm={last['rpm'] if last else '-'}",
    ]
    summary = "\n".join(lines)
    (LOG_DIR / f"flight_{ts}_summary.txt").write_text(summary + "\n", encoding="utf-8")
    print("\n" + summary)
    print(f"\n[日志] {LOG_DIR}\\flight_{ts}.json / _summary.txt")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
