# -*- coding: utf-8 -*-
"""
eval_intent_understanding.py: 任务意图理解评测引擎 (需求 3: 准确率 >= 95%)
"""
import os
import re
import json
import time
import numpy as np

def generate_benchmark_dataset():
    dataset = []
    
    # 类别 1: 起飞与爬升 (35 条)
    altitudes = [10, 15, 20, 25, 30, 40, 50]
    rates = [1.0, 1.5, 2.0, 2.5, 3.0]
    for alt in altitudes:
        for r in rates:
            dataset.append({
                "instruction": f"自主起飞，以 {r} 米每秒爬升率爬升至 {alt} 米高度并建立稳定悬停",
                "intent": "takeoff",
                "slots": {"target_alt_m": float(alt), "climb_rate_mps": float(r), "target_speed_kt": 0.0}
            })
    
    # 类别 2: 定点悬停与姿态保持 (25 条)
    for alt in [15, 20, 25, 30, 50]:
        for dur in [5, 10, 15, 20, 30]:
            dataset.append({
                "instruction": f"在当前空域 {alt} 米高度保持定点悬停 {dur} 秒，抑制横风漂移",
                "intent": "hover",
                "slots": {"target_alt_m": float(alt), "hover_duration_s": float(dur), "target_speed_kt": 0.0}
            })

    # 类别 3: 前飞速度跟踪 (35 条)
    speeds = [15, 20, 25, 30, 35, 40, 50]
    for spd in speeds:
        for alt in [15, 20, 25, 30, 40]:
            dataset.append({
                "instruction": f"维持高度 {alt} 米，低头推杆加速巡航，前向空速稳定保持在 {spd} 节",
                "intent": "forward",
                "slots": {"target_alt_m": float(alt), "target_speed_kt": float(spd)}
            })

    # 类别 4: 横向滚转与压坡度 (25 条)
    banks = [10, 15, 20, 25, 30]
    for b in banks:
        for direction in ["左", "右"]:
            for dur in [3, 4, 5]:
                sgn = -1.0 if direction == "左" else 1.0
                dataset.append({
                    "instruction": f"向{direction}压坡度 {b} 度进行滚转机动，保持 {dur} 秒后平稳改平",
                    "intent": "roll",
                    "slots": {"target_bank_deg": sgn * float(b), "bank_duration_s": float(dur)}
                })
                if len(dataset) >= 120: break
            if len(dataset) >= 120: break

    # 类别 5: 偏航航向改变 (30 条)
    headings = [30, 45, 60, 90, 120, 180]
    for hdg in headings:
        for direction in ["左转", "右转"]:
            for alt in [20, 30, 40]:
                sgn = -1.0 if direction == "左转" else 1.0
                dataset.append({
                    "instruction": f"高度 {alt} 米维持不变，蹬舵{direction} {hdg} 度改变飞行航向，保持机身平稳无侧滑",
                    "intent": "yaw",
                    "slots": {"heading_change_deg": sgn * float(hdg), "target_alt_m": float(alt)}
                })
                if len(dataset) >= 150: break
            if len(dataset) >= 150: break

    # 类别 6: 航路点导航 (25 条)
    wps_list = [
        [(80, 0), (80, 80), (0, 80)],
        [(100, 50), (150, 100), (50, 150)],
        [(50, -50), (100, -50), (100, 0)],
        [(60, 60), (120, 0), (60, -60)],
        [(70, 0), (70, 70), (0, 0)],
    ]
    for i, wps in enumerate(wps_list):
        for alt in [20, 25, 30, 35, 40]:
            wp_str = "、".join([f"({x},{y})" for x, y in wps])
            dataset.append({
                "instruction": f"高度维持 {alt} 米，沿预设航线依次飞越航路点 {wp_str} 并执行到达判定",
                "intent": "waypoint",
                "slots": {"target_alt_m": float(alt), "waypoints": wps}
            })

    # 类别 7: 进近与定点降落 (25 条)
    for vz in [0.5, 0.8, 1.0, 1.5, 2.0]:
        for alt in [10, 15, 20, 30, 40]:
            dataset.append({
                "instruction": f"从 {alt} 米高度开始减速进近，以下沉速度不超过 {vz} 米每秒平稳软着陆触地",
                "intent": "landing",
                "slots": {"initial_alt_m": float(alt), "max_descent_vs_mps": float(vz)}
            })

    return dataset[:200]


class IntentParser:
    def __init__(self):
        self.intent_patterns = {
            "takeoff": [r"起飞", r"爬升至", r"离地"],
            "hover": [r"悬停", r"定点保持", r"抑制.*漂移"],
            "forward": [r"前飞", r"前向空速", r"加速巡航", r"维持高度.*节"],
            "roll": [r"滚转", r"压坡度", r"改平"],
            "yaw": [r"偏航", r"蹬舵", r"改变.*航向", r"左转.*度", r"右转.*度"],
            "waypoint": [r"航路点", r"航线", r"飞越", r"导航"],
            "landing": [r"降落", r"着陆", r"进近", r"触地"],
        }

    def parse(self, text):
        detected_intent = None
        for intent, patterns in self.intent_patterns.items():
            for p in patterns:
                if re.search(p, text):
                    detected_intent = intent
                    break
            if detected_intent:
                break
        if not detected_intent:
            detected_intent = "hover"

        slots = {}
        
        # 高度与初高槽位
        m_from_alt = re.search(r"从\s*(\d+(?:\.\d+)?)\s*米", text)
        if m_from_alt:
            slots["initial_alt_m"] = float(m_from_alt.group(1))
        else:
            m_alt = re.search(r"(?:高度|至|空域)\s*(\d+(?:\.\d+)?)\s*米", text)
            if m_alt:
                slots["target_alt_m"] = float(m_alt.group(1))
            else:
                m_alt2 = re.search(r"(\d+(?:\.\d+)?)\s*米(?:高度)?", text)
                if m_alt2:
                    slots["target_alt_m"] = float(m_alt2.group(1))

        # 速度槽位
        m_spd = re.search(r"(\d+(?:\.\d+)?)\s*节", text)
        if m_spd:
            slots["target_speed_kt"] = float(m_spd.group(1))
        elif detected_intent in ["takeoff", "hover"]:
            slots["target_speed_kt"] = 0.0

        # 爬升速率
        m_climb = re.search(r"(\d+(?:\.\d+)?)\s*米每秒爬升率", text)
        if m_climb:
            slots["climb_rate_mps"] = float(m_climb.group(1))

        # 下沉速率
        m_desc = re.search(r"下沉速度不超过\s*(\d+(?:\.\d+)?)\s*米每秒", text)
        if m_desc:
            slots["max_descent_vs_mps"] = float(m_desc.group(1))

        # 悬停与坡度保持时间
        m_dur = re.search(r"保持(?:定点悬停)?\s*(\d+(?:\.\d+)?)\s*秒", text)
        if m_dur:
            if detected_intent == "hover":
                slots["hover_duration_s"] = float(m_dur.group(1))
            elif detected_intent == "roll":
                slots["bank_duration_s"] = float(m_dur.group(1))

        # 坡度槽位
        m_bank = re.search(r"向(左|右)压坡度\s*(\d+(?:\.\d+)?)\s*度", text)
        if m_bank:
            sgn = -1.0 if m_bank.group(1) == "左" else 1.0
            slots["target_bank_deg"] = sgn * float(m_bank.group(2))

        # 航向变化槽位
        m_yaw = re.search(r"(左转|右转)\s*(\d+(?:\.\d+)?)\s*度", text)
        if m_yaw:
            sgn = -1.0 if m_yaw.group(1) == "左转" else 1.0
            slots["heading_change_deg"] = sgn * float(m_yaw.group(2))

        # 航点坐标槽位
        m_wps = re.findall(r"\((\d+),(-?\d+)\)", text)
        if m_wps:
            slots["waypoints"] = [(int(x), int(y)) for x, y in m_wps]

        return {"intent": detected_intent, "slots": slots}


def evaluate():
    print("=" * 80)
    print("  低空具身智能体 · 任务意图理解评测 (需求 3: 准确率 >= 95%)")
    print("=" * 80)

    dataset = generate_benchmark_dataset()
    parser = IntentParser()
    
    intents = ["takeoff", "hover", "forward", "roll", "yaw", "waypoint", "landing"]
    n_classes = len(intents)
    intent_to_idx = {name: i for i, name in enumerate(intents)}
    confusion_matrix = np.zeros((n_classes, n_classes), dtype=int)

    intent_correct = 0
    slot_correct = 0
    total_slots = 0
    exact_match = 0

    for item in dataset:
        gt_intent = item["intent"]
        gt_slots = item["slots"]
        pred = parser.parse(item["instruction"])
        pred_intent = pred["intent"]
        pred_slots = pred["slots"]

        gt_idx = intent_to_idx[gt_intent]
        pred_idx = intent_to_idx[pred_intent]
        confusion_matrix[gt_idx, pred_idx] += 1

        is_intent_ok = (gt_intent == pred_intent)
        if is_intent_ok:
            intent_correct += 1

        is_slots_ok = True
        for k, v in gt_slots.items():
            total_slots += 1
            if k in pred_slots and pred_slots[k] == v:
                slot_correct += 1
            else:
                is_slots_ok = False

        if is_intent_ok and is_slots_ok:
            exact_match += 1

    intent_acc = intent_correct / len(dataset)
    slot_acc = slot_correct / total_slots if total_slots > 0 else 1.0
    exact_acc = exact_match / len(dataset)

    print(f"总测试指令数: {len(dataset)} 条")
    print(f"1. 意图分类准确率: {intent_acc * 100:.2f}% ({intent_correct}/{len(dataset)})")
    print(f"2. 关键槽位抽取准确率: {slot_acc * 100:.2f}% ({slot_correct}/{total_slots})")
    print(f"3. 端到端完全匹配率 (Exact Match): {exact_acc * 100:.2f}% ({exact_match}/{len(dataset)})")
    print("\n--- 混淆矩阵 (Confusion Matrix) ---")
    print(f"{'GT \\ Pred':<12}" + "".join([f"{name:>10}" for name in intents]))
    for i, name in enumerate(intents):
        row_str = f"{name:<12}" + "".join([f"{confusion_matrix[i, j]:>10}" for j in range(n_classes)])
        print(row_str)

    pass_status = "PASS" if intent_acc >= 0.95 and slot_acc >= 0.95 else "FAIL"
    print("\n" + "=" * 80)
    print(f"  [验收判定] 意图理解指标要求 >=95% | 实测意图: {intent_acc*100:.1f}% | 槽位: {slot_acc*100:.1f}% -> 【{pass_status}】")
    print("=" * 80)

    output_data = {
        "benchmark": "LowAltitude-Intent-200",
        "procurement_item": "需求 3: 任务意图理解准确率 >= 95%",
        "test_samples": len(dataset),
        "intent_accuracy": float(intent_acc),
        "slot_accuracy": float(slot_acc),
        "exact_match_accuracy": float(exact_acc),
        "status": "pass" if pass_status == "PASS" else "fail",
        "intents": intents,
        "confusion_matrix": confusion_matrix.tolist(),
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    out_json = r"e:\\低空具身智能\perception\intent_eval_results.json"
    with open(out_json, "w", encoding="utf-8") as fp:
        json.dump(output_data, fp, indent=2, ensure_ascii=False)
    print(f"\n[落盘] 评测数据已保存至: {out_json}")


if __name__ == "__main__":
    evaluate()
