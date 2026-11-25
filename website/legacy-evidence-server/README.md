# legacy-evidence-server —— 前期原型归档（非展示站交付物）

> 归档时间：2026-08-28 · 归档人：轨道F主任会话

## 是什么

轨道协同计划（`项目分工与协同计划.md`）建立**之前**，本会话按用户原始需求
（"训练要有证据链 + 记录/验证/看板/指标/训练实况"）在**项目根目录**搭建的
动态证据链平台原型。为遵守 §4 所有权规则（"根目录只放 E 维护的全局文档"、
轨道F 只写 `website/`），整体迁入本目录存档，**不属于**纯静态展示站的构建产物。

## 内容

| 文件/目录 | 说明 |
|-----------|------|
| `evidence_logger.py` | SHA-256 哈希链证据记录 SDK（追加写、防篡改、文件锁） |
| `evidence_server.py` | stdlib HTTP 服务（端口 8600）+ 指标派生（K1~K6） |
| `seed_evidence.py` | 既有真实成果导入脚本（7 条证据，含哈希链） |
| `train_ppo_evidence.py` | 训练脚本接入证据链示例（每 5 万步回写实况） |
| `web/` | 原型前端（HUD 风格，5 页签） |
| `evidence/` | 已生成的哈希证据链 `chain.jsonl` + 产物快照 |

## 处置说明

- 展示站 v1（`website/` 根目录）按 `interfaces/website-data-contract.md`
  以**纯静态 + 构建期快照**方式覆盖了"看板 / 成果 / 记录指纹 / 指标对照"需求；
- 哈希证据链、训练实况推送（`evidence/live/`）属于跨轨道能力，已在
  `coordination/mailbox-E.md` 留言请 PM 仲裁是否立为正式契约；
- 如需运行原型：`python evidence_server.py --port 8600`（依赖仅 Python 标准库；
  训练接入另需 rl_train 依赖，由轨道A环境决定）。
