# -*- coding: utf-8 -*-
"""
evidence_server.py — 训练证据链管理平台 · 后端服务
====================================================

纯 Python 标准库实现 (http.server), 零第三方依赖, 内网可离线运行。

    python evidence_server.py            # 默认 http://127.0.0.1:8600
    python evidence_server.py --port 9000

API:
    GET  /                    前端页面 (web/index.html)
    GET  /api/overview        看板聚合数据 (指标/节点/计数/最新证据/实时训练)
    GET  /api/evidence        证据列表
    GET  /api/evidence/<id>   证据详情
    GET  /api/verify          哈希链完整性校验 (重算全部哈希 + 产物校验)
    GET  /api/live            当前训练实时状态
    POST /api/evidence        追加人工证据 {type,title,payload}
                              payload 内可含 {"metric_id":"K3","measured":0.96}
                              用于人工上报指标 (网站"记录"页表单)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
sys.path.insert(0, BASE_DIR)

import evidence_logger as el  # noqa: E402

# ---------------------------------------------------------------------------
# 指标定义 (口径与展示规格) —— 与验收要求一一对应
# ---------------------------------------------------------------------------

METRIC_SPEC = {
    "K1": {"name": "动作操作成功率", "dir": ">=", "threshold": 85.0, "unit": "%",
           "display": "percent", "desc": "自主起飞、悬停、前飞、滚转、偏航、降落",
           "auto_field": ("evaluation", "success_rate", 100.0)},  # 训练评估自动回填
    "K2": {"name": "系统响应延迟", "dir": "<=", "threshold": 200.0, "unit": "ms",
           "display": "number", "desc": "控制指令发送至机器人产生响应"},
    "K3": {"name": "任务意图理解准确率", "dir": ">=", "threshold": 95.0, "unit": "%",
           "display": "percent", "desc": "任务规划、航路导航典型飞行场景"},
    "K4": {"name": "操控映射准确率", "dir": ">=", "threshold": 90.0, "unit": "%",
           "display": "percent", "desc": "任务意图到座舱物理操作的精准映射"},
    "K5": {"name": "座舱建模精度", "dir": "<=", "threshold": 5.0, "unit": "mm",
           "display": "number", "desc": "座舱三维数字资产库整体建模精度"},
    "K6": {"name": "有效操纵数据时长", "dir": ">=", "threshold": 1000.0, "unit": "h",
           "display": "number", "desc": "第一、三人称视角操纵摇杆有效数据"},
}


def compute_metrics(records: list) -> list:
    """从证据链推导每项指标的当前实测值: 自动评估回填 + 人工上报 (metric_id), 取 seq 最大者。"""
    results = {mid: {"measured": None, "evidence_id": None, "scope": None,
                     "basis": None, "updated_at": None}
               for mid in METRIC_SPEC}

    for rec in records:  # 按 seq 升序 → 后写覆盖
        p = rec.get("payload") or {}

        # 规则 A: 训练/评估记录自动回填 K1 (payload.success_rate)
        spec = METRIC_SPEC["K1"]
        if rec.get("type") == spec["auto_field"][0] and isinstance(
                p.get("success_rate"), (int, float)):
            scale = spec["auto_field"][2]
            results["K1"] = {
                "measured": round(p["success_rate"] * scale, 1),
                "evidence_id": rec["id"], "scope": p.get("env"),
                "basis": "训练评估自动回填",
                "updated_at": rec["time"]}

        # 规则 B: 任意记录 payload.metric_id 显式上报
        mid = p.get("metric_id")
        if mid in METRIC_SPEC and isinstance(p.get("measured"), (int, float)):
            val = p["measured"]
            unit = (p.get("unit") or METRIC_SPEC[mid]["unit"]).strip().lower()
            if METRIC_SPEC[mid]["display"] == "percent" and unit in ("%", "percent"):
                val = val * (100.0 if val <= 1.0 else 1.0)
            elif METRIC_SPEC[mid]["display"] == "percent":
                val = val  # 已是百分数值
            results[mid] = {
                "measured": round(float(val), 2),
                "evidence_id": rec["id"],
                "scope": p.get("scope"),
                "basis": p.get("basis") or "证据链记录上报",
                "updated_at": rec["time"]}

        # 规则 C: verification 记录的 judgements 批量上报
        for j in (p.get("judgements") or []):
            mid = j.get("metric_id")
            if mid in METRIC_SPEC and isinstance(j.get("measured"), (int, float)):
                val = float(j["measured"])
                if METRIC_SPEC[mid]["display"] == "percent" and val <= 1.0:
                    val *= 100.0
                results[mid] = {
                    "measured": round(val, 2),
                    "evidence_id": rec["id"],
                    "scope": j.get("scope"),
                    "basis": j.get("evidence_basis") or "验证判定上报",
                    "updated_at": rec["time"]}

    out = []
    for mid, spec in METRIC_SPEC.items():
        r = results[mid]
        measured = r["measured"]
        if measured is None:
            status = "pending"
        else:
            ok = measured >= spec["threshold"] if spec["dir"] == ">=" \
                else measured <= spec["threshold"]
            status = "pass" if ok else "fail"
        out.append({
            "id": mid, "name": spec["name"], "desc": spec["desc"],
            "dir": spec["dir"], "threshold": spec["threshold"], "unit": spec["unit"],
            "display": spec["display"],
            "measured": measured, "status": status,
            "evidence_id": r["evidence_id"], "scope": r["scope"],
            "basis": r["basis"], "updated_at": r["updated_at"],
        })
    return out


def build_milestones(records: list):
    """从 milestone 证据读取节点定义, 结合当前时间与后续 milestone 证据判定状态。"""
    baseline = None
    done_nodes = {}
    for rec in records:
        p = rec.get("payload") or {}
        if rec.get("type") == "milestone" and p.get("milestones") and baseline is None:
            baseline = p
        if rec.get("type") == "milestone" and p.get("node_id"):
            done_nodes[p["node_id"]] = rec

    now = datetime.now(el.TZ)
    items = []
    first_upcoming_marked = False
    for node in (baseline or {}).get("milestones", []):
        rec = done_nodes.get(node["id"])
        if rec:
            status = "done"
        else:
            try:
                d = datetime.fromisoformat(node["date"]).replace(tzinfo=el.TZ)
                status = "done" if d <= now else "upcoming"
            except ValueError:
                status = "upcoming"
            if status == "upcoming" and not first_upcoming_marked:
                status = "current"  # 最近的未到期节点 = 当前阶段目标
                first_upcoming_marked = True
        items.append({**node, "status": status,
                      "evidence_id": rec["id"] if rec else None})
    return items


# ---------------------------------------------------------------------------
# 聚合视图
# ---------------------------------------------------------------------------

_chain_cache = {"mtime": None, "records": None}


def get_records() -> list:
    try:
        mtime = os.path.getmtime(el.CHAIN_PATH)
    except OSError:
        mtime = None
    if _chain_cache["records"] is None or _chain_cache["mtime"] != mtime:
        _chain_cache["records"] = el.load_chain()
        _chain_cache["mtime"] = mtime
    return _chain_cache["records"]


def overview() -> dict:
    records = get_records()
    metrics = compute_metrics(records)
    milestones = build_milestones(records)

    # 工期进度: T0 → T0+12 个月
    t0 = None
    for rec in records:
        p = rec.get("payload") or {}
        if rec.get("type") == "milestone" and p.get("t0"):
            t0 = p["t0"]
            break
    progress_pct = None
    if t0:
        try:
            start = datetime.fromisoformat(t0).replace(tzinfo=el.TZ)
            end = start.replace(year=start.year + 1)
            now = datetime.now(el.TZ)
            progress_pct = round(max(0.0, min(1.0, (now - start).total_seconds() /
                                                   (end - start).total_seconds())) * 100, 1)
        except ValueError:
            pass

    type_counts = {}
    for r in records:
        type_counts[r["type"]] = type_counts.get(r["type"], 0) + 1

    live = el.EvidenceLogger.read_live()
    live_status = "idle"
    if live:
        live_status = "running" if live.get("stage") in ("started", "training", "evaluating") \
            else "idle"

    latest = [summarize(r) for r in records[-8:]][::-1]

    # K1 最新成功曲线 (供看板 sparkline)
    curve = []
    for r in records:
        h = (r.get("payload") or {}).get("history")
        if isinstance(h, list):
            curve = h  # 取出现过的最新 history (live 记录亦会归档)
    if live and isinstance(live, dict) and isinstance(live.get("history"), list):
        curve = live["history"]

    return {
        "project": {
            "name": "低空具身智能体运动控制与全数字仿真技术研究",
            "code": "",
            "t0": t0,
            "progress_pct": progress_pct,
        },
        "metrics": metrics,
        "milestones": milestones,
        "counts": {"total": len(records), "by_type": type_counts},
        "live": {"status": live_status, "run": live},
        "latest_evidence": latest,
        "success_curve": curve,
        "server_time": el.now_iso(),
    }


def summarize(rec: dict) -> dict:
    return {"id": rec["id"], "seq": rec["seq"], "time": rec["time"],
            "type": rec["type"], "type_zh": el.RECORD_TYPES.get(rec["type"], rec["type"]),
            "actor": rec.get("actor"), "title": rec.get("title"),
            "hash": rec.get("hash"), "prev_hash": rec.get("prev_hash"),
            "artifact_count": len(rec.get("artifacts") or []),
            "payload": rec.get("payload") or {}}


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "EvidencePlatform/1.0"

    def log_message(self, fmt, *args):  # 安静模式: 仅错误
        if args and args[0].startswith(("GET /api", "POST /api")):
            return
        super().log_message(fmt, *args)

    # -- 工具 ---------------------------------------------------------------

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel: str):
        path = os.path.normpath(os.path.join(WEB_DIR, rel.lstrip("/")))
        if not path.startswith(WEB_DIR) or not os.path.isfile(path):
            self._json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # -- GET ----------------------------------------------------------------

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        try:
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path in ("/style.css", "/app.js"):
                self._static(path.lstrip("/"))
            elif path == "/api/overview":
                self._json(overview())
            elif path == "/api/evidence":
                self._json({"records": [summarize(r) for r in get_records()]})
            elif path.startswith("/api/evidence/"):
                eid = path.rsplit("/", 1)[-1]
                for r in get_records():
                    if r["id"] == eid:
                        self._json(r)
                        return
                self._json({"error": f"证据不存在: {eid}"}, 404)
            elif path == "/api/verify":
                self._json(el.verify_chain())
            elif path == "/api/live":
                live = el.EvidenceLogger.read_live()
                status = "idle"
                if live and live.get("stage") in ("started", "training", "evaluating"):
                    status = "running"
                self._json({"status": status, "run": live,
                            "server_time": el.now_iso()})
            else:
                self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # 防止单次请求异常打崩服务
            self._json({"error": str(e)}, 500)

    # -- POST ---------------------------------------------------------------

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/evidence":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                self._json({"error": "payload 过大"}, 413)
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            type_ = data.get("type", "note")
            title = (data.get("title") or "").strip()
            payload = data.get("payload") or {}
            if not title:
                self._json({"error": "标题不能为空"}, 400)
                return
            if type_ not in el.RECORD_TYPES:
                self._json({"error": f"类型不合法: {type_}"}, 400)
                return
            if not isinstance(payload, dict):
                self._json({"error": "payload 必须是对象"}, 400)
                return
            ev = el.EvidenceLogger(actor=data.get("actor") or "web-manual")
            rec = ev.log(type_, title, payload=payload)
            self._json({"ok": True, "record": summarize(rec)})
        except json.JSONDecodeError:
            self._json({"error": "JSON 解析失败"}, 400)
        except Exception as e:
            self._json({"error": str(e)}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if not os.path.isfile(os.path.join(WEB_DIR, "index.html")):
        print("[x] 缺少 web/index.html", file=sys.stderr)
        sys.exit(1)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print("=" * 60)
    print(" 训练证据链管理平台")
    print(f" 地址: {url}")
    print(f" 证据链: {el.CHAIN_PATH}")
    print(" Ctrl+C 停止")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
