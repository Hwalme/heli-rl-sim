# 契约：网站只读数据源（website-data v1）

- **所有者**：轨道 E（代管）；**消费方**：轨道 F（网站）
- **状态**：**v1.1**（2026-08-28：合法数据源扩至 `verification/` 复验产物；已登记 `interfaces/VERSIONS.md` #3）
- **原则**：网站只读，禁止修改任何研究文件；数据缺口写 `website/data-requests.md`；对外数字逐字出自数据文件并附路径。

## 1. 训练结果（唯一权威数据源）

路径模式：`rl_train/results/*_results.json`，schema（v1）：

```json
{
  "mode": "direct | arm",
  "steps": 1000000,
  "train_seconds": 199.0,
  "steps_per_second": 5024,
  "success_rate": 1.0,
  "crash_rate": 0.0,
  "mean_alt_err_m": 6.8
}
```

当前已有：`rl_train/results/helo_ppo_results.json`
（direct / 1e6 步 / 199s / 成功率 100% / 坠毁 0% / 平均高度误差 6.80m）

## 2. 可摘录的文档与复验产物

- `训练方法与坠机诊断报告.md`（项目技术叙事 + 方法综述，摘录需注明出处）
- `项目分工与协同计划.md` §0 里程碑表（进度面板数据源）
- `verification/` 复验产物（只读，v1.1 新增合法源）：`VERIFICATION_REPORT.md`、`independent_eval_results.json`、`latency_results.json` —— 高保真口径（多种子动作达成、延迟 P99）的合法出处

## 3. 禁止

- 读取各轨道 `data/` 下的大文件（数据集/权重）进网站仓库；
- 在数据缺失时编造数字；一律提 `data-requests.md` 工单。
