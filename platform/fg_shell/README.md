# fg_shell — B-Shell 主线：FlightGear 显示壳（JSBSim 轨迹回放）

> 架构终裁：`coordination/mailbox-B.md` [2026-08-28 20:35]。
> **FG = 显示壳（`--fdm=external`，主E终审 2026-08-31 批准自 fdm=null 切换），JSBSim = 物理权威。**
> 原因：fdm=null 下 FG 的 position 属性被锁定（不接受 generic input 覆盖，telnet 实证写入被丢弃）；
> fdm=external 是 FG 官方外部 FDM 接口，position 可写（telnet 写 500ft 读回 500 实证）。
> 本模块把轨道 A 已产出的验收同口径轨迹（`rl_train/results/jsbsim_ppo_*_trajectory.json`，6 份）
> 通过 generic UDP 回放进 FlightGear 渲染——画面即验收同口径轨迹，不需要 A 做任何配合。

## ⚠️ 视觉替身披露（必读）

**FG 渲染的机型是 bo105，仅仅是视觉替身；仿真物理是 AH-1S(repaired)。**
M4 演示解说/字幕/报告必须写明"Bo-105 为可视化替身"，防口径质疑（终裁第 5 条）。

## 架构

```
rl_train/results/jsbsim_ppo_*_trajectory.json      (JSBSim 6-DoF, 50Hz, AH-1S)
        │
        ▼
platform/fg_shell/playback.py                       (按轨迹时间戳, 30Hz UDP)
        │   UDP 127.0.0.1:5500
        │   "lat,lon,alt,roll,pitch,hdg,gs,rpm\n"
        ▼
FlightGear 2024.1.4  (--fdm=null --aircraft=bo105)
        │   protocol/jsbsim_replay.xml -> /position /orientation 属性树
        ▼
FG 渲染窗口（PHNL，画面对齐验收轨迹）     telnet 127.0.0.1:5503（验证）
```

## 文件清单

| 文件 | 作用 |
|---|---|
| `protocol/jsbsim_replay.xml` | generic UDP 输入协议（8 字段 CSV→属性树）。**已复制一份到 fgdata `Protocol\`（见 §协议路径解析）** |
| `playback.py` | 回放器：读 trajectory.json → 按时间戳发 UDP；支持循环/倍速/起点；写回放 CSV+meta 到 `logs/` |
| `verify_replay.py` | 验证器：边回放边 telnet 5503 轮询，与轨迹同刻比对，写 `logs/verify_*.json/.txt` |
| `fg_shell_launch.bat` | B-Shell 专用 FG 启动器（CRLF+ASCII；fdm=null + 5500/5503；经 WMI 启动） |
| `fg_shell_replay_demo.bat` | 常驻演示回放（WMI 分离启动，窗口持续飞行；经 %TEMP% ASCII 路径 runner） |
| `logs/` | 回放 CSV/meta + 验证 JSON/txt |

## 快速用法

```bash
# 1. 启动 FG（必须 WMI 分离启动，不要 bash 后台任务/直接跑 fgfs.exe）
cp platform/fg_shell/fg_shell_launch.bat /c/Users/Administrator/AppData/Local/Temp/
wmic process call create "cmd /c C:\Users\ADMINI~1\AppData\Local\Temp\fg_shell_launch.bat"
#   约 40~50s 就绪；若屏幕弹出"多个FlightGear副本正在运行"，点 OK（只读模式，不影响回放）
#   无人点框时用 UIA 自动点掉，见 §环境事实(3)

# 2. 回放一条轨迹（默认 127.0.0.1:5500 @30Hz，无限循环）
python platform/fg_shell/playback.py --file <traj.json> --episode-index 0 --rate 30 --loop 0
python platform/fg_shell/playback.py --list-episodes --file <traj.json>   # 先看有哪些回合
#   可选: --speed 0.5 倍速 / --seed 42 / --lat0 --lon0 --elev-ft 回放起点 / --heading0 初始航向

# 3. 验证（回放 + telnet 比对一体）
python platform/fg_shell/verify_replay.py --file <traj.json> --samples 20
#   输出 logs/verify_<task>_<ts>.json/.txt: d_alt/d_hdg/d_hor 统计 + fg_sim_crashed + verdict
```

## 协议字段表（protocol/jsbsim_replay.xml，输入 8 字段 CSV）

| # | 包字段 | 单位 | 目标属性 | 来源 |
|---|---|---|---|---|
| 1 | latitude | deg | `/position/latitude-deg` | 平地近似：lat0 + N/111320 |
| 2 | longitude | deg | `/position/longitude-deg` | lon0 + E/(111320·cos lat0) |
| 3 | altitude | ft MSL | `/position/altitude-ft` | elev0(13ft=PHNL 场压) + agl_m/0.3048 |
| 4 | roll | deg | `/orientation/roll-deg` | 轨迹 `roll_deg` 直通（JSBSim phi 与 FG 同号） |
| 5 | pitch | deg | `/orientation/pitch-deg` | 轨迹 `pitch_deg` 直通（theta 同号） |
| 6 | heading | deg | `/orientation/heading-deg` | 有 `yaw_deg` 用记录值；否则积分 `yaw_rate_dps` |
| 7 | groundspeed | kt | `/velocities/groundspeed-kt` | √(Vn²+Ve²)·1.94384（仅 HUD 显示用） |
| 8 | rotor rpm | RPM | `/rotors/main/rpm` | 有 `rotor_rpm` 用记录值；否则 428（bo105 governor 飞行位实测）——纯视觉 |

包格式：`%.7f,%.7f,%.3f,%.3f,%.3f,%.3f,%.3f,%.1f` + `\n`（**结尾换行符不可省**，见 §环境事实(2)）。

## 轨迹数据口径（实测核对结论）

- 字段（50Hz，dt=0.02）：`agl_m`（相对高 m，已减 AH-1S 停机基准 6.30ft）、`vz_mps`（当地垂速，向上+）、
  `vx_mps/vy_mps`（**机体**前/右速度，JSBSim u/v）、`roll_deg/pitch_deg`、`yaw_rate_dps`（机体偏航角速度）。
- 仅 `direct(hover)` 和 `landing` 两文件带 `yaw_deg`（绝对航向 0-360）、`x_m/y_m`、`rotor_rpm`、`speed_kts`；
  其余 4 文件（forward/roll/waypoint/yaw）的航向与水平位置由本模块积分：
  `psi += yaw_rate·dt`；`Vn = u·cosψ − v·sinψ；Ve = u·sinψ + v·cosψ`（忽略机体 w 轴分量的显示级近似）；
  初始航向 `--heading0`（默认 0）。
- **数据勘误（记录层）**：`x_m/y_m` 是对 `vx/vy` 的"不旋转"直接积分（已按 0.005m 精度复现），
  当航向≠0 时 ≠ JSBSim 真实地迹。本模块回放时改用上式的物理正确投影，与该两文件自带 `x_m/y_m` 数值会有偏差，属**有意纠正**。
- `agl_m` 用 `vz` 积分可复现（误差 0.05m）；回放直接用逐帧记录值，不积分。

## 验证结果（2026-08-28 真机，FlightGear 2024.1.4，日志在 logs/）

| 轨迹 | 类别 | 结论 | max\|Δalt\| | max\|Δhdg\| | d_hor | /sim/crashed |
|---|---|---|---|---|---|---|
| hover (direct) seed_123 ep0, 12s | 记录航向 | **PASS** | 3.67 ft | 5.74° | 25.9 m max | 0（未坠机） |
| waypoint seed_0 ep0, 70s | 积分航向+位置 | **PASS** | 4.01 ft | 2.69° | 19.4 m max | 0（未坠机） |

- 偏差量级 = telnet 采样斜率折算（单次往返 0.4~0.5s × 爬升率/转弯率），非回放误差；
  waypoint 全程 +412ft 爬升段 d_alt 恒为 -3.2ft ≈ 600fpm × 0.32s 读延迟，方向大小完全吻合。
- FG 位置连续变化（`fg_position_changed=true`），循环回放接缝（traj_t 12s→0）正常。
- 常驻演示：`fg_shell_replay_demo.bat`（WMI 分离）让 FG 窗口持续按 waypoint 轨迹飞行。

## 环境事实（本次新踩坑，供后续会话复用）

1. **`--generic` 协议字段不支持绝对路径**：`--generic=...,udp,C:/path/to/xx.xml` 静默失败
   （通道不创建、UDP 口不 bind、日志无 io 行、无任何报错）。**必须用协议名**（不带 .xml），
   FG 到 `<fg-root>/Protocol/<名>.xml` 查找。→ 已把 `jsbsim_replay.xml` 复制到
   `D:\Program Files\FlightGear 2024.1\downloads\fgdata_2024_1\Protocol\`（**增量单文件，已披露**；
   规范源在 platform/fg_shell/protocol/，两者内容一致）。
2. **generic input 包必须以 `<line_separator>`（newline）结尾**：缺 `\n` 时 FG 凑不齐一行，
   通道静默不生效（UDP 有包、属性零变化、无报错）。排查成本 ~1 小时，切记。
3. **FG 2024.1.4 单实例锁**：已有 fgfs 存活时再启动，新实例弹模态框
   "多个FlightGear副本正在运行"（LockFileDialog，文案：将以只读模式运行，无法下载/不保存设置），
   **点 OK 后可多实例并行**（读只模式对纯回放无影响）。无人点框则新实例永久卡死在 ~100MB。
   UIA 自动点框（按 PID 定位窗口、点 OK 按钮）：
   `powershell` + `System.Windows.Automation`，样例见 mailbox-B 2026-08-28 21:0x 条目。
4. **多会话互踩风险**：其他会话按 B1 交接卡第 1 步 `taskkill //F //IM fgfs.exe` 会把本模块的实例一起杀掉
   （本次已发生 1 次）。约定：各会话杀 fgfs 前先 `wmic process where "name='fgfs.exe'" get ProcessId,CommandLine`
   按命令行（5501/5503 端口可区分）只杀自己的。
5. bat 必须 CRLF+ASCII（中文路径经 bat 传参会乱码）；B-Shell 的 bat 一律 ASCII，长路径经
   `%TEMP%`（`C:\Users\ADMINI~1\...` 短名）绕开。

## 端口约定（多会话共存）

| 端口 | 归属 |
|---|---|
| 5501 telnet | 其他会话（rev4 DemoStab 实例）——**勿占用勿杀** |
| 5502 | 备用（勿占用） |
| **5500 UDP (generic in)** | **B-Shell 本模块** |
| **5503 telnet** | **B-Shell 本模块（验证）** |

## 已知局限

- 水平位置为显示级重建（机体 w 轴分量未测，无法参与投影）；高度/姿态/航向为轨迹精确值。
- 4 个无 `yaw_deg` 的文件初始航向取 `--heading0`（默认 0），非 JSBSim 真实初始 psi（记录里没有）。
- `--fdm=null` 下 FG 不做坠机判定，`/sim/crashed` 恒 0；坠机与否以轨迹元数据
  （`crashed/success` 字段，JSBSim 侧判定）为准，回放层在验证日志中原样透出。
