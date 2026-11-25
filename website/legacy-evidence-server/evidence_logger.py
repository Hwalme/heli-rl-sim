# -*- coding: utf-8 -*-
"""
证据链记录器 (Evidence Chain Logger)
====================================

为  低空具身智能体项目提供"训练可追溯"的证据链能力:
每次训练 / 评估 / 验证 / 事故都追加一条带 SHA-256 哈希的证据记录,
后一条记录包含前一条记录的哈希 (prev_hash), 形成防篡改哈希链。

存储布局:
    evidence/
    ├── chain.jsonl          # 证据链 (append-only, 每行一条 JSON 记录)
    ├── artifacts/           # 证据引用的产物快照 (可选, 复制入链)
    └── live/current_run.json  # 正在进行的训练的实时状态 (网站轮询)

记录结构 (canonical JSON, sort_keys, sha256):
    {
      "seq": 7,                      # 链内序号 (从 1 开始)
      "id": "EV-20260828-000007",    # 证据编号
      "time": "2026-08-28T09:30:00+08:00",
      "type": "evaluation",          # milestone/training_run/evaluation/verification/incident/analysis/data_collection/note
      "actor": "train_ppo_evidence.py",
      "title": "...",
      "payload": { ... },            # 指标、配置等自由结构
      "artifacts": [{"name": "...", "path": "...", "sha256": "..."}],
      "prev_hash": "<上一条 hash | 64 个 0>",
      "hash": "<sha256(canonical(本记录去掉 hash 字段))>"
    }

用法 (训练脚本中):
    from evidence_logger import EvidenceLogger
    ev = EvidenceLogger(actor="train_ppo.py")
    ev.begin_run("PPO v3 训练", {"steps": 400000})
    ev.update_live(steps=120000, stage="training", metrics={"success_rate": 0.5})
    ev.log("evaluation", "中期评估", {"success_rate": 0.5})
    ev.finish_run({"success_rate": 1.0}, artifacts=[("策略模型", "results/helo_ppo.zip")])

用法 (验证整条链):
    python evidence_logger.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_DIR = os.path.join(BASE_DIR, "evidence")
CHAIN_PATH = os.path.join(EVIDENCE_DIR, "chain.jsonl")
LIVE_DIR = os.path.join(EVIDENCE_DIR, "live")
LIVE_PATH = os.path.join(LIVE_DIR, "current_run.json")
ARTIFACT_DIR = os.path.join(EVIDENCE_DIR, "artifacts")

GENESIS_HASH = "0" * 64

# 东八区 (项目为国内直升机所, 统一用 +08:00)
TZ = timezone(timedelta(hours=8))

RECORD_TYPES = {
    "milestone":       "进度节点",
    "training_run":    "训练运行",
    "evaluation":      "评估",
    "verification":    "指标验证",
    "incident":        "事故/失败",
    "analysis":        "分析报告",
    "data_collection": "数据采集",
    "note":            "人工记录",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def canonical_json(obj) -> str:
    """参与哈希的规范化序列化: key 排序 + 紧凑分隔符, 保证跨进程可复算。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, _bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 跨进程文件锁 (Windows: msvcrt; 其他平台: fcntl)
# ---------------------------------------------------------------------------

class _FileLock:
    """对 chain.jsonl 的追加加排他锁, 防止训练进程与网站后端同时写坏链。"""

    def __init__(self, path: str):
        self.path = path + ".lock"
        self._fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            self._fh.close()
            self._fh = None
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                self._fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        return False


# ---------------------------------------------------------------------------
# 链读取 / 校验
# ---------------------------------------------------------------------------

def load_chain() -> list:
    """读取全部证据记录 (按 seq 升序)。文件不存在或为空返回 []。"""
    if not os.path.exists(CHAIN_PATH):
        return []
    records = []
    with open(CHAIN_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    records.sort(key=lambda r: r.get("seq", 0))
    return records


def _record_digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return sha256_hex(canonical_json(body).encode("utf-8"))


def verify_chain() -> dict:
    """
    完整性校验:
      1. 每条记录 hash == sha256(canonical(去掉 hash 的记录))
      2. 链接: 第 n 条 prev_hash == 第 n-1 条 hash (首条 == GENESIS)
      3. 产物校验: 记录引用的 artifacts 文件存在且 sha256 一致
    返回 {ok, total, broken_at, problems[], artifact_stats{checked, missing, mismatch}}
    """
    records = load_chain()
    problems = []
    broken_at = None
    prev = GENESIS_HASH
    art_checked = art_missing = art_mismatch = 0

    for rec in records:
        seq = rec.get("seq")
        if rec.get("prev_hash") != prev:
            problems.append(f"#{seq} prev_hash 与前一条记录哈希不匹配")
            broken_at = broken_at or seq
        if _record_digest(rec) != rec.get("hash"):
            problems.append(f"#{seq} hash 重算不一致 (记录内容被篡改或非 canonical 写入)")
            broken_at = broken_at or seq
        for art in rec.get("artifacts", []):
            p = art.get("path", "")
            expect = art.get("sha256", "")
            if not os.path.isabs(p):
                p = os.path.join(BASE_DIR, p)
            if not os.path.exists(p):
                art_missing += 1
                problems.append(f"#{seq} 产物缺失: {art.get('name', p)}")
            else:
                art_checked += 1
                if sha256_file(p) != expect:
                    art_mismatch += 1
                    problems.append(f"#{seq} 产物内容变更: {art.get('name', p)}")
        prev = rec.get("hash", prev)

    # 若某条 hash 已坏, 其后所有 prev 链接都会连带着报 prev_hash 问题, 去重即可
    ok = not problems
    return {
        "ok": ok,
        "total": len(records),
        "broken_at": broken_at,
        "problems": problems,
        "artifact_stats": {
            "checked": art_checked,
            "missing": art_missing,
            "mismatch": art_mismatch,
        },
        "verified_at": now_iso(),
        "head_hash": records[-1]["hash"] if records else GENESIS_HASH,
    }


# ---------------------------------------------------------------------------
# 记录器
# ---------------------------------------------------------------------------

class EvidenceLogger:
    """向证据链追加记录 + 维护 live 实时状态。"""

    def __init__(self, actor: str = "manual"):
        self.actor = actor
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        os.makedirs(LIVE_DIR, exist_ok=True)

    # -- 内部 ---------------------------------------------------------------

    def _next_seq_and_prev(self) -> tuple:
        records = load_chain()
        if records:
            return records[-1]["seq"] + 1, records[-1]["hash"]
        return 1, GENESIS_HASH

    # -- 公开 API -------------------------------------------------------------

    def log(self, type_: str, title: str, payload: dict | None = None,
            artifacts: list | None = None) -> dict:
        """
        追加一条正式证据。
        artifacts: [(名称, 路径), ...] 或 [{"name","path"}]; 路径相对项目根,
        会计算文件 SHA-256 写入记录; 同时把文件复制到 evidence/artifacts/ 留档。
        锁内原子完成: 分配 seq -> 生成 id -> 计算哈希 -> 追加落盘。
        """
        if type_ not in RECORD_TYPES:
            raise ValueError(f"未知证据类型: {type_}, 可选: {list(RECORD_TYPES)}")
        arts = []
        for item in artifacts or []:
            if isinstance(item, dict):
                name, path = item["name"], item["path"]
            else:
                name, path = item
            ap = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
            if os.path.exists(ap):
                digest = sha256_file(ap)
                # 留档副本: evidence/artifacts/<时间戳>_<名称>
                snap_name = f"{datetime.now(TZ):%Y%m%d_%H%M%S}_{os.path.basename(ap)}"
                snap_path = os.path.join(ARTIFACT_DIR, snap_name)
                os.makedirs(ARTIFACT_DIR, exist_ok=True)
                try:
                    shutil.copy2(ap, snap_path)
                    stored = os.path.relpath(snap_path, BASE_DIR).replace("\\", "/")
                except OSError:
                    stored = path  # 复制失败则仅引用原路径
                arts.append({"name": name, "path": stored,
                             "origin": path.replace("\\", "/"), "sha256": digest})
            else:
                arts.append({"name": name, "path": path.replace("\\", "/"),
                             "origin": path.replace("\\", "/"), "sha256": None,
                             "missing_at_log_time": True})
        with _FileLock(CHAIN_PATH):
            seq, prev = self._next_seq_and_prev()
            rec = {
                "id": f"EV-{datetime.now(TZ):%Y%m%d}-{seq:06d}",
                "time": now_iso(),
                "type": type_,
                "actor": self.actor,
                "title": title,
                "payload": payload or {},
                "artifacts": arts,
                "seq": seq,
                "prev_hash": prev,
            }
            rec["hash"] = _record_digest(rec)
            with open(CHAIN_PATH, "a", encoding="utf-8") as f:
                f.write(canonical_json(rec) + "\n")
        return rec

    # -- live 训练状态 ---------------------------------------------------------

    def begin_run(self, title: str, config: dict | None = None) -> str:
        run_id = f"RUN-{datetime.now(TZ):%Y%m%d-%H%M%S}"
        self._write_live({
            "run_id": run_id,
            "title": title,
            "actor": self.actor,
            "config": config or {},
            "stage": "started",
            "steps": 0,
            "total_steps": (config or {}).get("steps"),
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "metrics": {},
            "history": [],   # [{steps, success_rate, crash_rate, alt_err}]
        })
        return run_id

    def update_live(self, steps: int | None = None, stage: str | None = None,
                    metrics: dict | None = None, history_point: dict | None = None,
                    total_steps: int | None = None):
        cur = self.read_live() or {}
        if steps is not None:
            cur["steps"] = steps
        if total_steps is not None:
            cur["total_steps"] = total_steps
        if stage is not None:
            cur["stage"] = stage
        if metrics:
            cur["metrics"] = {**(cur.get("metrics") or {}), **metrics}
        if history_point is not None:
            cur.setdefault("history", []).append(history_point)
        cur["updated_at"] = now_iso()
        self._write_live(cur)

    def finish_run(self, stage: str = "finished"):
        cur = self.read_live()
        if cur:
            cur["stage"] = stage
            cur["updated_at"] = now_iso()
            self._write_live(cur)

    def clear_live(self):
        try:
            os.remove(LIVE_PATH)
        except FileNotFoundError:
            pass

    @staticmethod
    def _write_live(data: dict):
        os.makedirs(LIVE_DIR, exist_ok=True)
        tmp = LIVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LIVE_PATH)

    @staticmethod
    def read_live() -> dict | None:
        if not os.path.exists(LIVE_PATH):
            return None
        try:
            with open(LIVE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None


# ---------------------------------------------------------------------------
# CLI: python evidence_logger.py --verify | --tail 5
# ---------------------------------------------------------------------------

def _main():
    ap = argparse.ArgumentParser(description="证据链工具")
    ap.add_argument("--verify", action="store_true", help="校验整条证据链完整性")
    ap.add_argument("--tail", type=int, default=0, help="显示最近 N 条记录")
    args = ap.parse_args()

    if args.verify:
        result = verify_chain()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["ok"] else 1)

    records = load_chain()
    if args.tail:
        records = records[-args.tail:]
    for rec in records:
        print(f"#{rec['seq']:>3} {rec['id']}  [{RECORD_TYPES.get(rec['type'], rec['type'])}] "
              f"{rec['title']}  @ {rec['time']}")
    if not records:
        print("(证据链为空)")


if __name__ == "__main__":
    _main()
