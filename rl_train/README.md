# rl_train/ —— 运动控制 / RL 训练 (轨道 A)

JSBSim AH-1S 6-DoF 高保真环境上的 PPO 强化学习训练管线, 覆盖验收
需求 5 的全部 6 种自主飞行动作 (起飞/悬停/前飞/滚转/偏航/降落/航线机动)。

## 文件结构

| 文件 | 说明 |
|------|------|
| `jsbsim_helo_env.py` | 悬停任务环境 (`JSBSimHeloEnv`), 支持 direct 4 维 / arm 7 关节 |
| `jsbsim_tasks.py` | 机动任务族 v2: 前飞/降落/滚转/偏航/航点 5 个环境 + 共享基类 |
| `train_jsbsim.py` | 统一训练入口 (`--task` 选择任务, 5 种子 × 20 回合评估) |
| `train_ppo.py` | 简化环境 (FastHeloEnv) 训练入口, M0 时期产物 |
| `helo_env.py` | 简化 1-DoF 质点环境 (M0, 历史基准) |
| `heuristic_check.py` | 简化环境启发式 PD 基准 (20/20) |
| `build_suite_report.py` | 聚合 6 份任务结果 → 套件级验收汇总 JSON |
| `results/` | 训练权重 (.zip) + 结果报告 (*_results.json) |

## 使用

```bash
# 悬停 (向后兼容, direct/arm 双模式)
python train_jsbsim.py --task hover --mode direct --steps 300000

# 机动任务 (direct 4 通道)
python train_jsbsim.py --task forward  --steps 600000 --ent-coef 0.01
python train_jsbsim.py --task landing  --steps 500000 --ent-coef 0.01
python train_jsbsim.py --task roll     --steps 600000 --ent-coef 0.01
python train_jsbsim.py --task yaw      --steps 600000 --ent-coef 0.01
python train_jsbsim.py --task waypoint --steps 800000 --ent-coef 0.01

# 套件汇总 (聚合 results/ 全部 6 份结果, 产出 jsbsim_multitask_suite_results.json)
python build_suite_report.py
```

评估口径: 每任务 5 个独立随机种子 (42/123/7/0/99) × 各 20 回合 = 100 回合;
验收线 = 成功率 ≥ 85% 且坠毁率 = 0% (验收指标 5)。

## 任务定义 (jsbsim_tasks.py v2)

所有任务共享: 4 维动作 [-1,1]、10 维观测、50 Hz 控制周期、`_apply_sas`
姿态阻尼执行包络 (与已训悬停环境逐字一致)、`reset()` 先经 `_fly_to_hover`
PD 增稳爬升建立初始悬停 (起始姿态 ≤10°)。

| 任务 | 目标 | 成功判据 |
|------|------|---------|
| `forward` | 30 kt 定速 + 20 m 定高 | 速度 ±5 kt 且高度 ±3 m 累计 > 4 s |
| `landing` | 15 m 悬停软着陆 | 接地 vz ≥ -3.0 m/s (硬接地记失败不记坠毁) |
| `roll` | 压坡度 ±15~20° 保持 4s 后改平保持 4s | 坡度段平均误差 < 6° 且末态 \|roll\| < 5° |
| `yaw` | 航向改变 ±60~120° 并保持 | 航向误差 < 5° 累计 > 2 s 且高度带内 |
| `waypoint` | 依次飞抵 3 个 L 形航点 | 3 点全部进入 10 m 半径 |

### 环境级机动补偿 (不改动作契约)

调试中实测的失败模式与对应补偿 (均属环境内部增稳, 4 维动作语义不变):

1. **滚转-总距前馈 + 垂直速度阻尼** (roll 任务): 压坡度时垂直升力按
   cos(φ) 减小, 无补偿则策略必然掉高度触地、歪靠橇轨卡死 (~-30° 动不了)。
   `coll = (0.70 + 0.25·u + 0.12·max(-vz,0)) / cos(φ)`。
2. **u_yaw 低通滤波** (yaw 任务, τ=0.4s): PPO 输出的 bang-bang 饱和舵量
   使航向在目标两侧 ±100° 来回甩, 低通后等效柔和蹬舵。
3. **目标坡度左右均衡采样** (roll 任务): 按回合奇偶交替符号, 防止策略
   收敛到单侧压坡。

### 奖励塑形要点 (踩坑记录)

- 势函数进度项 + "拖延成本" (未达成条件每步扣 0.05~0.3): 没有拖延成本时
  PPO 收敛到"趴在悬停配平不动" (0% 成功 0% 坠毁)。
- 高度进度权重必须 ≥ 速度进度权重 + 俯冲惩罚 (`pitch < -15°` 部分每 rad
  扣 0.5): 否则"栽头换速度"是奖励最优, 84% 回合俯冲坠地。
- 降落任务硬接地 (vz ≤ -3 m/s) 按 E 轨道 A6 口径记"着陆失败", 不判坠毁;
  姿态 > 60° 才计入坠毁率。

## 结果

见 `results/*_results.json` (逐任务) 与 `results/jsbsim_multitask_suite_results.json`
(套件汇总, 6 动作全覆盖验收判定)。网站数据面板自动消费本目录 results。
