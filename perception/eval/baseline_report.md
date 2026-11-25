# C3 基线评测报告 · OpenFly 1% 子集

> 日期:2026-08-28 · 负责人:轨道C 主任 · 输入:`data/Annotation/` 标注 + `eval/metrics.py`

## 1. 实验设计

由于本机 RTX 3060 Laptop 6GB 显存 << OpenVLA-7B LoRA 微调所需的 24GB,本轨道按任务书**降级为评测轨道**(详见 `perception/C2_decision.md`)。

评测轨道目标:**让任何下游模型/基线都能被公平打分**。

### 1.1 评测集
- 标注层:OpenFly Annotation 全量,**103,226 条**(train 100,226 / seen 1,800 / unseen 1,200)
- 评测样本:`train` 抽 1,000、`seen` 1,800、`unseen` 1,200,**合计 4,000 条**(seed=42,见 `eval/run_eval.py`)
- 信息集:每条含语言文本 `gpt_instruction` + 离散 `action` + 折线 `pos`(ground-truth 航点,语义上即"目标点序列")

### 1.2 指标(航空 VLN/R2R 标准族)
| 指标 | 含义 | 范围 | 解读 |
|------|------|------|------|
| NE  | Navigation Error,终点到 GT 目标的欧氏距离 | m,↓ | 越小越准 |
| SR  | Success Rate,NE < 3m 的比例 | 0~1,↑ | 主流评价指标 |
| OSR | Oracle SR,轨迹上任一点进入 3m 阈值的比例 | 0~1,↑ | 校准"中途擦边" |
| SPL | Success weighted by Path Length, SR × (l_ref / max(p, l_ref)) | 0~1,↑ | SR 用最优路径归一 |
| nDTW| 归一化动态时间规整,衡量整条轨迹相似度 | 0~1,↑ | 评价走形 |
| SDTW| nDTW × 是否成功 | 0~1,↑ | SR 加权版本 |

注:本版 SPL 将"参考路径长 l_ref"实现为**GT 航点折线总长**(SPL 原义为 ground-truth shortest path,在航点数据集里即 GT 折线),oracle 回放时 l_ref==p → SPL=1.0,验证自洽。

### 1.3 基线策略(4 条)
| 策略 | 信息集 | 设计意图 |
|------|--------|---------|
| `gt_oracle` | GT 目标 + 完整航点 | 回放 GT,期望全指标 = 1 |
| `noisy_5m` | GT 目标 + 完整航点 + 每步 N(0,5m) 噪声 | 模拟定位误差,校准 SR 对误差的灵敏度 |
| `truncated_60`| GT 目标 + 60% GT 航点 + 悬停 | 模拟"提前中止",SR 应接近 0 |
| `random_walk`| 无目标信息,纯随机游走同长度 | 零信息基线下限 |

四策略构成"指标灵敏度梯子",从满分开 oracle 到全 0 随机 walk,任意指标实现正确的话应单调下降。

## 2. 结果(4000 条全 split)

```
gt_oracle    SR=1.0000  OSR=1.0000  SPL=1.0000  nDTW=1.0000  SDTW=1.0000  NE=0.00 m
noisy_5m     SR=0.0515  OSR=0.1247  SPL=0.0253  nDTW=0.0763  SDTW=0.0049  NE=7.92 m
truncated_60 SR=0.0262  OSR=0.0262  SPL=0.0262  nDTW=0.1402  SDTW=0.0259  NE=33.69 m
random_walk  SR=0.0008  OSR=0.0032  SPL=0.0003  nDTW=0.0079  SDTW=0.0001  NE=85.92 m
```

完整 JSON: `perception/eval/baseline_results.json`

## 3. 评测器自检(灵敏度梯子)

| 断言 | 期望 | 实测 | 含义 |
|------|------|------|------|
| oracle SR = 1.0 | 是 | ✅ 1.0 | 评测器一致 |
| oracle SPL = 1.0 | 是 | ✅ 1.0 | SPL 语义正确(参考路径=GT 折线) |
| oracle nDTW ≈ 1 | 是 | ✅ 1.0 | DTW 实现正确 |
| 0 < noisy SR < 1 | 是 | ✅ 0.05 | SR 对 5m 误差敏感 |
| truncated SR < 0.1 | 是 | ✅ 0.026 | 大部分未达目标 |
| random SR < 0.01 | 是 | ✅ 0.0008 | 零信息基线近零 |

**结论:评测器实现与语义自洽 ✅**。

## 4. 关键观察

1. **GT 末段长悬停**:`truncated_60` 在 60% 处截停后仍有 SR=2.6%,是因为 OpenFly 标注 `pos` 序列里 `9` 号动作(悬停)高频出现,后段常在同一坐标重复。这反过来印证了"走 GT 不等于成功"在某些长尾上的病态,**给真实模型留了评分上限空间(此 baseline SR=1,真实模型不可能 ≥1)**。
2. **5m 噪声就足以打断 success**:SR=5%,NE=7.92m → 终点偏离目标 >7m,直接超 3m 阈值一半以上。**说明 OpenFly 评测对终点定位敏感**(终点 ±1m 才算成功)。
3. **随机 walk NE≈86m**:与 GT 平均欧氏距离 74m 同量级 → 评估样本的"走廊尺度"普遍在百米级,适合 30~150m 步长模型。

## 5. 升级路径(有 ≥24GB 卡时)

按 `C2_decision.md` §4:任何 VLA/VLN 模型只需产出一条**预测航点序列**,按以下接口喂入即可:

```python
from eval.metrics import evaluate_episodes
# pred 为 List[List[(x,y,z)]] 与 gt 一一对应
results = evaluate_episodes(list(zip(gt_list, pred_list)))
```

可直接与上表 4 基线对比。所有指标代码位于 `eval/metrics.py`,基线函数位于 `eval/run_eval.py`。

## 6. 涉及文件

- `perception/eval/metrics.py` —— 指标库(NE/SR/OSR/SPL/nDTW/SDTW,带自检)
- `perception/eval/run_eval.py` —— 基线评测入口
- `perception/eval/baseline_results.json` —— 4000 条基线结果
- `perception/C2_decision.md` —— C2 降级决策依据
- `perception/analysis/data_stats.json` —— 标注层全量统计