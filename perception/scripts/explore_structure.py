# C1 · Step1b: 分目录抓取 OpenFly 结构 —— 顶层 → 二级 → 每个二级目录的文件汇总。
# 目的:找到"环境"粒度的文件数/大小分布,为 1% 子集选型做磁盘估算。只读 API。
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
API = "https://hf-mirror.com/api/datasets/IPEC-COMMUNITY/OpenFly/tree/main"


def curl_json(url, tries=4):
    tmp_body = DATA_DIR / "_p.json"
    tmp_head = DATA_DIR / "_h.txt"
    for i in range(tries):
        r = subprocess.run(["curl.exe", "-s", "--max-time", "60", "-D", str(tmp_head),
                            "-o", str(tmp_body), url], capture_output=True)
        if r.returncode == 0:
            try:
                items = json.loads(tmp_body.read_text(encoding="utf-8", errors="replace"))
                link = None
                for line in tmp_head.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.lower().startswith("link:") and 'rel="next"' in line.lower():
                        link = line.split("<", 1)[1].split(">", 1)[0]
                if link:
                    link = link.replace("https://huggingface.co", "https://hf-mirror.com")
                return items, link
            except json.JSONDecodeError:
                pass
        time.sleep(2 + i)
    raise RuntimeError(f"fetch failed: {url}")


def list_dir(path, recursive=False, max_pages=400):
    """列出目录下条目(文件+目录),处理翻页。"""
    entries = []
    base = f"{API}/{path}" if path else API
    url = base + "?limit=1000" + ("&recursive=true" if recursive else "")
    pages = 0
    while url and pages < max_pages:
        items, url = curl_json(url)
        entries.extend(items)
        pages += 1
        if pages % 20 == 0:
            print(f"    [{path}] page {pages}, entries {len(entries)}", flush=True)
    return entries


def summarize(items):
    files = [i for i in items if i.get("type") == "file"]
    dirs = [i for i in items if i.get("type") == "directory"]
    total = sum((i.get("lfs") or {}).get("size", i.get("size", 0)) for i in files)
    return files, dirs, total


def main():
    # 1) 顶层
    top = list_dir("")
    files, dirs, _ = summarize(top)
    print("== TOP ==")
    for d in dirs:
        print("  DIR ", d["path"])
    for f in files:
        sz = (f.get("lfs") or {}).get("size", f.get("size", 0))
        print(f"  FILE {f['path']}  {sz/1e6:.1f} MB")

    # 2) 每个顶层目录的二级结构
    structure = {}
    for d in dirs:
        lvl2 = list_dir(d["path"])
        f2, d2, sz2 = summarize(lvl2)
        structure[d["path"]] = {"n_files": len(f2), "n_dirs": len(d2), "bytes_files": sz2,
                                "subdirs": [x["path"] for x in d2]}
        print(f"== {d['path']}: files={len(f2)} dirs={len(d2)} direct_bytes={sz2/1e9:.2f}GB")
        for x in d2[:40]:
            print("     sub:", x["path"])
        (DATA_DIR / "_openfly_structure.json").write_text(
            json.dumps(structure, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE")


if __name__ == "__main__":
    main()
