# -*- coding: utf-8 -*-
"""C3 · 导航评测指标(航空 VLN 标准)

实现 OpenFly / Aerial-VLN 语境下的标准导航指标族。全部为纯函数:
    gt_path   : List[(x, y, z)]  ground-truth 航点序列(起点→…→目标)
    pred_path : List[(x, y, z)]  智能体实际轨迹
    success_threshold : 判定成功的终点距离阈值(米),OpenFly benchmark 默认 3m

指标:
    ne   Navigation Error        终点到目标欧氏距离(米,越小越好)
    sr   Success Rate            ne < 阈值 的比例(0~1)
    osr  Oracle Success Rate     轨迹上任一点进入阈值即算成功的比例
    spl  Success weighted by Path Length   SR × (l/max(p,l)),l=起点→目标直线最短路
    ndtw normalized DTW          轨迹与 GT 的归一化动态时间规整相似度(0~1,越接近1越好)
    sdtw SDTW                    nDTW 的"成功加权"版本
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float, float]
Path = List[Point]

SUCCESS_THRESHOLD_M = 3.0


def dist(a: Point, b: Point) -> float:
    """三维欧氏距离。"""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def path_length(path: Path) -> float:
    """折线总长度。"""
    if len(path) < 2:
        return 0.0
    return sum(dist(path[i], path[i + 1]) for i in range(len(path) - 1))


def navigation_error(gt_path: Path, pred_path: Path) -> float:
    """终点到 GT 目标的欧氏距离(米)。GT/预测任一为空时返回 inf。"""
    if not gt_path or not pred_path:
        return float("inf")
    return dist(pred_path[-1], gt_path[-1])


def is_success(gt_path: Path, pred_path: Path,
               threshold: float = SUCCESS_THRESHOLD_M) -> bool:
    return navigation_error(gt_path, pred_path) < threshold


def oracle_success(gt_path: Path, pred_path: Path,
                   threshold: float = SUCCESS_THRESHOLD_M) -> bool:
    if not gt_path or not pred_path:
        return False
    goal = gt_path[-1]
    return any(dist(p, goal) < threshold for p in pred_path)


def spl(gt_path: Path, pred_path: Path,
        threshold: float = SUCCESS_THRESHOLD_M) -> float:
    """单条轨迹 SPL;成功时 = l / max(p, l),失败为 0。

    l = GT 参考路径长度(航点折线总长,即数据集给定的"应走路径");
    p = 预测轨迹实际长度。GT 回放时 l==p → SPL=1。
    """
    if not is_success(gt_path, pred_path, threshold):
        return 0.0
    l_ref = path_length(gt_path)
    if l_ref <= 0:
        return 0.0
    p_len = path_length(pred_path)
    return l_ref / max(p_len, l_ref)


def _dtw_matrix(a: Path, b: Path) -> List[List[float]]:
    n, m = len(a), len(b)
    INF = float("inf")
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = dist(a[i - 1], b[j - 1])
            D[i][j] = c + min(D[i - 1][j], D[i][j - 1], D[i - 1][j - 1])
    return D


def ndtw(gt_path: Path, pred_path: Path,
         threshold: float = SUCCESS_THRESHOLD_M) -> float:
    """nDTW = exp(-DTW / (len(gt)*threshold)),1 表示与 GT 完全重合。"""
    if not gt_path or not pred_path:
        return 0.0
    D = _dtw_matrix(gt_path, pred_path)
    dtw = D[len(gt_path)][len(pred_path)]
    denom = max(len(gt_path), 1) * threshold
    return math.exp(-dtw / denom)


def sdtw(gt_path: Path, pred_path: Path,
         threshold: float = SUCCESS_THRESHOLD_M) -> float:
    """SDTW = nDTW × 是否成功。"""
    return ndtw(gt_path, pred_path, threshold) * (1.0 if is_success(gt_path, pred_path, threshold) else 0.0)


def evaluate_episodes(episodes: Sequence[Tuple[Path, Path]],
                      threshold: float = SUCCESS_THRESHOLD_M) -> dict:
    """批量评测。

    episodes: [(gt_path, pred_path), ...]
    返回指标字典(NE 单位米;SR/OSR/SPL/nDTW/SDTW 为 0~1)。
    """
    n = len(episodes)
    if n == 0:
        return {"n": 0}
    nes = [navigation_error(g, p) for g, p in episodes]
    srs = [1.0 if is_success(g, p, threshold) else 0.0 for g, p in episodes]
    osrs = [1.0 if oracle_success(g, p, threshold) else 0.0 for g, p in episodes]
    spls = [spl(g, p, threshold) for g, p in episodes]
    ndtws = [ndtw(g, p, threshold) for g, p in episodes]
    sdtws = [sdtw(g, p, threshold) for g, p in episodes]
    finite_nes = [v for v in nes if math.isfinite(v)]
    return {
        "n": n,
        "threshold_m": threshold,
        "NE_m": sum(finite_nes) / max(len(finite_nes), 1),
        "SR": sum(srs) / n,
        "OSR": sum(osrs) / n,
        "SPL": sum(spls) / n,
        "nDTW": sum(ndtws) / n,
        "SDTW": sum(sdtws) / n,
    }


if __name__ == "__main__":
    # 自检:GT 轨迹 vs 三种代理轨迹,验证指标方向正确
    gt = [(0, 0, 10), (10, 0, 10), (20, 0, 10), (30, 0, 10)]
    perfect = list(gt)                    # 完全复现 → SR=1 SPL=1 nDTW≈1
    detour = [(0, 0, 10), (15, 8, 12), (30, 0, 10)]   # 绕路但到达 → SR=1 SPL<1
    fail = [(0, 0, 10), (10, 0, 10), (18, 2, 11)]     # 半路停下 → SR=0
    r = evaluate_episodes([(gt, perfect), (gt, detour), (gt, fail)])
    print("self-check:", {k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()})
    assert r["SR"] == 2 / 3 and r["SPL"] > 0 and r["SPL"] < 1
    assert r["OSR"] == 2 / 3, "oracle: perfect/detour 成功, fail 未进 3m"
    print("[PASS] metrics self-check")
