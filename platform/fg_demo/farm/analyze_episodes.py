# -*- coding: utf-8 -*-
"""CEM 农场回合数据分析器（只读）。
用法: python farm/analyze_episodes.py [--recent N]
输出: 通过率、失败模式、flip_t/参数相关性、分维度成功/失败分布对比。
"""
import json
import math
import sys
from pathlib import Path

CSV = Path(__file__).resolve().parent / "episodes.csv"
DIMS = ["ffw", "ramp_fast", "punch", "yaw_hold", "damper"]
LO = {"ffw": 0.0, "ramp_fast": 0.03, "punch": 0.38, "yaw_hold": 0.5, "damper": 0.0}
HI = {"ffw": 4.0, "ramp_fast": 0.10, "punch": 0.52, "yaw_hold": 2.5, "damper": 1.5}


def rows():
    out = []
    if not CSV.exists():
        return out
    with open(CSV, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass  # 尾部半行（正在写入）
    return out


def mean(v):
    return sum(v) / len(v) if v else float("nan")


def sd(v):
    if len(v) < 2:
        return float("nan")
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def main():
    recent = 0
    if "--recent" in sys.argv:
        recent = int(sys.argv[sys.argv.index("--recent") + 1])
    rs = rows()
    if recent:
        rs = rs[-recent:]
    n = len(rs)
    if n == 0:
        print("no episodes yet")
        return
    ok = [r for r in rs if r.get("result") == "success"]
    fail = [r for r in rs if r.get("result") != "success"]
    print(f"episodes={n} success={len(ok)} pass_rate={len(ok)/n*100:.1f}%")

    from collections import Counter
    print("result mix:", dict(Counter(r.get("result", "?") for r in rs)))
    print("max_phase mix:", dict(sorted(Counter(int(r.get("max_phase", -1)) for r in rs).items())))

    # 通过率随迭代
    by_iter = {}
    for r in rs:
        by_iter.setdefault(r.get("iter", -1), []).append(1 if r.get("result") == "success" else 0)
    its = sorted(by_iter)
    if its:
        line = []
        for it in its[-12:]:
            v = by_iter[it]
            line.append(f"i{it}:{sum(v)}/{len(v)}")
        print("per-iter pass (last 12):", " ".join(line))

    # 最近窗口
    w = rs[-20:]
    print(f"last-20 pass rate: {sum(1 for r in w if r.get('result')=='success')/len(w)*100:.0f}%")

    # flip_t 分布（失败且记录了 flip_t 的）
    fts = [r["flip_t"] for r in fail if isinstance(r.get("flip_t"), (int, float))]
    if fts:
        print(f"flip_t: n={len(fts)} mean={mean(fts):.1f}s sd={sd(fts):.1f} "
              f"min={min(fts):.1f} max={max(fts):.1f} med={sorted(fts)[len(fts)//2]:.1f}")
    ts = [r["t"] for r in fail if isinstance(r.get("t"), (int, float))]
    if ts:
        print(f"fail episode duration: mean={mean(ts):.1f}s max={max(ts):.1f}s")

    # 分维度：成功 vs 失败分布
    print("\ndim           succ(mean±sd)     fail(mean±sd)     delta    corr(dim,success)")
    for d in DIMS:
        sv = [r["params"][d] for r in ok if isinstance(r.get("params"), dict) and d in r["params"]]
        fv = [r["params"][d] for r in fail if isinstance(r.get("params"), dict) and d in r["params"]]
        allv = [(r["params"][d], 1.0 if r.get("result") == "success" else 0.0)
                for r in rs if isinstance(r.get("params"), dict) and d in r["params"]]
        c = corr([a for a, _ in allv], [b for _, b in allv])
        print(f"{d:12s}  {mean(sv):6.2f}±{sd(sv):4.2f} (n={len(sv):3d}) "
              f"{mean(fv):6.2f}±{sd(fv):4.2f} (n={len(fv):3d}) "
              f"{(mean(sv)-mean(fv)):+6.2f}   r={c:+.2f}")

    # 失败回合里成功差异最大的维度（点二列相关平方的粗替：组间/组内方差比）
    print("\nvariance-ratio (between-group / total) per dim:")
    for d in DIMS:
        allv = [r["params"][d] for r in rs if isinstance(r.get("params"), dict) and d in r["params"]]
        if len(allv) < 4 or len(ok) < 1 or len(fail) < 1:
            continue
        tot = sd(allv) ** 2
        if tot <= 1e-12:
            continue
        ns, nf = len(ok), len(fail)
        betw = (ns * (mean([r["params"][d] for r in ok]) - mean(allv)) ** 2
                + nf * (mean([r["params"][d] for r in fail]) - mean(allv)) ** 2) / n
        print(f"  {d:12s} {betw/tot:.3f}")


if __name__ == "__main__":
    main()
