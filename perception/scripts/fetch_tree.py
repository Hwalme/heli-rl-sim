# C1 · Step1: 通过 hf-mirror 递归抓取 OpenFly 数据集文件树,统计各目录大小,为 1% 子集选型做磁盘估算。
# 只读 API,不下载任何数据文件。
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
API = "https://hf-mirror.com/api/datasets/IPEC-COMMUNITY/OpenFly/tree/main"


def fetch_tree():
    all_files = []
    url = API + "?recursive=true&limit=1000"
    pages = 0
    fails = 0
    tmp_body = DATA_DIR / "_tree_page.json"
    tmp_head = DATA_DIR / "_tree_head.txt"
    out_path = DATA_DIR / "_openfly_tree.json"
    # 断点续传:已有产出则跳过已完成页数(简化:整体重跑也可,但慢;这里记录进度)
    while url and pages < 3000:
        try:
            subprocess.run(
                ["curl.exe", "-s", "--max-time", "90", "-D", str(tmp_head),
                 "-o", str(tmp_body), url],
                check=True, capture_output=True,
            )
            fails = 0
        except subprocess.CalledProcessError:
            fails += 1
            if fails >= 5:
                raise
            print(f"  page {pages+1} fetch failed ({fails}/5), retry in 5s", flush=True)
            import time
            time.sleep(5)
            continue
        body = tmp_body.read_text(encoding="utf-8", errors="replace")
        items = json.loads(body)
        if not items:
            url = None
            break
        for it in items:
            if it.get("type") == "file":
                lfs = it.get("lfs")
                all_files.append({
                    "path": it["path"],
                    "size": it.get("size", 0),
                    "lfs_size": lfs.get("size") if lfs else None,
                })
        head = tmp_head.read_text(encoding="utf-8", errors="replace")
        link = None
        for line in head.splitlines():
            low = line.lower()
            if low.startswith("link:") and 'rel="next"' in low:
                link = line.split("<", 1)[1].split(">", 1)[0]
        url = link  # Link 头里已是完整 URL,但域名是上游 huggingface.co,替换为镜像
        if url:
            url = url.replace("https://huggingface.co", "https://hf-mirror.com")
        pages += 1
        if pages % 5 == 0:
            # 增量落盘,防中断丢进度
            out_path.write_text(
                json.dumps(all_files, ensure_ascii=False), encoding="utf-8")
            print(f"  page {pages}, files so far: {len(all_files)}", flush=True)
    out_path.write_text(
        json.dumps(all_files, ensure_ascii=False), encoding="utf-8")
    return all_files


def eff_size(f):
    return f["lfs_size"] if f["lfs_size"] is not None else f["size"]


def main():
    files = fetch_tree()
    if not files:
        print("ERROR: empty file tree")
        sys.exit(1)
    (DATA_DIR / "_openfly_tree.json").write_text(
        json.dumps(files, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(eff_size(f) for f in files)
    print(f"TOTAL_FILES: {len(files)}")
    print(f"TOTAL_SIZE : {total/1e9:.2f} GB ({total/2**30:.2f} GiB)")

    # 按顶层目录聚合
    agg = {}
    for f in files:
        parts = f["path"].split("/")
        key = parts[0] if len(parts) > 1 else "(root)"
        agg[key] = agg.get(key, [0, 0])
        agg[key][0] += 1
        agg[key][1] += eff_size(f)
    print("\n== 顶层目录统计 ==")
    for key, (cnt, size) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print(f"{key:60s} files={cnt:6d} size={size/1e9:8.2f} GB")

    # 按扩展名聚合
    exts = {}
    for f in files:
        ext = Path(f["path"]).suffix.lower() or "(none)"
        exts[ext] = exts.get(ext, [0, 0])
        exts[ext][0] += 1
        exts[ext][1] += eff_size(f)
    print("\n== 扩展名统计 ==")
    for ext, (cnt, size) in sorted(exts.items(), key=lambda kv: -kv[1][1])[:15]:
        print(f"{ext:12s} files={cnt:6d} size={size/1e9:8.2f} GB")


if __name__ == "__main__":
    main()
