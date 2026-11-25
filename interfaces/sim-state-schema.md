# 契约：仿真状态与观测 Schema（sim-state v0-draft）

- **所有者**：轨道 B（仿真平台）
- **消费方**：轨道 A（RL）、轨道 C（感知）、轨道 F（网站，只读 results）
- **状态**：草稿。B2 平台选型后，新平台环境必须映射到本 schema。

## 1. 观测向量（rl_train/helo_env.py 当前实现，10 维 float32）

| 索引 | 字段 | 缩放 |
|------|------|------|
| 0 | agl（离地高度） | /100 |
| 1 | vz（垂直速度） | /15 |
| 2 | vx | /10 |
| 3 | vy | /10 |
| 4 | pitch | /45° |
| 5 | pitch_rate | /3 rad·s⁻¹ |
| 6 | roll | /45° |
| 7 | roll_rate | /3 rad·s⁻¹ |
| 8 | yaw_rate | /60°·s⁻¹ |
| 9 | target_alt - agl | /100 |

## 2. step() 返回的 info 字典（事实标准）

```json
{ "agl": float, "target_alt": float, "alt_err": float,
  "crashed": bool, "success": bool, "mean_alt_err": float }
```

## 3. 新平台继承规则（B2 选型后生效）

- 新环境必须提供：等价观测（允许增维，禁止改前 10 维语义）、info 字段、
  坠毁判定语义（姿态超限/硬接地）；
- 单位统一 SI（米/秒/弧度），对外展示才换算英尺；
- 域随机化接口：`reset(seed)` 后随机目标高度、风、质量扰动必须保留。
