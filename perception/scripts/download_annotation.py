# C1 · Step2a: 下载 Annotation 全量(0.42GB,数据集标注骨架)。
# HF_ENDPOINT 走 hf-mirror;curl 直下(LFS resolve 重定向由 -L 跟随)。
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
BASE = "https://hf-mirror.com/datasets/IPEC-COMMUNITY/OpenFly/resolve/main"

FILES = [
    "Annotation/statistc.py",
    "Annotation/seen.json",
    "Annotation/unseen.json",
    "Annotation/train.json",
]


def fetch(rel: str) -> None:
    dst = DATA_DIR / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{rel}"
    for attempt in range(3):
        print(f"downloading {rel} (attempt {attempt+1}) ...", flush=True)
        r = subprocess.run(["curl.exe", "-L", "-s", "--max-time", "1800",
                            "--retry", "3", "-o", str(dst), url])
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            mb = dst.stat().st_size / 1e6
            print(f"  OK {mb:.1f} MB", flush=True)
            return
        time.sleep(5)
    print(f"  FAILED {rel}", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    for f in FILES:
        fetch(f)
    print("ALL DONE")
