# C1 · Step2c: 列 traj/env_gs_ecust 与 Image/env_gs_ecust 的目录层级,搞清真实组织。
import json
import subprocess
from pathlib import Path

DATA = Path(r"perception\data")
API = "https://hf-mirror.com/api/datasets/IPEC-COMMUNITY/OpenFly/tree/main"


def ls(p, limit=1000):
    url = f"{API}/{p}?limit={limit}"
    subprocess.run(["curl.exe", "-s", "--max-time", "60", "-o", str(DATA / "_p.json"), url],
                   check=True)
    return json.loads((DATA / "_p.json").read_text(encoding="utf-8", errors="replace"))


def show(items, n=15):
    files = [i for i in items if i.get("type") == "file"]
    dirs = [i for i in items if i.get("type") == "directory"]
    print(f"  dirs={len(dirs)} files={len(files)}; sample:")
    for it in dirs[:n]:
        print("   D", it["path"])
    for it in files[:n]:
        sz = (it.get("lfs") or {}).get("size", it.get("size", 0)) / 1e6
        print(f"   F {it['path'][-70:]} {sz:.2f} MB")


for p in ["traj/env_gs_ecust", "Image/env_gs_ecust"]:
    print("==", p)
    sub = ls(p)
    show(sub)
    # 下一层
    for it in [i for i in sub if i.get("type") == "directory"][:2]:
        print("--", it["path"])
        sub2 = ls(it["path"])
        show(sub2, 10)
        for it2 in [i for i in sub2 if i.get("type") == "directory"][:2]:
            print("   --", it2["path"])
            show(ls(it2["path"]), 8)
