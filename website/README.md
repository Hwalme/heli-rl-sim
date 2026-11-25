# website/ —— 项目展示站（轨道F）

低空具身智能项目对外展示站：**技术路线介绍 + M0 成果展示 + 进度面板 + 验收指标对照**。

## 特性

- **纯静态、零依赖、零构建工具链**：纯 HTML/CSS/原生 JS，无 CDN、无 npm，
  构建后双击 `index.html`（file://）即可打开，内网/离线可用；
- **数据只读 + 指纹固证**：所有数字来自 `rl_train/results/*_results.json`
  与指定文档摘录（出处页面均有标注）；构建时为每份数据计算 SHA-256 指纹，
  网站展示的每个数字可溯源、可校验——这就是本站的"证据链"；
- **禁止自造数据**：缺口一律挂 `data-requests.md` 工单，页面如实显示"待验证"。

## 使用

```bash
# 1. 构建数据快照（每次 rl_train/results 更新后重跑；仅 Python 标准库）
python build.py

# 2. 查看：直接双击 index.html；或起本地静态服务
python -m http.server 8700 --directory website
#    浏览器打开 http://127.0.0.1:8700
```

## 目录结构

```
website/
├── index.html            # 单页站：总览看板/技术路线/成果展示/进度面板/指标对照
├── assets/
│   ├── style.css         # HUD 暗色主题（离线内联，无外部字体/CDN）
│   └── app.js            # 渲染逻辑（KPI 卡、SVG 曲线、记录表、时间线）
├── data/
│   └── data.js           # build.py 生成的数据快照（window.__SITE_DATA__）
├── build.py              # 数据快照构建脚本（只读消费 results + 文档摘录）
├── data-requests.md      # 数据缺口工单（F 写 / 全体可回填）
├── legacy-evidence-server/  # 前期动态证据链原型归档（非交付物，见其 README）
└── README.md             # 本文件
```

## 数据流

```
rl_train/results/*_results.json ──┐
项目分工与协同计划.md §0 里程碑 ──┼─→ build.py ─→ data/data.js ─→ index.html
coordination/STATUS.md 轨道状态 ──┘   (SHA-256)      (快照+指纹)
```

## 数据契约

遵循 `interfaces/website-data-contract.md`（website-data v1）。本目录内
`data-requests.md` 为轨道F唯一对外数据需求通道；跨轨道沟通走
`coordination/` 文件邮箱。维护人：轨道F主任会话。
