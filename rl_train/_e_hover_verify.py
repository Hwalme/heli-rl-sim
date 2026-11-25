# -*- coding: utf-8 -*-
"""E hover-point verification with CONTRACT spool discipline.
Spool at coll=0, dt=0.01, until rpm>=495 (cap 40 s sim). Then hold coll steps
0.60/0.70/0.75/0.80/0.85 (5 s each, zero cyclic, no wind) and a 2 s ground
stability check at coll=0.55. ASCII out via result file.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import helispike_env as H
import train_jsbsim

def deg(x):
    return math.degrees(x)

L = []
env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=False, seed=42)()
u = env.unwrapped
f = u.fdm
with H.SuppressJSBSimOutput():
    f.load_ic('reset00.xml', True)
    f.run_ic()
    f.set_dt(0.01)
    u._start_engine()
    f.disable_output()
    f.set_property_value('atmosphere/wind-north-fps', 0.0)
    f.set_property_value('atmosphere/wind-east-fps', 0.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    spool_steps = 0
    rpm = 0.0
    for i in range(4000):
        f.run()
        spool_steps = i + 1
        rpm = f.get_property_value('propulsion/engine[0]/rotor-rpm')
        if rpm >= 495.0:
            break
    L.append('SPOOL: %d steps (%.1f s sim), rpm=%.1f' % (spool_steps, spool_steps * 0.01, rpm))
    f.set_dt(H.DT_PHYSICS)
    u._agl_integrated = 0.0
    u._agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    u._psi_prev = math.radians(f.get_property_value('attitude/psi-deg'))
    u._phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
    u._theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
    u._p_diff = u._q_diff = u._r_diff = 0.0

    def hold_coll(coll_val, n_steps, tag):
        f.set_property_value('fcs/collective-cmd-norm', coll_val)
        f.set_property_value('fcs/elevator-cmd-norm', 0.0)
        f.set_property_value('fcs/aileron-cmd-norm', 0.0)
        f.set_property_value('fcs/rudder-cmd-norm', 0.0)
        agls, vzs, atts = [], [], []
        for _ in range(n_steps):
            f.run()
            u._update_diffs()
            s = u._raw()
            agls.append(s['agl'])
            vzs.append(s['vz'])
            atts.append(max(abs(deg(s['pitch'])), abs(deg(s['roll']))))
        L.append('%s coll=%.2f: tail_vz=%+.2f m/s tail_agl=%.2f m max_att=%.1f deg'
                 % (tag, coll_val, float(np.mean(vzs[-100:])), float(np.mean(agls[-100:])), max(atts)))

    hold_coll(0.55, 400, 'GROUND')
    for c in (0.60, 0.70, 0.75, 0.80, 0.85):
        hold_coll(c, 1000, 'HOVER?')
env.close()
with open('_e_hover_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
