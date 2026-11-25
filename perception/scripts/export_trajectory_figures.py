# -*- coding: utf-8 -*-
"""C1 · Step4: 从已下载的媒体样例导出可视化。

输入 : perception/data/sample_manifest.json + data/sample/**/*.parquet
输出 : perception/analysis/frames/<traj_id>_f{0,mid,last}.png   样例帧
       perception/analysis/fig_traj_overview.png                轨迹俯视总览(含 GT 航点)
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SAMPLE = DATA / "sample"
OUT = ROOT / "analysis"
FRAMES = OUT / "frames"
FRAMES.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False


def load_manifest():
    return json.loads((DATA / "sample_manifest.json").read_text(encoding="utf-8"))


def find_annotation(manifest, media_rec):
    """在对应 split 里找 image_path 匹配的标注样本(含完整 pos 航点)。"""
    split = media_rec["split"]
    items = json.loads((DATA / "Annotation" / f"{split}.json").read_text(encoding="utf-8"))
    for it in items:
        if it["image_path"] == media_rec["image_path"]:
            return it
    return None


def export_frames(parquet_path, traj_id, picks=(0, -2, -1)):
    """导出首/中/末三帧 PNG;返回帧内 pos 序列。"""
    t = pq.read_table(parquet_path)
    n = t.num_rows
    frames_dir = FRAMES / traj_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    positions = [list(t.column("pos")[i].as_py()) for i in range(n)]
    for tag, idx in zip(("first", "mid", "last"),
                        (picks[0], max(0, n // 2) if n > 2 else picks[1], n - 1)):
        img = t.column("image")[idx].as_py()
        png = img["bytes"] if isinstance(img, dict) else img[0]
        (frames_dir / f"f{idx}_{tag}.png").write_bytes(png)
    return positions


def main():
    manifest = load_manifest()
    recs = [r for r in manifest["splits"]["seen"]["records"]
            + manifest["splits"]["unseen"]["records"] if "media_local" in r]
    print(f"media records: {len(recs)}")

    n = len(recs)
    cols = 3
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 3.8))
    axes = axes.flatten()

    for k, r in enumerate(recs):
        pq_path = DATA / r["media_local"]
        traj_id = Path(r["media_local"]).stem
        # 帧导出
        positions = export_frames(pq_path, traj_id)
        # 航点(GT 标注)叠加俯视图
        ax = axes[k]
        ax.plot([p[0] for p in positions], [p[1] for p in positions],
                "-", lw=1, alpha=0.6, label="parquet 帧位姿")
        ann = find_annotation(manifest, r)
        if ann and ann.get("pos"):
            ax.plot([p[0] for p in ann["pos"]], [p[1] for p in ann["pos"]],
                    "--", lw=1.4, color="#c0392b", label="标注航点")
            ax.plot(ann["pos"][0][0], ann["pos"][0][1], "g^", ms=8, label="起点")
            ax.plot(ann["pos"][-1][0], ann["pos"][-1][1], "r*", ms=10, label="目标")
        ax.set_title(f"{r['split']}:{traj_id[:22]}", fontsize=8)
        ax.set_aspect("equal")
        ax.tick_params(labelsize=6)
        if k == 0:
            ax.legend(fontsize=6)
        print(f"  {r['split']} {traj_id}: {len(positions)} frames exported")

    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle("OpenFly 1% 子集抽样轨迹俯视总览(实线=帧位姿,虚线=标注航点)", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "fig_traj_overview.png", dpi=130)
    print("overview ->", OUT / "fig_traj_overview.png")
    print("DONE")


if __name__ == "__main__":
    main()
