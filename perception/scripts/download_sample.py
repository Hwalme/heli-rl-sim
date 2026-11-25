# -*- coding: utf-8 -*-
"""C1 · Step3b: 1% 分层抽样 + 媒体样例下载。

抽样定义(写死,保证可复现,seed=42):
  - 标注层:seen/unseen/train 三个 split 全量已下载(0.42GB,数据集骨架);
  - 样本级:每个 split 抽 1%(max(1, round(n*0.01))),按环境分层比例分配;
  - 媒体层:仅 seen+unseen 抽中样本对应的 traj parquet(≤15MB,至多 12 条)落盘,
    train 抽中样本只记录引用清单 —— 原因:train 媒体 1% ≈ 1000 条 × ~20MB ≈ 20GB,
    超出本机磁盘预算(E 盘剩余 ~64GB,须为后续微调/系统留余量)。
输出:
  perception/data/sample_manifest.json
  perception/data/sample/<env>/<scenario>/<traj_id>.parquet   媒体样例
"""
import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ANN = DATA / "Annotation"
SAMPLE = DATA / "sample"
BASE = "https://hf-mirror.com/datasets/IPEC-COMMUNITY/OpenFly/resolve/main"
MAX_MEDIA = 12          # 最多下载的 parquet 条数
MAX_PARQUET_MB = 15     # 单条 parquet 大小上限
SEED = 42


def load(name):
    with (ANN / f"{name}.json").open(encoding="utf-8") as f:
        return json.load(f)


def env_of(item):
    return item["image_path"].split("/")[0]


def stratified_sample(items, ratio=0.01):
    """按环境分层比例抽样;每环境至少 1 条;顺序打乱后按原相对顺序返回。"""
    rng = random.Random(SEED)
    by_env = defaultdict(list)
    for idx, it in enumerate(items):
        by_env[env_of(it)].append(idx)
    picked = []
    envs = sorted(by_env)
    quota_total = max(1, round(len(items) * ratio))
    quota = {e: max(1, round(len(by_env[e]) / len(items) * quota_total)) for e in envs}
    # 修正配额和,避免超抽
    while sum(quota.values()) > quota_total:
        e = max(quota, key=lambda k: quota[k])
        quota[e] = max(1, quota[e] - 1)
        if quota[e] == 1:
            break
    for e in envs:
        idxs = by_env[e][:]
        rng.shuffle(idxs)
        picked.extend(idxs[:quota[e]])
    return sorted(picked), quota


def fetch(rel, dst, tries=3):
    dst.parent.mkdir(parents=True, exist_ok=True)
    for a in range(tries):
        r = subprocess.run(["curl.exe", "-L", "-s", "--max-time", "900", "--retry", "2",
                            "-o", str(dst), f"{BASE}/{rel}"])
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return dst.stat().st_size
        time.sleep(4)
    return None


def main():
    manifest = {"seed": SEED, "ratio": 0.01, "endpoint": "hf-mirror.com",
                "splits": {}, "media_downloaded": []}
    parquet_queue = []
    for name in ("train", "seen", "unseen"):
        items = load(name)
        idxs, quota = stratified_sample(items)
        recs = []
        for i in idxs:
            it = items[i]
            recs.append({
                "split": name, "idx": i,
                "image_path": it["image_path"],
                "traj_parquet": f"traj/{it['image_path']}.parquet",
                "instruction": it["gpt_instruction"][:200],
                "n_steps": len(it.get("action", [])),
            })
        manifest["splits"][name] = {"n_total": len(items), "n_sampled": len(recs),
                                    "env_quota": quota, "records": recs}
        print(f"{name}: {len(items)} -> {len(recs)} sampled", flush=True)
        if name != "train":
            for r in recs:
                parquet_queue.append(r)

    # 媒体下载(seen/unseen 样例)
    ok, skipped_big, failed = 0, 0, 0
    for r in parquet_queue[:MAX_MEDIA]:
        rel = r["traj_parquet"]
        dst = SAMPLE / rel.split("traj/", 1)[1]
        size = fetch(rel, dst)
        if size is None:
            failed += 1
            print(f"  FAILED {rel}", flush=True)
            continue
        if size > MAX_PARQUET_MB * 1_000_000:
            dst.unlink(missing_ok=True)
            skipped_big += 1
            continue
        ok += 1
        r["media_local"] = str(dst.relative_to(DATA))
        r["media_bytes"] = size
        manifest["media_downloaded"].append(r["media_local"])
        print(f"  OK {rel} {size/1e6:.1f} MB", flush=True)
    print(f"media: ok={ok} skipped_big={skipped_big} failed={failed}")

    (DATA / "sample_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print("manifest ->", DATA / "sample_manifest.json")
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
