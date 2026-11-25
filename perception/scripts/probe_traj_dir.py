# C1 · Step2b: 探测单个轨迹目录与对应 Image 目录的内容与文件大小。
import json
import subprocess
from pathlib import Path

DATA = Path(r"perception\data")
API = "https://hf-mirror.com/api/datasets/IPEC-COMMUNITY/OpenFly/tree/main"


def ls(p, limit=50):
    url = f"{API}/{p}?limit={limit}"
    subprocess.run(["curl.exe", "-s", "--max-time", "60", "-o", str(DATA / "_p.json"), url],
                   check=True)
    return json.loads((DATA / "_p.json").read_text(encoding="utf-8", errors="replace"))


def show(items, n=12):
    for it in items[:n]:
        sz = (it.get("lfs") or {}).get("size", it.get("size", 0)) / 1e6
        print(f"  {it['type']:5s} {it['path'][-66:]:66s} {sz:9.2f} MB")


traj_dir = "Image/env_gs_ecust/astar_data/long/2025-1-10_11-53-17_719885386"
img_dir = "Image/env_gs_ecust/astar_data/short/2025-1-10_0-0-39_972605807"
for d in (traj_dir, img_dir):
    resp = ls(d)
    print("==", d)
    if isinstance(resp, dict):
        print("  RAW:", json.dumps(resp, ensure_ascii=False)[:500])
    else:
        show(resp, 8)
