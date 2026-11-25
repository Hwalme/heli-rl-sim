# C2 决策:基线路线 —— 显存核查与降级说明

> 日期:2026-08-28 · 决策人:轨道C 主任会话 · 依据:《项目分工与协同计划.md》§2 轨道C + 《训练方法与坠机诊断报告.md》§3

## 1. 硬件核查(本机实测)

`nvidia-smi` 输出(2026-08-28):

```
NVIDIA GeForce RTX 3060 (Laptop, 131W)
专用显存:1116 MiB / 6144 MiB(系统占用中)
CUDA 13.2 / Driver 596.36
```

**可用显存 ≈ 5GB,总显存 6GB。**

## 2. 任务书判据

任务书原文:"OpenVLA-7B LoRA 在子集上的最小微调(**先确认显存:7B LoRA 约 24GB,不够就降级为评测轨道**)"。

| 方案 | 显存需求 | 本机 6GB | 结论 |
|------|---------|----------|------|
| openvla/openvla-7b LoRA 微调 | ~24GB(7B 权重 bf16 ≈14GB + LoRA 激活/优化器) | ❌ 缺口 4 倍 | 不可行 |
| openvla-7b 4bit 量化推理 | ~5-6GB | ⚠️ 临界,无微调空间 | 仅理论可行,不做承诺 |
| **降级:评测轨道**(任务书明文允许) | CPU/任意 GPU | ✅ | **采用** |

## 3. 降级后的 C2/C3 工作定义

评测轨道 = 把"任何模型来了都能被公平打分"的基础设施建好:

1. **C2-E1 指标库**:`eval/metrics.py` —— NE / SR / OSR / SPL / nDTW / SDTW(航空 VLN 标准,自检通过);
2. **C2-E2 基线集**:在 OpenFly 1% 子集上跑三类非神经基线,校准指标语义:
   - GT-oracle:直接回放 ground-truth 航点(指标应为满分,验证评测器正确性);
   - 直线基线:起点→终点直线飞行(有成功率、SPL<1);
   - 随机基线:随机游走(应接近 0 分);
3. **C2-E3 基线报告**:`eval/baseline_report.md` 记录三类基线分数,作为后续任何 VLA 模型的对照锚点。

## 4. 升级路径(留给未来有 ≥24GB 卡时)

- 权重:`openvla/openvla-7b`(HF,64k 下载);数据:本轨道 `data/` 的 1% 子集 + RLDS 版 `IPEC-COMMUNITY/OpenFly-rlds`;
- 微调:LoRA r=16, bf16, batch 1 + grad-accum,单卡 24GB 参考 OpenVLA 官方 recipe;
- 接入:微调模型的预测航点按 `eval/run_eval.py` 的输入协议喂入即可,指标不变,报告可直接对比。

## 5. 涉及文件

- `eval/metrics.py` —— 指标库(已自检 PASS)
- `eval/run_eval.py` —— 评测入口(对接数据格式后完成)
- `eval/baseline_report.md` —— 基线报告(C3 交付)
