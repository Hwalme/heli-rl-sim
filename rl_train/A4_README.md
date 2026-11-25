# A4 鲁棒性评估

> 轨道 A · 任务书 §2 A4：在不重训的前提下，用现有训练好的 PPO 模型在扰动矩阵下评测，给 M4 集成演示提供"已知能工作"vs"已知要重训"的稳定性账。

---

## 1. 背景与动机

| 任务 | 训练后验收 (5 种子 × 20 回合) | 结论 |
|------|------------------------------|------|
| hover (悬停) | 100% / 0% / 3.26 m | ✅ 达标 |
| landing (降落) | 100% / 0% / 0.09 m | ✅ 软着陆达标 |
| forward (前飞 30 kt) | 0% / 0% / 28.52 kt | ❌ 误差卡在带下边 |
| roll (滚转 ±15-20°) | 0% / 0% / 16.27° | ❌ 卡在下边界 |
| yaw (偏航 ±60-120°) | 0% / 0% / 88.96° | ❌ 策略拒绝转 |
| waypoint (3 航点) | 0% / 24% / 1428 m | ❌ 未前进 |

验收要求 6 任务成功率 ≥85%。当前 2/6 达标，4/6 失败。

A3 "课程任务族" 任务书要求"悬停→航点→避障"层级训练；但直接重训 4 失败任务在不知根因的情况下只是重复 E 15:55 整改批评的"无证据声称达标"。

**A4 的价值**：在训练改进之前，先**量化现有策略在扰动下的鲁棒性边界**——
- 哪些扰动等级会让 hover 也退化？（决策"训练时要不要加强域随机化"）
- 哪些扰动等级下 forward/roll 可能反而成功？（决策"是不是 success 判据过严"）
- yaw 89° 误差的根因：奖励信号 vs 训练时长 vs 模型容量？

扰动矩阵的输出是后续**针对根因重训**的依据——避免盲目重训。

---

## 2. 扰动矩阵设计

训练时域随机化（已有 baseline）：
- 风：±6 fps（≈ 11.6 kt）随机南北 + 东西分量
- 质量：±5% 摄动
- 延迟：L0 无延迟

评估矩阵沿三轴向外推 3 等级：

| 风等级 | wind_max_fps | 阵风 | 用途 |
|--------|--------------|------|------|
| calm | 0 | 否 | 静风基线 |
| nominal | 6 | 否 | 训练域内 |
| strong | 15 | 否 | 超出训练域 |
| gust | 10 | 是 (5 s 周期 sin, ±8 fps) | 阵风动态扰动 |

| 质量等级 | 偏移 | 用途 |
|---------|------|------|
| nominal | ±5% | 训练域内 |
| heavy15 | ±15% | 中等超载（近似携弹/副油） |
| heavy25 | ±25% | 重载（极端工况） |

| 延迟等级 | hold_steps | 含义 |
|---------|-----------|------|
| L0 | 0 | 训练域（无延迟） |
| L1 | 1 | D 契约 v1 默认（一个 DT 零阶保持） |
| L2 | 2 | 严重延迟（两 DT 零阶保持） |

矩阵总规模：4×3×2 = **24 conditions × 任务 × 20 ep**。

---

## 3. 运行命令

```bash
# 仅悬停鲁棒性 (24 conditions × 20 ep ≈ 6 分钟)
python eval_robustness.py --tasks hover --episodes 20

# 4 失败任务全矩阵 (12 conditions × 4 × 20 ep ≈ 24 分钟)
python eval_robustness.py \
    --tasks forward,roll,yaw,waypoint \
    --wind nominal,strong,gust \
    --mass nominal,heavy15 \
    --delay L0,L1 \
    --episodes 20 \
    --out results/robustness_4tasks_summary.json

# 5 任务×2 关键条件 (baseline vs 最强扰动) ≈ 10 分钟
python eval_robustness.py \
    --tasks hover,forward,landing,roll,yaw,waypoint \
    --wind nominal,gust \
    --mass nominal,heavy25 \
    --delay L0,L1 \
    --episodes 20
```

---

## 4. 输出文件

- `results/robustness_<task>.json`：单任务 × 全扰动条件结果
- `results/robustness_<tasks>_summary.json`：汇总（任务清单 + 条件数 + 结果文件路径）
- 字段：
  ```json
  {
    "model_path": "...",
    "conditions": {
      "w_nominal__m_nominal__d_L0": {
        "wind": "nominal", "mass": "nominal", "delay": "L0",
        "success_rate": 1.0, "crash_rate": 0.0, "mean_err": 3.20,
        "seed_results": [{...}, ...]
      },
      ...
    }
  }
  ```

---

## 5. 已知限制与解释注意事项

1. **风扰动仅在 reset 时注入**：训练时 JSBSim 任务的 `_reset_jsbsim` 注入风 step 0 的随机分量；后续 step 不再变。这意味着 hover/landing 这种"先爬升→稳态"任务测的是"启动瞬态 + 稳态期衰减"；forward/roll/yaw/waypoint 这种"持续机动"任务的风扰动效应更明显。
2. **质量摄动用 coll 中心偏移近似**：脚本修补 `_fly_to_hover` 内的 coll 中心 +0.10·Δmass/0.05。物理上不严格（应改 JSBSim mass + inertia），但能反映"重载悬停需要更大总距"的核心效应。
3. **延迟修补是 step 级的零阶保持**：D 契约 v1 一阶跟踪（τ_eq=0.5s）未在评估矩阵中实现，原因是 PPO 模型从未在 L1 训练过，纯评估 L1 没有基线对比价值（评估的是"零训练延迟假设的失配"）。
4. **策略从未在扰动下训练**：评估的全是 OOD 扰动（除非 calibration 用 calm/nominal/L0 这档正好是训练域）。结果应解读为"模型泛化边界"而非"训练内鲁棒性"。

---

## 6. 与 STATUS / E 信箱的同步

A4 鲁棒性评估进展同步至 `coordination/STATUS.md` 轨道 A 行的"最新交付"列。

完成后追加 `mailbox-E.md` 一条"4 任务根因初诊"消息，附 `robustness_summary.json` 摘录，供 E 决定下一步是"改进奖励 + 重训"还是"接受扰动鲁棒性边界 + 调整验收标准"。

---

## 7. 后续衔接：A3 课程任务族 / 重训

鲁棒性矩阵产出后，按以下优先级处理 4 失败任务：

1. **forward**（误差28.94 kt 卡在带下边）→ 改 in_bounds 判据 + 课程初始化 + 重训 1M 步
2. **roll**（误差 15° 卡在带下边）→ 改奖励斜率（bank phase 进度奖励权重 40→60）+ 重训
3. **yaw**（误差 89° 完全未转）→ 根因：u_yaw 一阶低通 + 25° 目标等效过小；先改 reward shape + 重训
4. **waypoint**（综合失败）→ 等前 3 任务收敛后做课程（从 forward 初始化 + 重训）

不重复训练直到鲁棒性矩阵产出（**避免 E 15:55 整改批评的"无证据声称达标"重演**）。