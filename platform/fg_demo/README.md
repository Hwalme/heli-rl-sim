# B1 · FlightGear Bo-105 完整起落演示（修复版）

> 任务书 §2 轨道 B / B1 验收：完整起落一次无坠毁，留日志/录屏。
> **当前状态（2026-08-28 13:10 实测结论）**：
> - 链路修复（§1 三条 + §1.4 启动）**全部通过真机验证**。
> - 演示**失败**——**架构性瓶颈**：telnet 逐属性闭环实测 ≈6.6 s/帧（10 get+3 set/帧），无法满足 Bo-105 增稳带宽（≤0.1 s）。
> - 下一轮 B1 必须重构链路（UDP generic 双向 / Nasal 内环跑在 FG 内部），详见 §6。

---

## 1. 修复了什么（对照诊断报告 §1 四条）

| 诊断条目 | 修复点（在本目录文件） |
|---|---|
| **§1.1 控制通道：throttle ≠ collective** | 本机 `downloads\Aircraft\bo105\bo105.xml` L67 把主旋翼 COLLECTIVE 直接绑定在 `engine[0]/throttle`，**反向**（小值=大总距）。`fg_demo.py` 写 `engine[n]/throttle` 即提总距；本机**无 collective 属性绑定**。详见 §4 |
| **§1.2 起飞关增稳→离地即翻滚** | `fg_demo.py` `Autopilot.stabilize()` 从连接后**全程运行**（地面→起飞→爬升→悬停→下降→接地），不再"零操纵"；角速度读 `/orientation/*-rate-deg`；PD 增益 roll 0.055/0.055、pitch 0.050/0.040（旧版 0.035/0.030@0.25s 加大并提速） |
| **§1.3 通信三方矛盾** | `start_fg_demo.bat`：**去掉 `--fdm=external`**（YASim 原生）、**去掉 `--generic`**、加 `--telnet=socket,in,5,127.0.0.1,5501,tcp`（2024.1 不接受末尾 `,prompt`，旧 7 段式会让 `fg_io.cxx` 解析失败） |
| **§1.4 启动阻塞** | `--prop:/devel=1 --prop:/quickstart=1`：`bo105.nas` 在 fdm-init 时执行 `engines.quickstart()`，延时归零、旋翼秒级到 ~428 RPM（governor 飞行位实际稳态，非文档所写 442 RPM；详细见 §5） |
| **额外** KSFO 无地景会沉地坠毁 | 演示机场换 **PHNL**（夏威夷檀香山；fgdata 内置 tile w158n21；出生正常 AGL+3.9 ft） |
| **额外** 2024.1 启动弹窗/模态框 | `--fullscreen=0`（替代被废弃的 `--disable-full-screen`）+ `--console=0`（防重定向弹窗） |

## 2. 目录文件

| 文件 | 用途 |
|---|---|
| `start_fg_demo.bat` | **CRLF + ASCII** 启动参数（CRLF 是 Windows bat 必须）；2024.1 兼容版（含 PHNL / `--fullscreen=0` / telnet 6 段式 / `--console=0`） |
| `fg_demo.py` | Python 3.10+ 标准库客户端 + 10 Hz 姿态 PD 全程增稳 + 总距闭环垂直剖面（地面→离地→爬升→悬停60 ft×20 s→下降→接地）+ JSON+TXT 日志 |
| `logs/flight_<ts>.json` | 帧日志（`t`、`phase`、`coll`、`agl`、`vs`、`pitch`、`roll`、`heading`、`rpm`…） |
| `logs/flight_<ts>_summary.txt` | 人类可读总结 |
| `_diag.bat` | 早期调试用 bat 参数 echo（CRLF 验证），可忽略 |

## 3. 使用方法

```cmd
:: 1) 启动 FlightGear（PHNL 停机位 bo105，零弹窗）
start_fg_demo.bat

:: 2) 在另一个终端跑自动起落
python fg_demo.py

:: 退出码: 0=成功无坠毁 / 2=坠毁或中止 / 3=连接或自检失败
```

**关键状态机**（在 `fg_demo.py` `Autopilot.step()` 内）：
1. `ground_flat` (2 s)：总距=1.0（平桨），PD 运行
2. `liftoff_ramp`：总距按 0.015/s 减小（增总距），直至 `vs>2 fps` 或 `agl>2 ft` → 锁存离地总距
3. `climb`：vs_target=+8 fps，PI 调总距；`agl≥50` → `hover_reach`
4. `hover_reach` / `hover_hold`：60 ft 悬停 20 s
5. `descend`：vs_target 分段（-6/-3.5/-2 fps）
6. `touchdown` → 接地 `settle` → done

帧间隔 telnet 实测 6.6 s，状态机相位按真实墙钟计时（不拉长/缩短相位）。

## 4. **本机 Bo-105 总距属性关键事实**（A 联调必读）

> 来源：`D:\Program Files\FlightGear 2024.1\downloads\Aircraft\bo105\bo105.xml` 第 67-70 行

```xml
<control-input axis="/controls/engines/engine[0]/throttle" control="COLLECTIVE"
               src0="0.0" src1="1.0"
               dst0="1.0" dst1="-1.0"/>
```

**含义**：
- YASim 主旋翼 COLLECTIVE 通过 `engine[0]/throttle` 接收，方向反向：`throttle=0` → COLLECTIVE +1（最大总距 15.8°）→ 最大升力；`throttle=1` → COLLECTIVE -1（-0.2° 平桨）→ 无升力。
- **本机属性路径中没有 `/controls/engines/engine[n]/collective`**——直接写 collective 在本机无效。
- `Autopilot.set_collective(value)` 同步写 `engine[0]/throttle` 与 `engine[1]/throttle`，幂等无害。
- **不同 FG 版本 / 不同 bo105 变体**可能使用 `/controls/collective` 或 `engine[n]/collective`；新版本部署前请以本机 `bo105.xml` 的实际 `<control-input>` 行为为准。

D 契约 v1-frozen 规定 `collective ∈ [0,1]`（`interfaces/action-space.md` §1.1/§4），与本机 throttle 属性值域**完全一致**：RL 策略输出的 `collective` 可直接 `engine[0]/throttle = collective` 桥接到 FG，**无需 D 契约改动**。

## 5. **本机 Bo-105 功率/转速实测事实**（勘误，请覆盖原 README 旧数据）

| 量 | 原 README 误写 | **2026-08-28 真机实测** |
|---|---|---|
| 发动机功率杆 `/controls/engines/engine[n]/power` | 飞行位 `0.63` | **飞行位 `1.0`**；`0.63` 是**慢车位**（`bo105.nas` L330 `pos:{idle:0.63, flight:1}`） |
| 旋翼稳态 RPM | 442（文档/原代码假设） | **~428**（governor 飞行位下实测：power=1.0 后旋翼 279→428.5 RPM 稳定保持；非文档所称 442） |
| 自检阈值 | `rpm > 400` | `rpm > 400`（与 ~428 兼容，可继续） |
| 出生点 AGL | 假设 ~0 | **PHNL 停机位 ~3.9 ft**；状态机按 agl_ref 减基准处理 |

`fg_demo.py` 已应用上述勘误：POWER 写 1.0；wait_ready 仅在 `fdm-initialized=1` 后才判 `crashed`（FDM 未就绪时该信号默认 1，会误报开场坠机）；相位计时用真实墙钟 dt_real。

## 6. 已知问题与重构方向（架构性瓶颈，**B1 下一轮必须解决**）

**根因**（2026-08-28 13:10 实测 logs/flight_20260828_125832.json，46.4 s 仅 7 帧）：

- telnet 属性树**单帧往返 ≈6.6 s**（10 get + 3 set 共 13 次往返；FG 渲染下单次往返 0.4-0.5 s）。
- 诊断报告 §1.2 自己要求控制周期 ≤0.1 s（10 Hz）——实测差 **66 倍**。
- 总距斜坡至 ~0.418 离地瞬间姿态发散（roll→-84°、vs=-121 fps 坠毁），**telnet 闭环无法满足 Bo-105 悬停增稳带宽**。

**重构方向**（二选一）：
1. **UDP generic 双向通道**：写正确的 protocol XML，目标 10-20 Hz；注意诊断报告 §1.3 否定的不是 generic 本身，而是"无协议文件的裸 UDP 文本"。
2. **Nasal 内环跑在 FG 内部**（帧率级）：telnet/UDP 只下发目标指令（总距、姿态指令），PD/姿态闭环写进 bo105.nas 旁挂的脚本，fg_demo.py 退化为"高层指令派发"。

**预期**：① 链路吞吐 ≥10 Hz；② Bo-105 悬停/起飞闭环可观测；③ 与 RL 策略对接只传 4 维控制量（collective、cyclic_pitch、cyclic_roll、pedals）。

## 7. 已知与已知未

**已知（fgdata 已恢复）**：用户已装 FlightGear-2024.1.4-data.txz (1.88 GB) 至 `downloads\fgdata_2024_1`（版本精确匹配 fgfs 2024.1.4）；bat 零弹窗启动；PHNL 停机位出生 AGL+3.9 ft；旋翼 ~428 RPM 稳定。

**已知未（按价值排序）**：
- B1 重构决策（UDP generic vs Nasal 内环）：需 E 仲裁（FG 链路架构 vs 单一脚本可控性取舍）；当前未动。
- 真机/VLA 对接（章节 §6 重构后才能让 RL 策略以 ≥10 Hz 控制）。
- 视觉回放（FGFSD replay 或 fgtape）是否要作为验收附件：当前未要求。