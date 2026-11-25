# -*- coding: utf-8 -*-
"""rev7 demo_stabilizer.xml 接线补丁（待主任授权，默认 dry-run）。

把 CEM 三个死旋钮接进相位机，并新增 liftoff 俯仰目标旋钮（分析师发现4）：
  1. ramp-fast: L189 `1.0 - 0.11 * t_ph` -> `1.0 - num("/demo/tune/ramp-fast") * t_ph`
  2. punch-floor: blitz 下限 0.40 -> num("/demo/tune/punch-floor")
  3. damper: 各相位硬编码 damper 乘以 num("/demo/tune/damper")（默认 1.0 不变性）
  4. liftoff-pitch（新）: phase 2 增加 base_pitch = num("/demo/tune/liftoff-pitch")，
     initNode 默认 0.0（与现状一致）；CEM 搜索 -4.5~0（历史成功样本 -3°~-4.5°）

用法:
  python xml_patch_pending.py            # dry-run，显示 diff 预览
  python xml_patch_pending.py --apply    # 备份+替换+XML well-formed 校验
  python xml_patch_pending.py --restore  # 从备份恢复
注意: fgfs 只在启动时读 --config，应用后需重启实例（杀 fgfs，worker 自动重拉）。
"""
import shutil
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

XML = Path(r"D:\Program Files\FlightGear 2024.1\demo_stabilizer.xml")
BAK = XML.with_suffix(".xml.bak_farm_20260829")

# (old, new) 精确串替换；每条必须在原文中唯一命中
REPLACEMENTS = [
    # 1. ramp-fast 接线
    ("                var stepv = 1.0 - 0.11 * t_ph;\n"
     "                if (stepv < 0.40) stepv = 0.40;",
     '                var stepv = 1.0 - num("/demo/tune/ramp-fast") * t_ph;\n'
     '                var punch = num("/demo/tune/punch-floor");\n'
     "                if (stepv < punch) stepv = punch;"),
    # 3. damper 乘旋钮（在漂移阻尼块前统一乘，默认 1.0 时行为不变）
    ("        # ---- drift damper modifies attitude targets (hover/land/levels) ----\n"
     "        if (damper > 0.0) {",
     "        damper = damper * num(\"/demo/tune/damper\");\n"
     "        # ---- drift damper modifies attitude targets (hover/land/levels) ----\n"
     "        if (damper > 0.0) {"),
    # 4. liftoff 俯仰目标旋钮（phase 2 guidance 块）
    ("        if (phase == 2) {\n"
     "            # liftoff: pure vertical ramp, no PI\n"
     "            setprop(\"/controls/engines/engine[0]/throttle\", coll_val);",
     "        if (phase == 2) {\n"
     "            # liftoff: pure vertical ramp + nose-down trim (front-skid pivot)\n"
     "            base_pitch = num(\"/demo/tune/liftoff-pitch\");\n"
     "            setprop(\"/controls/engines/engine[0]/throttle\", coll_val);"),
    # 4b. initNode 声明（默认 0.0 = 与现状一致）
    ("props.globals.initNode(\"/demo/tune/damper\", 1.0);",
     "props.globals.initNode(\"/demo/tune/damper\", 1.0);\n"
     "props.globals.initNode(\"/demo/tune/liftoff-pitch\", 0.0);"),
]


def main():
    apply = "--apply" in sys.argv
    restore = "--restore" in sys.argv
    if restore:
        shutil.copyfile(BAK, XML)
        print(f"[patch] restored from {BAK}")
        return 0
    text = XML.read_text(encoding="utf-8")
    if apply and not BAK.exists():
        shutil.copyfile(XML, BAK)
        print(f"[patch] backup -> {BAK}")
    changed = text
    ok = True
    for i, (old, new) in enumerate(REPLACEMENTS, 1):
        n = changed.count(old)
        if n != 1:
            print(f"[patch] REPLACEMENT {i}: 命中 {n} 次（需恰好 1 次）——中止")
            ok = False
            continue
        if apply:
            changed = changed.replace(old, new)
        print(f"[patch] REPLACEMENT {i}: OK")
        print("  - " + old.strip().splitlines()[0][:90])
        print("  + " + new.strip().splitlines()[0][:90])
    if apply and ok:
        ET.fromstring(changed)  # well-formed 校验（解析失败会抛异常）
        XML.write_text(changed, encoding="utf-8")
        print("[patch] APPLIED + XML well-formed OK. 需重启 fgfs 实例生效。")
    elif apply:
        print("[patch] 有替换未命中，未写盘。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
