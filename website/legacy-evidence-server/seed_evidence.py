# -*- coding: utf-8 -*-
"""
seed_evidence.py — 将项目既有真实工作成果导入为初始证据链
==========================================================

只导入"有据可查"的事实 (来源: rl_train/ 产物 + 训练方法与坠机诊断报告.md):
  #1  milestone    需求基线导入 ( 六项技术指标 + 四个进度节点)
  #2  analysis     FlightGear 实时回路诊断结论 (控制通道/增稳/通信架构)
  #3  verification 环境自检与启发式 PD 基准 (20/20 成功, 0 坠毁)
  #4  incident     PPO v1 训练失败 (地面死锁局部最优, 成功率 0%)
  #5  training_run PPO v2 训练 (1,000,000 步 / 199s / 5024 steps/s)
  #6  evaluation   v2 最终确定性评估 (20/20 成功, 坠毁 0%, 高度误差 6.80m)
  #7  verification 指标 K1/K2 达标判定 (口径: 简化仿真环境)

运行: python seed_evidence.py          (重复运行不会重复导入, 已有链则跳过)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence_logger as el  # noqa: E402

# 项目基线: T0 = 2026-08-01 (合同签订, 可在此调整)
T0 = "2026-08-01"

METRICS_BASELINE = [
    {"id": "K1", "name": "动作操作成功率", "requirement": "≥ 85%",
     "source": "自主起飞、悬停、前飞、滚转、偏航、降落功能操作"},
    {"id": "K2", "name": "系统响应延迟", "requirement": "≤ 200ms",
     "source": "控制指令发送至机器人产生响应"},
    {"id": "K3", "name": "任务意图理解准确率", "requirement": "≥ 95%",
     "source": "任务规划、航路导航典型飞行场景"},
    {"id": "K4", "name": "操控映射准确率", "requirement": "≥ 90%",
     "source": "任务意图到座舱物理操作映射"},
    {"id": "K5", "name": "座舱建模精度", "requirement": "≤ 5mm",
     "source": "飞行座舱场景三维数字资产库 (摇杆/脚蹬/总距杆/仪表/按钮)"},
    {"id": "K6", "name": "有效操纵数据时长", "requirement": "≥ 1000 小时",
     "source": "第一、三人称视角操纵摇杆数据采集"},
]

MILESTONES = [
    {"id": "T0", "name": "技术服务合同和技术协议签订", "date": T0},
    {"id": "T1", "name": "动力学和运动控制模型搭建 + 数据管理软件设计", "date": "2027-01-01", "label": "T0+5月"},
    {"id": "T2", "name": "软件在环仿真环境搭建, 形成全流程研发能力", "date": "2027-06-01", "label": "T0+10月"},
    {"id": "T3", "name": "合同验收", "date": "2027-08-01", "label": "T0+12月"},
]


def main():
    existing = el.load_chain()
    if existing:
        print(f"证据链已存在 ({len(existing)} 条记录), 跳过种子导入。"
              f"如需重建请先删除 evidence/chain.jsonl")
        return

    ev = el.EvidenceLogger(actor="seed_evidence.py")

    ev.log("milestone", "需求基线导入:  验收技术要求",
           payload={
               "project": "低空具身智能体运动控制与全数字仿真技术研究",
               "project_code": "",
               "t0": T0,
               "metrics_baseline": METRICS_BASELINE,
               "milestones": MILESTONES,
               "note": "四个进度节点日期按 T0=2026-08-01 推算, 合同签订后可修订",
           })

    ev.log("analysis", "FlightGear 实时回路诊断: 训练不可行的根因",
           payload={
               "conclusion": "FlightGear 实时渲染回路无法承载 RL 训练 (≈4 步/秒, 训练 10^7 步需约 29 天), 降级为最终可视化工具; 训练改用无头快速环境",
               "findings": [
                   "helo_fg_loop.py 起飞阶段误写 engine throttle 而非 collective, 总距未受控 (flight_log.json: coll=0.72 恒定, agl 钉在 3.9ft)",
                   "起飞阶段关闭姿态增稳, Bo-105 悬停静不稳定 → 离地即滚转发散坠机",
                   "启动/复活流程每轮阻塞 1~3 分钟墙钟时间",
                   "简化动力学脚本地面效应硬切换导致贴地弹跳",
               ],
               "fix": "新增 rl_train/ 无头 Gymnasium 环境 (50Hz, 修好地面效应与推力标定) + SB3 PPO",
           },
           artifacts=[("训练方法与坠机诊断报告", "训练方法与坠机诊断报告.md")])

    ev.log("verification", "环境可解性验证: 启发式 PD 基准",
           payload={
               "method": "手写 PD 控制器基准 (heuristic_check.py)",
               "result": "20/20 回合成功起飞并悬停到目标高度, 0 坠毁, 最终高度误差 ≈1.3m",
               "env_selfcheck": "满总距 300 步爬升到 75.4m, 无坠毁; 悬停总距标定 0.7007",
               "conclusion": "环境可解, 排除环境本身不可训练的可能",
           })

    ev.log("incident", "PPO v1 训练失败: 策略拒绝起飞 (地面死锁)",
           payload={
               "symptom": "奖励仅含 -|alt_err| 时, 训满 400k 步成功率 0%, 坠毁率 0%, 平均高度误差恒定 ≈52m (永远趴地)",
               "root_cause": "地面是死锁平台: 趴地时任何动作都不改变状态 (无因果信号), 早期随机飞行只会挨罚 → 收敛到'不起飞'局部最优",
               "lesson": "奖励结构必须给'起飞'动作提供正梯度",
               "fix": "v2 改用势函数奖励塑形 r += (prev_alt_err - alt_err) + 起飞拖延惩罚 + 悬停保持奖励 + 熵系数 0.01",
           })

    ev.log("training_run", "PPO v2 训练: FastHeloEnv 起飞-悬停任务",
           payload={
               "algorithm": "Stable-Baselines3 PPO (MlpPolicy 128x128)",
               "mode": "direct (总距/俯仰/滚转/尾桨)",
               "steps": 1000000,
               "parallel_envs": 8,
               "device": "cpu",
               "train_seconds": 199.0,
               "steps_per_second": 5024,
               "reward_shaping": "势函数塑形 + 起飞拖延惩罚 + 悬停保持奖励",
               "success_curve": "0% (<600k) → 83% (700k) → 100% (800k 起)",
               "history": [
                   {"steps": 200000, "success_rate": 0.0, "crash_rate": 0.0, "mean_alt_err_m": 52.0},
                   {"steps": 400000, "success_rate": 0.0, "crash_rate": 0.0, "mean_alt_err_m": 52.0},
                   {"steps": 600000, "success_rate": 0.0, "crash_rate": 0.0, "mean_alt_err_m": 52.0},
                   {"steps": 700000, "success_rate": 0.83, "crash_rate": 0.0, "mean_alt_err_m": 11.4},
                   {"steps": 800000, "success_rate": 1.0, "crash_rate": 0.0, "mean_alt_err_m": 7.2},
                   {"steps": 1000000, "success_rate": 1.0, "crash_rate": 0.0, "mean_alt_err_m": 6.8},
               ],
           })

    ev.log("evaluation", "PPO v2 最终评估: 确定性策略 20 回合",
           payload={
               "episodes": 20,
               "deterministic": True,
               "success_rate": 1.0,
               "crash_rate": 0.0,
               "mean_alt_err_m": 6.8,
               "task": "随机目标高度 25-70m 起飞并悬停",
               "env": "FastHeloEnv (简化质点+一阶姿态动力学, 50Hz)",
               "caveat": "简化环境口径; 迁移 Bo-105/YASim 需系统辨识 + 域随机化微调",
           },
           artifacts=[("评估指标", "rl_train/results/helo_ppo_results.json"),
                      ("策略模型", "rl_train/results/helo_ppo.zip")])

    ev.log("verification", "指标达标判定: K1 成功率 / K2 响应延迟",
           payload={
               "judgements": [
                   {"metric_id": "K1", "requirement": "≥ 85%", "measured": 1.0,
                    "pass": True,
                    "evidence_basis": "EV 记录 #6 (20/20 确定性评估)",
                    "scope": "简化仿真环境, 任务=起飞+悬停; 前飞/滚转/偏航/降落未验收"},
                   {"metric_id": "K2", "requirement": "≤ 200ms", "measured": 20,
                    "unit": "ms",
                    "pass": True,
                    "evidence_basis": "仿真控制周期 DT=0.02s (50Hz), rl_train/helo_env.py",
                    "scope": "全数字仿真口径; 硬件在环实测延迟未开展"},
               ],
               "not_started": ["K3", "K4", "K5", "K6"],
           })

    result = el.verify_chain()
    print(f"已导入 {len(el.load_chain())} 条种子证据; 链校验: "
          f"{'通过' if result['ok'] else '失败: ' + str(result['problems'])}")
    print(f"链头哈希: {result['head_hash'][:16]}…")


if __name__ == "__main__":
    main()
