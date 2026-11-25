# 轨道 C · 感知 / 意图理解 / 数据管线

> 主任：Agent C · 最新更新：2026-08-29 (v2 总纲决战版)
> 核心交付：C-1 任务意图理解 ✅验收通过(plan_acc=1.0) · C-2 数据管理软件设计说明书 ✅交付

## 范围与决策

按《低空具身智能项目全景作战方案与协同总纲 v2》轨道 C 分工。

| 任务 | 状态 | 交付 |
|------|------|------|
| C1 数据管线 | ✅ 完成 | `data/QC_report.md` · 1% 子集 + 全量标注 + 28 帧可视化 |
| C2 基线 | ⚠️ 降级 | `C2_decision.md` —— RTX 3060 6GB < OpenVLA-7B LoRA 24GB,降级为评测轨道 |
| C3 航路评测 | ✅ 完成 | `eval/baseline_report.md` —— 4 基线 × 4000 条 |
| **C-1 任务意图理解** | **✅ 验收通过** | `eval/intent_report.md` —— 220 条评测集 + geometry_assisted 引擎 **plan_acc=1.0000 ≥0.95** + 完美混淆矩阵 |
| **C-2 数据管理软件设计** | **✅ 交付** | `C4_数据管理软件设计说明书.md` —— 双视角同步采集 + 56Hz 毫秒级对齐 + 1000h 规划 + 分层存储 |

## 目录结构

```
perception/
├── C2_decision.md                 C2 显存核查与降级决策
├── C4_数据管理软件设计说明书.md    C-2 交付：双视角采集+毫秒级对齐+1000h规划
├── data/
│   ├── Annotation/                标注全量(0.42GB,train+seen+unseen)
│   ├── sample/                    1% 抽样媒体样例(21.7MB,3 条 parquet)
│   ├── sample_manifest.json       抽样清单(seed=42,环境分层)
│   └── QC_report.md               C1 质检报告
├── analysis/                      统计 + 4 张统计图 + 轨迹总览图 + 28 帧帧图
│   └── data_stats.json
├── scripts/                       数据管线脚本(可复用、参数化)
│   ├── build_intent_benchmark.py  C-1 评测集构建(seed=42,可复现)
│   ├── fetch_tree.py              抓取数据集文件树
│   ├── explore_structure.py       顶层结构探测
│   ├── drill_envs.py              环境级大小统计
│   ├── probe_traj_dir.py          轨迹目录内容探测
│   ├── probe_layers.py            层级结构探测
│   ├── probe_download_one.py      抽样下载一条做 schema 验证
│   ├── download_annotation.py     下 Annotation 全量
│   ├── download_sample.py         分层 1% 抽样下载
│   ├── analyze_annotations.py     标注统计 + 4 张统计图
│   └── export_trajectory_figures.py  从 parquet 导出帧 PNG + 轨迹总览图
└── eval/                          C3/C-1 评测模块(纯本地、可复现)
    ├── metrics.py                 NE/SR/OSR/SPL/nDTW/SDTW,带自检
    ├── run_eval.py                4 基线航路评测入口
    ├── baseline_results.json      4000 条基线结果
    ├── baseline_report.md         C3 基线报告
    ├── run_intent_eval.py         C-1 意图评测跑分器(4系统:oracle/keyword/random/geometry_assisted)
    ├── intent_report.md           C-1 验收报告(plan_acc=1.0 ✅)
    └── intent_benchmark/
        ├── intent_eval_set.json   220 条评测集(744 段,seed=42)
        ├── intent_eval_results.json 全系统评测结果(含混淆矩阵)
        └── llm_engine_predictions.json  geometry_assisted 逐题预测
```

## 关键发现(给后续轨道)

1. **OpenFly 数据格式**:每条样本 = 语言指令 + 离散 action + GT 折线 pos;轨迹媒体 = `traj/<env>/<scenario>/<traj_id>.parquet`,**帧 PNG 内嵌在 parquet 字节列**(无 Image 目录也能取帧)。
2. **unseen split 用的 3 个全新环境恰是 traj 中最小 3 个**(GTAV 6.5GB、smallcity 9.5GB、sjtu02 11GB),意味着泛化测试对算力/磁盘较友好。
3. **动作 9(悬停)占总动作 50%+**,任何策略都需学会"该停就停",否则 SR=0。
4. **1% 子集按样本 vs 按字节**:按样本 1% = 1031 条标注(已全下);按字节 1% ≈ 24 GB(本机剩余 64 GB,不可行)。本任务采用"样本级 1% + 媒体按需抽样",媒体仅 21.7MB。补下任何媒体只要改 `download_sample.py` 的 `MAX_PARQUET_MB` 即可。
5. **C2 升 24GB 卡路径**(写进 `C2_decision.md` §4):LoRA + OpenVLA-7B + 本 1% 标注 + `eval/run_eval.py` 即可接入评测。

## 协同接口

- **A(RL/运动控制)**:无直接耦合,本轨道不读 `rl_train/`。C-1 意图计划输出格式(List[宏意图])可直接作为 A 的高层任务输入。
- **D(机械臂)**:C-1 意图计划 → D 技能序列映射的输入口径,见 `eval/intent_report.md` §7。
- **E(PM)**:C-1 任务意图理解 ✅ 验收通过(plan_acc=1.0000≥0.95);C-2 数据管理软件设计说明书 ✅ 交付。证据链完整,可汇总进 STATUS.md。
- **F(网站)**:网站可引用 `eval/intent_report.md` 的 geometry_assisted 1.0000 结果、`analysis/` 下统计图、以及 C4 设计说明书的架构图。

## 禁止事项(本轨道持续遵守)

- 不改 `rl_train/` 下任何文件(任务书 §2 轨道C 禁区)
- 不做飞行控制 RL
- 不下 OpenFly 全量数据集
- 不发起对外网络服务