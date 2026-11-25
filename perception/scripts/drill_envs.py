# C1 · Step1c: 钻取环境级文件清单 —— traj/ 全部环境递归统计、Image/ 环境一层探测 + 样例环境递归统计。
# 产出:per-env 大小表,作为 1% 子集选型依据。只读 API,不下数据。
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


def list_dir(path, recursive=False, max_pages=800):
    entries = []
    base = f"{API}/{path}" if path else API
    url = base + "?limit=1000" + ("&recursive=true" if recursive else "")
    pages = 0
    while url and pages < max_pages:
        items, url = curl_json(url)
        if not items:
            break
        entries.extend(items)
        pages += 1
        if pages % 25 == 0:
            print(f"    [{path}] page {pages}, entries {len(entries)}", flush=True)
    return entries


def files_of(entries):
    out = []
    for i in entries:
        if i.get("type") == "file":
            out.append({"path": i["path"],
                        "bytes": (i.get("lfs") or {}).get("size", i.get("size", 0))})
    return out


def main():
    result = {"traj": {}, "image": {}}

    # Annotation 文件明细
    ann = files_of(list_dir("Annotation"))
    result["annotation_files"] = ann
    print("== Annotation files ==")
    for f in ann:
        print(f"  {f['path']}  {f['bytes']/1e6:.1f} MB")

    # traj/ 每环境递归(轨迹文件应该不多)
    print("== traj/ per-env recursive ==")
    for env in sorted(json.loads((DATA_DIR / "_openfly_structure.json").read_text(encoding="utf-8"))
                      ["Image"]["subdirs"]):
        env_name = env.split("/")[-1]
        fl = files_of(list_dir(f"traj/{env_name}", recursive=True))
        tb = sum(f["bytes"] for f in fl)
        result["traj"][env_name] = {"n_files": len(fl), "bytes": tb}
        print(f"  traj/{env_name}: files={len(fl)} {tb/1e9:.3f} GB", flush=True)
        (DATA_DIR / "_openfly_env_sizes.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    # Image/ 每环境一层
    print("== Image/ per-env shallow ==")
    for env in sorted(result["traj"]):
        sub = list_dir(f"Image/{env}")
        fl = files_of(sub)
        ndir = sum(1 for i in sub if i.get("type") == "directory")
        tb = sum(f["bytes"] for f in fl)
        result["image"][env] = {"n_files_level1": len(fl), "n_dirs_level1": ndir,
                                "bytes_level1": tb}
        print(f"  Image/{env}: L1 files={len(fl)} dirs={ndir} direct={tb/1e9:.3f} GB", flush=True)
        (DATA_DIR / "_openfly_env_sizes.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print("DONE")


if __name__ == "__main__":
    main()
