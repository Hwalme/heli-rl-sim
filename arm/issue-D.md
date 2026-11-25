# D1 冒烟 issue：arm 模式在 takeoff/hover v5 观测下的链路与映射异常

- **提出方**：轨道 D（机械臂本体/具身接口）
- **日期**：2026-08-30
- **性质**：架构缺口 + 契约对齐问题，需 E 裁决
- **证据**：`arm/results/d1_arm_smoke_v5_report.json`（冒烟脚本 `arm/smoke_arm_v5.py`，只读 import + 实例化 + step，未改 rl_train 任何文件）
- **运行环境**：anaconda base Python（numpy 2.4.6 / jsbsim 1.3.1 / gymnasium 1.3.0）

---

## 1. 冒烟结论总览

| 场景 | 动作维度 | 观测维度 | 链路可跑 | 备注 |
|------|:---:|:---:|:---:|:---|
| hover arm 模式 (JSBSimHeloEnv) | 7 | **12** (v5) | ✅ | reset/step 20步无崩溃，映射正常输出 |
| takeoff direct 模式 (TakeoffTask) | 4 | **14** (v5) | ✅ | v5 观测含 pos_n/pos_e，正常 |
| takeoff arm 模式 | 7 | — | ❌ | 架构不支持（见 §2） |

---

## 2. 异常一：TakeoffTask 不支持 arm 模式（架构缺口）

### 2.1 实证

- `train_jsbsim.make_env(task="takeoff", action_mode="arm")` → **raise ValueError**：
  > `task=takeoff 仅支持 direct 模式 (arm 映射契约目前只覆盖悬停任务)`
- 直接实例化 `TakeoffTask` 后调用 `step(7维动作)` → **raise ValueError**：
  > `too many values to unpack (expected 4)`（`jsbsim_tasks.py` L1068：`u_coll, u_pitch, u_roll, u_yaw = [float(a) for a in np.clip(action, -1.0, 1.0)]`）

### 2.2 根因

- `JSBSimTaskBase.__init__`（`jsbsim_tasks.py` L78）硬编码 `action_space = spaces.Box(-1.0, 1.0, shape=(4,))`，无 arm 模式开关。
- `make_env`（`train_jsbsim.py` L82-83）对非 hover 任务的 arm 模式直接拒绝。
- arm 模式的 7 关节→4 维映射仅实现在 `jsbsim_helo_env.py`（悬停环境），任务族 `jsbsim_tasks.py` 未接入。

### 2.3 影响

- 任务书要求"arm 模式在 takeoff v5 观测下链路可跑"，当前架构无法满足。
- 若未来需要机械臂操控起飞/着陆等任务，必须为任务族加 arm 模式支持。

### 2.4 待 E 裁决

- **选项 A**：为 `JSBSimTaskBase` 增加 `action_mode="direct"|"arm"` 参数，复用 `jsbsim_helo_env._arm_to_controls()` 的映射逻辑（需 D 提供映射层抽离），takeoff/landing 等任务支持 arm 模式。
- **选项 B**：明确 arm 模式仅覆盖 hover 任务，takeoff 等任务永久使用 direct 模式，任务书 D1 范围缩减为"hover arm 模式冒烟"。
- D 建议：选项 A，但映射层需先解决 §3 的契约不一致问题。

---

## 3. 异常二：j0 总距映射与 action-space v1-frozen §4 不一致

### 3.1 契约定义（`interfaces/action-space.md` §4，v1-frozen）

```
collective = clip((j0 + 1.5) / 3.0, 0, 1)
```
- j0 = -1.5 → collective = 0.0（总距最低）
- j0 = 0 → collective = 0.5（中位）
- j0 = +1.5 → collective = 1.0（总距最高）

### 3.2 代码实现（`jsbsim_helo_env.py` L116-127 `_arm_to_controls()`）

```python
collective = float(np.clip(j[0] / 1.5, -1.0, 1.0))   # 输出 [-1, 1]
```
然后经 `_apply_controls` L137：`coll = 0.70 + u_coll * 0.25` → 实际总距 ∈ [0.45, 0.95]

### 3.3 实证对比（冒烟 Check 2，5 个测试点）

| j0 | 契约 collective | 代码 raw | 代码→SAS后总距 | 偏差 |
|---:|:---:|:---:|:---:|:---|
| -1.5 | 0.000 | -1.000 | **0.450** | +0.450 |
| -0.75 | 0.250 | -0.500 | **0.575** | +0.325 |
| 0.0 | 0.500 | 0.000 | **0.700** | +0.200 |
| +0.75 | 0.750 | +0.500 | **0.825** | +0.075 |
| +1.5 | 1.000 | +1.000 | **0.950** | -0.050 |

### 3.4 分析

- 代码把 j0 当作 **direct 模式的 a0**（以悬停配平点 0.70 为中心，行程 [0.45, 0.95]），而契约定义的是 **绝对总距 [0, 1]**。
- 这意味着 arm 模式下 j0=-1.5（契约语义"总距最低=0"）实际输出总距 0.45（仍有相当升力），j0=+1.5（契约语义"总距最高=1"）实际输出 0.95（未达满程）。
- j1–j3（俯仰/滚转/脚蹬）的映射 `clip(j/0.8, -1, 1)` 与契约逐字一致，无问题。
- 契约 §4 来源注明"helo_sim_final.py L193–203；rl_train/helo_env.py _arm_to_controls() 与之相同"——但当前 `jsbsim_helo_env.py` 的实现与契约不符，可能是历史版本漂移或契约起草时参考了旧文件。

### 3.5 待 E 裁决

- **选项 A**：修改 `jsbsim_helo_env._arm_to_controls()` 对齐契约（`collective = (j0+1.5)/3.0`），然后 `_apply_controls` 对 collective 通道做特殊处理（直接使用 [0,1] 而非 0.70+d*0.25）。**这会改变已训 arm 模式策略的行为，需 A 确认是否重训。**
- **选项 B**：修改契约 §4 对齐代码实现（j0 映射为以 0.70 为中心的 [0.45, 0.95]），走契约变更流程（issue → D 评审 → A 适配 → E 记录版本）。**这是 v1-frozen 后的契约变更，需 A 书面同意。**
- D 建议：选项 B（代码是实际运行的权威，契约应描述实际行为；且选项 A 会导致 arm 模式总距行程从 [0.45,0.95] 扩展到 [0,1]，可能引入训练不稳定）。但最终以 A 的训练影响评估为准。

---

## 4. 异常三：hover v5 观测维度与任务族不一致（非阻塞，记录）

- hover (JSBSimHeloEnv) v5 观测 = **12维** = 原10维 + pos_n/50 + pos_e/50
- takeoff (TakeoffTask) v5 观测 = **14维** = 悬停10维 + alt_err/14 + vz/5 + pos_n/50 + pos_e/50
- 两者都含位置位移（v5 整改要求），但任务族追加了 alt_err 和 vz 两个任务特定维。这是正常的任务差异，非缺陷。
- hover arm 模式在 12 维观测下链路正常，无需额外处理。

---

## 5. D 轨已确认无问题项

1. hover arm 模式 7 维动作 → 映射 → 4 维控制 → JSBSim 推进 → 12 维观测，全链路无崩溃（20 步随机动作实测）。
2. j1–j3 映射与契约一致。
3. takeoff direct 模式 14 维 v5 观测正常输出（含 pos_n/pos_e）。
4. arm 模式内部 P 控制器（增益 2.0，DT=0.02）正常追踪目标关节角（冒烟映射采样显示 joints 与 joint_targets 有合理滞后）。

---

## 6. 阻塞项与下一步

- **阻塞 D1 完整验收**：takeoff arm 模式不支持（异常一）+ j0 映射不一致（异常二），均需 E 裁决。
- D 轨在裁决前不做任何修改（铁律：不改 rl_train 环境文件；契约变更需走流程）。
- 裁决后：若选项 A（任务族加 arm 支持），D 提供映射层抽离参考实现；若选项 B（契约对齐代码），D 起草契约 v1.1 变更文档。
