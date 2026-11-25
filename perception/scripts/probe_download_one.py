# C1 · Step2d: 下载一条最小轨迹的 parquet + pose.jsonl,解析 schema 验证"帧内嵌 parquet"假设。
import json
import subprocess
from pathlib import Path

DATA = Path(r"perception\data")
BASE = "https://hf-mirror.com/datasets/IPEC-COMMUNITY/OpenFly/resolve/main"

traj_id = "2025-1-10_0-1-27_2016747294"
targets = [
    (f"traj/env_gs_ecust/astar_data/short/{traj_id}.parquet",
     DATA / "sample" / "env_gs_ecust" / "astar_data" / "short" / f"{traj_id}.parquet"),
    (f"Image/env_gs_ecust/astar_data/short/{traj_id}/pose.jsonl",
     DATA / "sample" / "env_gs_ecust" / "astar_data" / "short" / f"{traj_id}.pose.jsonl"),
]
for rel, dst in targets:
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl.exe", "-L", "-s", "--max-time", "600", "--retry", "3",
                        "-o", str(dst), f"{BASE}/{rel}"])
    print(rel, "->", dst.stat().st_size / 1e6, "MB" if r.returncode == 0 else "FAILED")
