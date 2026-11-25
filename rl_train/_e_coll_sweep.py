# -*- coding: utf-8 -*-
"""E collective sweep: find the real hover collective of helispike FDM.
Free flight, zero cyclic, coll in 0.40..0.90, 6 s each. Records steady vz & agl.
If hover point > policy max (0.70), the hover task is infeasible as contracted.
ASCII output only.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import helispike_env as H
import train_jsbsim

def deg(x):
    return math.degrees(x)

def run_coll(coll_val):
    env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=False, seed=42)()
    u = env.unwrapped
    f = u.fdm
    with H.SuppressJSBSimOutput():
        f.load_ic('reset00.xml', True)
        f.run_ic()
        f.set_dt(H.DT_PHYSICS)
        u._start_engine()
        f.disable_output()
        f.set_property_value('simulation/notify-level', 0)
        f.set_property_value('simulation/log-level', 0)
        f.set_property_value('atmosphere/wind-north-fps', 0.0)
        f.set_property_value('atmosphere/wind-east-fps', 0.0)
        f.set_property_value('fcs/collective-cmd-norm', 0.0)
        for _ in range(1100):
            f.run()
        u._agl_integrated = 0.0
        u._agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
        u._psi_prev = math.radians(f.get_property_value('attitude/psi-deg'))
        u._phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
        u._theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
        u._p_diff = u._q_diff = u._r_diff = 0.0
        f.set_property_value('fcs/collective-cmd-norm', coll_val)
        f.set_property_value('fcs/elevator-cmd-norm', 0.0)
        f.set_property_value('fcs/aileron-cmd-norm', 0.0)
        f.set_property_value('fcs/rudder-cmd-norm', 0.0)
        agls, vzs = [], []
        diverged = False
        for i in range(1200):  # 6 s
            f.run()
            s = u._raw()
            if abs(deg(s['roll'])) > 45 or abs(deg(s['pitch'])) > 45:
                diverged = True
                break
            agls.append(s['agl'])
            vzs.append(s['vz'])
        tail_vz = float(np.mean(vzs[-200:])) if len(vzs) >= 200 else float(np.mean(vzs)) if vzs else float('nan')
        tail_agl = float(np.mean(agls[-200:])) if agls else 0.0
    env.close()
    return tail_vz, tail_agl, diverged

def main():
    print('coll  tail_vz(m/s)  tail_agl(m)  diverged>45deg')
    for c in [0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        vz, agl, div = run_coll(c)
        print('%.2f  %7.2f  %7.2f  %s' % (c, vz, agl, 'YES' if div else 'no'))
        sys.stdout.flush()
    print('NOTE: hover coll = smallest coll with tail_vz ~ 0 while airborne (agl>0.5).')
    print('      policy max = 0.70 (HOVER_COLLECTIVE 0.50 + u_coll*0.20).')

if __name__ == '__main__':
    main()
