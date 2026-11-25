# -*- coding: utf-8 -*-
"""B-Shell 全科目实机演示验证：最新6条轨迹成功回合"""
import subprocess, sys, os, json, re, time

os.chdir(os.path.dirname(os.path.abspath(__file__)))
RESULTS = r"rl_train\results"

# 最新6条轨迹 + 成功episode（seed_42组）
TASKS = [
    ("hover",    "jsbsim_ppo_direct_trajectory.json",     42, 1),
    ("takeoff",  "jsbsim_ppo_takeoff_fg_trajectory.json", 42, 0),
    ("forward",  "jsbsim_ppo_forward_v3_trajectory.json", 42, 1),
    ("roll",     "jsbsim_ppo_roll_trajectory.json",       42, 0),
    ("yaw",      "jsbsim_ppo_yaw_trajectory.json",        42, 0),
    ("waypoint", "jsbsim_ppo_waypoint_trajectory.json",   42, 0),
]

results = []
for name, fname, seed, ep in TASKS:
    fpath = os.path.join(RESULTS, fname)
    print("\n=== %s (%s seed=%d ep=%d) ===" % (name, fname, seed, ep), flush=True)
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-u", "verify_replay.py",
         "--file", fpath, "--seed", str(seed), "--episode-index", str(ep),
         "--samples", "20"],
        capture_output=True, text=True, timeout=300
    )
    elapsed = time.time() - t0
    m = re.search(r'summary: (\{.*\})', proc.stdout)
    summary = {}
    if m:
        try: summary = json.loads(m.group(1))
        except: pass
    verdict = summary.get("verdict", "UNKNOWN")
    d_alt = summary.get("d_alt_ft", {})
    d_hdg = summary.get("d_hdg_deg", {})
    crashed = summary.get("fg_sim_crashed")
    print("  verdict=%s  alt_max=%.1fft  hdg_max=%.1fdeg  crashed=%s  time=%.0fs" % (
        verdict, d_alt.get("max_abs",0), d_hdg.get("max_abs",0), crashed, elapsed), flush=True)
    results.append((name, verdict, summary, fname))

print("\n" + "="*70, flush=True)
print("B-SHELL DEMO VERIFICATION SUMMARY (latest trajectories)", flush=True)
print("="*70, flush=True)
pass_count = 0
for name, verdict, summary, fname in results:
    flag = "PASS" if verdict == "PASS" else "FAIL"
    if verdict == "PASS": pass_count += 1
    print("  [%s] %-10s alt_max=%6.1fft  hdg_max=%6.1fdeg  crashed=%s" % (
        flag, name,
        summary.get("d_alt_ft",{}).get("max_abs",0),
        summary.get("d_hdg_deg",{}).get("max_abs",0),
        summary.get("fg_sim_crashed","?")), flush=True)
print("-"*70, flush=True)
print("  %d/%d PASS (all crashed=0 means demo visuals OK)" % (pass_count, len(results)), flush=True)
