# helispike 接口契约 v1（二代机型 · A 适配器输入）

> **发布**：轨道 B 主任会话，2026-08-30（主E任务书"接口契约尽早出"交付件）
> **适用**：`platform/fdm_spike/jsbsim_root/aircraft/helispike/`（helispike 0.1 → 0.2-WIP，本契约以 0.2-WIP 实测为准；字段级变更走版本号递增）
> **消费方**：轨道 A（环境适配器/重训）、轨道 D（技能库规格审计）
> **实测环境**：jsbsim 1.3.1 GitHub build 1837（2026-05-17），默认 root，headless，200Hz
> **重要（T1 甄别结论 2026-08-30）**：ah1s（默认 root，mainE已成熟轨道 A 一代证据机）复跑同方法同剖面，**死属性集与 helispike 完全一致**——是 **jsbsim 1.3.1 构建的全局性方法缺陷，非 helispike 特有**（我之前归因"自定义 root 绑定异常"是错的，致歉）。审计脚本 `audit_liveness_ah1s.py` 证据同 `audit_results/liveness_*.json`。**属性死因**：`/sim/flightmodel_reset` 后 IC 阶段未声明这些节点、FDMExec 在 `set_dt` 后才触发物性初始化、在 phase 0（spool 前）读这些属性时初始化尚未生效——AH-1S 在 A 轨道能"看到"是 run 后的延迟暴露。**修正映射（契约 §3.2）**已按 T1 实测修订。

---

## 1. 模型加载与 reset 流程（A 适配器必须照做）

```python
import jsbsim
fdm = jsbsim.FGFDMExec(jsbsim.get_default_root_dir(), None)  # 必须默认 root（自定义 root 标准属性绑定异常）
fdm.load_model("helispike", True)
fdm.load_ic("reset00.xml", True)     # 地面、机头 0°、agl≈4ft（滑橇 0.22ft）
fdm.run_ic()
fdm.set_dt(0.005)                    # 200Hz（滑橇接触建议 >=200Hz）
# 启动三件套（顺序无关，缺一旋翼不转/不飞）：
fdm.set_property_value("propulsion/engine[0]/set-running", 1)   # 涡轴
fdm.set_property_value("propulsion/engine[1]/set-running", 1)   # 尾桨 1hp dummy
fdm.set_property_value("propulsion/engine[0]/clutch", 1)        # ★主旋翼离合器（默认分离！）
fdm.set_property_value("fcs/throttle-cmd-norm[0]", 1.0)         # governor 通过本通道调转速
# spool：总距 0 保持，等待 rotor-rpm 进入 [495, 565]（governor 自动稳住，约 12-20s）
```

**reset 语义**：reset00 = 停机坪地面、總距平桨（collective-cmd=1.0 为平桨位，见 §2 方向说明）、无风。
**重新复位**：任意时刻 `load_ic("reset00.xml", True); run_ic()` 即回到停机状态（旋翼转速会掉，需重新 spool）。

## 2. 控制通道清单（写方向 + 单位 + 有效范围，全部实测）

| 通道（写） | 单位/范围 | 方向语义 | 实测效应 |
|---|---|---|---|
| `fcs/collective-cmd-norm` | 0..1 | **0=平桨（无升力），越大升力越大**（非反向）；**0.70≈设计悬停点**（1160lb）；>0.72 爬升；0.50-0.55 下降 | 线性映射 0..1 → 0..0.30 rad 桨距（FCS 内建表） |
| `fcs/elevator-cmd-norm` | -0.42..+0.42（FCS 内裁剪） | **+ = 低头**（前飞）；- = 低头解除/抬头 | 纵向周期变距 `longitudinal-ctrl-rad`（弧度），0.2-WIP 前飞配平表按空速内建小幅低头配平 |
| `fcs/aileron-cmd-norm` | -0.30..+0.30（含滚转配平后余量） | 符号经实测标定：**正 = 右滚压杆方向**（A 适配器建议闭环整定，勿直接信符号） | 横向周期变距 `lateral-ctrl-rad` |
| `fcs/rudder-cmd-norm` | -0.60..+0.60（前馈+踏板合计后裁剪） | 与内置尾桨前馈叠加；**前馈（collective→antitorque 表）已在 FDM 内**，A 只需踩动态修正 | `antitorque-ctrl-rad`（尾桨总距弧度） |
| `fcs/throttle-cmd-norm[0]` | 0.64..1.00 | **被内建转速 governor 占用**——A 不要写（governor 表：530rpm 处 0.35、575rpm 处 0.0） | 涡轴 N1/功率 |

**姿态控制符号（A 适配器姿态环实测标定值，helispike 专用、勿套用其他机型）**：
**`roll_sign=+1`**（T2 实证：前飞 -6/-9/-12° 扫掠下，前 -1 全 60°/170°/s 失稳；改 +1 全稳；副翼正=正 roll）、`pitch_sign=+1`（升降对俯仰）、`hdg_sign=+1`（psi 反馈对航向）、`ff_sign=-1`（尾桨前馈）。
A 的姿态环输出叠加在上述通道上时，**必须按自己的闭环整定符号**（helispike 的本机符号已实测如上，可直接作为先验）。

## 3. 观测属性清单（活性审计实测，audit_liveness.py 791 样本）

### 3.1 活属性（ALIVE，可用于观测/奖励/反馈）——34 个中精选

| 属性 | 活性范围 | 用途 |
|---|---|---|
| `attitude/phi-deg` / `theta-deg` / `psi-deg`（及 -rad） | roll 106° / pitch 61° / psi 358° | 姿态观测（**偏航速率用 psi 数值差分**，见 §4） |
| `velocities/h-dot-fps` | 活 | **垂直速度（爬升/下降判据主通道）** |
| `velocities/vc-fps` | 53.5 | 爬升率（备用） |
| `velocities/ve-kts` | 31.7 | 空速（前飞任务主通道） |
| `position/lat-gc-deg` / `long-gc-deg` | 活（前飞段有位移） | 位置/漂移观测 |
| `propulsion/engine[0]/rotor-rpm` | 599 | 旋翼转速（governor 反馈） |
| `propulsion/engine[0]/n1` | 39.8 | 涡轴 N1 |
| `propulsion/engine[0]/thrust-lbs` | 4030 | 主旋翼推力 |
| `propulsion/engine[1]/rotor-rpm` | 3616 | 尾桨转速 |
| `propulsion/engine/[01]/*-ctrl-rad`（4 个） | 活 | FCS 输出镜像（调试/观测） |
| `aero/qbar-psf`、`atmosphere/P-psf` | 3.4 / 1.66 | 动压/静压 |
| `gear/unit[0]/WOW` | 1/0 | **Weight-On-Wheels（离地/接地判据主通道）** |
| `inertia/weight-lbs` | 90（燃油消耗） | 重量 |
| `fcs/collective-cmd-norm`、`fcs/elevator-cmd-norm` | 活 | 输入回读 |

### 3.2 死属性（DEAD，禁止使用）——**T1 甄别 2026-08-30 修订**：原 v1 列 19 个，**ah1s 默认 root 复跑同方法同剖面全部命中** = jsbsim 1.3.1 全局方法缺陷（A 轨 12 维映射按此重定）；helispike 端额外 [STATIC] 标注 2 个（机构在 reset 时被冻结为常量，非死）。

| 属性 | 状态 | 替代方案 |
|---|---|---|
| `velocities/p-radps` / `q-radps` / `r-radps` | **恒 0** | 姿态角数值差分：`d(phi)/dt`、`d(theta)/dt`、`d(psi)/dt`（200Hz 下中心差分，噪声可滤） |
| `accelerations/pdot|qdot|rdot-radpssec` | **恒 0** | 角加速度 = 角速率再差分（噪声放大，慎用） |
| `position/agl-ft` / `position/h-agl-ft` / `position/altitude-ft` | **恒 0** | `velocities/h-dot-fps` 积分（实测与真值一致），或 lat/lon+场压换算 |
| `velocities/groundspeed-kt` / `airspeed-fps` | **恒 0** | `velocities/ve-kts`（活）|
| `accelerations/n-pilot-*norm` | 恒 0 | 无（过载观测不可用） |
| `atmosphere/rho-slugs-ft3` | 恒定 | 静态属性（按设计恒定，非死亡） |

**A 轨 12 维观测映射建议**（对照现 env）：agl→h-dot 积分或 lat/lon 场压换算；r→psi 差分；空速→ve-kts。**全属性活性表**见 `platform/fdm_spike/audit_results/liveness_*.json`。

## 4. 内建 AFCS/配平表（FDM 自带，A 适配器无需重造）

| 项 | 位置 | 说明 |
|---|---|---|
| 尾桨扭矩前馈表 | `Systems/hs_afcs_min.xml` yaw_afcs | collective(0..1)→antitorque 0..0.40 rad（**根治 JSBSim #1139**：静态扭矩补偿不再依赖 I 项或人工踏板）；符号 ff_sign=-1、幅值 ff-gain 可调（运行时属性） |
| 滚转-尾桨耦合配平 | 同上 roll 通道 | collective→lateral 配平（尾桨高于 CG 的反滚转） |
| 俯仰/滚转空速配平 | 同上 pitch/roll 通道 | collective×airspeed 2D 表（悬停≈0，30kt 处 -0.05 rad 低头）——**0.2-WIP：前飞段实测标定进行中，当前值仅占位** |
| 转速 governor | 同上 rpm_governor 通道 | throttle=f(rotor-rpm) 表，稳态 549-563 rpm；A **不要写 throttle** |

## 5. 已验证行为包络（helispike 0.1 SPIKE 实测；0.2-WIP 复测进行中）

| 行为 | 结果 | 证据 |
|---|---|---|
| 静悬 15s（手离杆） | 航向漂移 5.56°（判据 <10°） | probe_results/spike_v2_*.json probeA |
| collective 阶跃爬升 60s | +952ft，姿态 ≤1.2°，无翻覆 | 同上 probeB |
| collective 0.5→1.0 扫掠 | 无发散，+478ft | 同上 probeC |
| 前飞 | **WIP**：ve 9.3kt@theta-5.75° 首个配平点已采；>23kt 滚转发散待修 | probe_results/probe02_*.json |
| 着陆/接地 | **WIP**：缓降剖面滚转发散，接地行为未获有效样本 | 同上 |

## 6. 版本与变更流程

- helispike 版本号随 FDM XML 变更递增（0.1 SPIKE → 0.2-WIP）；每次变更同步 `site-packages/jsbsim/aircraft/helispike/`（默认 root 才能加载）与 `platform/fdm_spike/jsbsim_root/`（规范源）。
- 契约字段级变更 → 版本号递增 + mailbox-B/E 通报；A/D 在冻结版上开发，不受在途调整影响。
- 已知未决（0.2 收口项）：前飞滚转稳定性（>23kt）、着陆接地垂速包络、悬停-下降矩阵补全（部分数据已采，见 probe02 JSON）。
