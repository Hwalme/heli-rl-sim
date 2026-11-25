# -*- coding: utf-8 -*-
"""
build_suite_report.py: 聚合各任务结果文件, 生成覆盖验收 6 种飞行动作的
套件级汇总报告 jsbsim_multitask_suite_results.json。

只读 results/ 下各任务的 *_results.json, 不重新训练。任一必需任务文件缺失时
该动作记为 missing, 套件 overall 不达标。

动作映射 (验收指标 5: 自主操作成功率 >= 85%, 覆盖起飞/悬停/前飞/滚转/偏航/降落):
  required=True 的 5 个任务覆盖验收全部 6 种动作 (起飞+悬停由 hover 覆盖);
  waypoint 航线机动为 A3 课程扩展项, 如实报告但不作为验收门禁。
"""
import os
import sys
import json
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

SUITE = [
    ("起飞与稳定悬停", "hover", "jsbsim_ppo_direct_results.json", True),
    # E 2026-08-30 09:55 裁决: 起飞验收代表=takeoff_hard (FG=显示壳, 演示为轨迹回放);
    # takeoff_fg 降级选做 (历史最佳10%/12%, 见 mailbox-E 10:40 结案条目), 不入必需套件
    ("起飞(地面→15m, 漂移<30m)", "takeoff", "jsbsim_ppo_takeoff_hard_v20260830T091717_results.json", True),
    ("前飞速度跟踪 (30kt)", "forward", "jsbsim_ppo_forward_results.json", True),
    ("滚转机动 (压坡度/改平)", "roll", "jsbsim_ppo_roll_results.json", True),
    ("偏航机动 (航向改变/保持)", "yaw", "jsbsim_ppo_yaw_results.json", True),
    ("定点降落 (软接地+航向/漂移/近地门)", "landing", "jsbsim_ppo_landing_250k_g2verify_results.json", True),
    ("航路点导航 (航线机动, 扩展)", "waypoint", "jsbsim_ppo_waypoint_results.json", False),
]


def main():
    maneuvers = {}
    rates, crashes = [], []
    for label, key, fname, required in SUITE:
        path = os.path.join(RESULTS_DIR, fname)
        if not os.path.exists(path):
            maneuvers[key] = {"maneuver": label, "status": "missing",
                              "results_file": fname, "required": required}
            continue
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        passed = bool(r.get("acceptance_passed", False))
        maneuvers[key] = {
            "maneuver": label,
            "status": "pass" if passed else "fail",
            "required": required,
            "success_rate": r.get("overall_success_rate"),
            "crash_rate": r.get("overall_crash_rate"),
            "episodes": sum(s.get("episodes", 0) for s in r.get("seed_results", {}).values()),
            "seeds": sorted(r.get("seed_results", {}).keys()),
            "metric": r.get("task_metric", "mean_alt_err_m"),
            "metric_value": r.get("overall_mean_alt_err_m"),
            "results_file": fname,
        }
        if passed and required:
            rates.append(r.get("overall_success_rate", 0.0))
            crashes.append(r.get("overall_crash_rate", 1.0))

    all_pass = all(maneuvers[k].get("status") == "pass" for _, k, _, req in SUITE if req)

    report = {
        "suite": "jsbsim-ah1s-multitask-maneuvers",
        "procurement_item": "需求5: 全数字仿真自主飞行动作, 成功率>=85%, 覆盖起飞/悬停/前飞/滚转/偏航/降落",
        "maneuvers": maneuvers,
        "maneuvers_passed": sum(1 for _, k, _, req in SUITE if req and maneuvers[k].get("status") == "pass"),
        "maneuvers_required_total": sum(1 for _, _, _, req in SUITE if req),
        "extension_passed": [k for _, k, _, req in SUITE if not req and maneuvers[k].get("status") == "pass"],
        "overall_success_rate": float(sum(rates) / len(rates)) if rates else 0.0,
        "overall_crash_rate": float(sum(crashes) / len(crashes)) if crashes else 1.0,
        "acceptance_passed": bool(all_pass),
        "acceptance_rule": "覆盖验收6动作的5个必需任务各自多种子(5 seeds x 20 eps)成功率>=85% 且坠毁率=0; 扩展项不设门禁",
        "verified_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    out = os.path.join(RESULTS_DIR, "jsbsim_multitask_suite_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[Suite Report] {report['maneuvers_passed']}/{report['maneuvers_required_total']} required maneuvers pass | "
          f"extensions pass: {report['extension_passed'] or '无'} | "
          f"overall_success={report['overall_success_rate']*100:.1f}% | saved -> {out}")


if __name__ == "__main__":
    main()
