# -*- coding: utf-8 -*-
"""CEM 农场驱动器：多实例并行采样控制器参数，精英保留迭代，直到连续 20 回合
通过率 >= 90% 或时间预算耗尽。状态持久化，可断点续跑。
用法: python farm_driver.py --budget-min 240
"""
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FARM_DIR = HERE / "farm"
FARM_DIR.mkdir(exist_ok=True)
STATE = FARM_DIR / "cem_state.json"
CSV = FARM_DIR / "episodes.csv"

WORKERS = [  # (port, tag, launcher bat)
    # 运维约束（2026-08-29 04:35~05:20 三次复现实测）：本机 FG 2024.1.4
    # 为全局单例——任一实例存活期间，新实例启动即永久卡死（~100MB 零 CPU，
    # junction/独立 FG_HOME/换端口均无效）；且实例寿命 ~12 分钟
    # （heap corruption，WER 0xc0000374）。三实例并行在本机不可实现，
    # 改为单实例轮转，worker 自带自愈重拉。
    (5504, "A", r"C:\Users\Administrator\AppData\Local\Temp\fg_launch_A.bat"),
]
EPS_PER_EVAL = 2
TARGET_PASS = 0.90
CONFIRM_N = 20
BUDGET_MIN = 240.0

DIMS = ["ramp_fast", "punch", "yaw_hold", "damper", "cyc_gain", "lo_trim", "ff_band"]
# rev8.2 语义（2026-08-29 XML，主任核定）：新增 lo-trim 低头配平旋钮
# （离地段温和扶住 pitch=lo-trim，0.5×cyc-gain 增益，±0.15 限幅）；
# phase2 出口 agl+14 或 vs>2.0。锚点 = 01:04 成功飞行配方 + lo-trim -3.5°。
# rev8.6 新增 ff-band（毒带 coll 0.40-0.62 内前馈增益倍率，默认 2.0，范围 1.0-3.0）
# ——当前主攻旋钮，交接包 2026-08-29 §3.6 行动清单第1项。
MU0 = [0.02, 0.33, 1.0, 0.5, 0.5, -3.5, 2.0]
SIG0 = [0.003, 0.012, 0.25, 0.20, 0.20, 0.8, 0.4]
LO = [0.008, 0.30, 0.400, 0.000, 0.250, -6.0, 1.0]
HI = [0.100, 0.360, 2.500, 0.800, 1.200, 0.0, 3.0]
SIG_MIN = [0.0015, 0.005, 0.08, 0.06, 0.06, 0.3, 0.15]


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"mu": MU0[:], "sig": SIG0[:], "iter": 0, "hist": [],
            "confirm": [], "budget_used_min": 0.0}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def sample_params(st, rng):
    import random
    p = {}
    for i, k in enumerate(DIMS):
        v = random.gauss(st["mu"][i], st["sig"][i])
        p[k] = max(LO[i], min(HI[i], v))
    return p


def run_worker(port, tag, bat, params, eps):
    args = [sys.executable, str(HERE / "farm_worker.py"), str(port), tag,
            json.dumps(params), str(eps)]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    try:
        out, _ = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        # 修复原实现 bug：超时路径不杀子进程 → 僵尸 worker 积压、
        # 幽灵 relaunch 实例（04:56 事件）。/T 连带杀工人重启的 cmd 树。
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        out, _ = proc.communicate()
    for line in (out or "").splitlines():
        if line.startswith("RESULTS_JSON:"):
            return json.loads(line[len("RESULTS_JSON:"):])
    return [{"result": "worker-error", "score": 0.0, "ep": i} for i in range(eps)]


def eval_params_parallel(st, params_list):
    """params_list: [(params, worker_idx)] 并行跑"""
    results = [None] * len(params_list)
    threads = []

    def w(i, params, widx):
        port, tag, bat = WORKERS[widx]
        results[i] = run_worker(port, tag, bat, params, EPS_PER_EVAL)

    for i, (params, widx) in enumerate(params_list):
        t = threading.Thread(target=w, args=(i, params, widx))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    flat = []
    for r in results:
        if r:
            flat.extend(r)
    return flat


def main():
    budget_min = BUDGET_MIN
    for i, a in enumerate(sys.argv):
        if a == "--budget-min":
            budget_min = float(sys.argv[i + 1])

    st = load_state()
    import random
    rng_seed = st.get("rng", 42)
    random.seed(rng_seed)
    csv_f = open(CSV, "a", encoding="utf-8")
    t_start = time.time()
    print(f"[farm] resume iter={st['iter']} mu={st['mu']}", flush=True)

    while (time.time() - t_start) < budget_min * 60:
        # 采样 3 组参数（3 工人并行）
        batch = [(sample_params(st, random), wi) for wi in range(len(WORKERS))]
        t0 = time.time()
        results = eval_params_parallel(st, batch)
        dt = time.time() - t0

        # 记分
        for pi, (params, widx) in enumerate(batch):
            rs = [r for r in results if r.get("params") == params] if False else []
        # 简化：按顺序分配结果（run_worker 返回按 params 顺序）
        idx = 0
        for pi, (params, widx) in enumerate(batch):
            rs = results[idx: idx + EPS_PER_EVAL]
            idx += EPS_PER_EVAL
            if not rs:
                continue
            # 基建失败行（not-ready/worker-error/no-instance，无 params 键）
            # 落盘留审计，但不进 hist/CEM/达标统计——分析师批次2建议：
            # 只有真实飞行回合参与统计，否则通过率天花板被基建故障压死。
            for r in rs:
                csv_f.write(json.dumps({**r, "iter": st["iter"]}) + "\n")
            real = [r for r in rs if "params" in r]
            if not real:
                print(f"[farm] iter={st['iter']} w{widx} 基建失败 x{len(rs)}"
                      f"（不进统计）", flush=True)
                continue
            passes = sum(1 for r in real if r["result"] == "success")
            mean_score = sum(r["score"] for r in real) / len(real)
            st["hist"].append({"iter": st["iter"], "params": params,
                               "pass": passes, "n": len(real),
                               "score": mean_score})
            print(f"[farm] iter={st['iter']} w{widx} params="
                  f"{ {k: round(v,3) for k,v in params.items()} } "
                  f"pass={passes}/{len(real)} score={mean_score:.2f} ({dt:.0f}s)",
                  flush=True)

        # CEM 更新：按 score 取精英；近 10 迭代零成功 → 冻结 mu/sig
        # （分析师建议：确定性仿真下全零精英窗口会让方差假塌缩）
        recent_pass = sum(h["pass"] for h in st["hist"][-10:])
        if st["hist"] and recent_pass == 0:
            print("[farm] 近10迭代零成功：冻结 mu/sig", flush=True)
        else:
            all_scores = [(h["score"], h["params"]) for h in st["hist"][-9:]]
            all_scores.sort(key=lambda x: -x[0])
            elite = all_scores[:max(3, len(all_scores) // 2)]
            for i, k in enumerate(DIMS):
                vals = [p[k] for _, p in elite]
                st["mu"][i] = sum(vals) / len(vals)
                var = sum((v - st["mu"][i]) ** 2 for v in vals) / len(vals)
                st["sig"][i] = max(SIG_MIN[i], var ** 0.5)
        st["iter"] += 1
        st["budget_used_min"] += dt / 60.0

        # 达标判定：最近 CONFIRM_N 个真实飞行回合的通过率
        recent = [h for h in st["hist"] for _ in range(h["n"])][-CONFIRM_N:]
        # hist 存的是聚合，重新从 csv 统计更可靠（剔除无 params 的基建行）
        csv_f.flush()
        with open(CSV, encoding="utf-8-sig") as f:
            eps_rows = [json.loads(l) for l in f if l.strip()]
        real_eps = [r for r in eps_rows if "params" in r]
        recent_eps = real_eps[-CONFIRM_N:]
        if len(recent_eps) >= CONFIRM_N:
            pr = sum(1 for r in recent_eps if r.get("result") == "success") / len(recent_eps)
            print(f"[farm] 最近{len(recent_eps)}真实回合通过率 {pr*100:.0f}%", flush=True)
            if pr >= TARGET_PASS:
                best = max(st["hist"], key=lambda h: h["score"])
                print(f"[farm] *** TARGET 90% ACHIEVED *** mu={st['mu']}", flush=True)
                save_state(st)
                csv_f.close()
                return 0
        save_state(st)
    print("[farm] 预算耗尽", flush=True)
    save_state(st)
    csv_f.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
