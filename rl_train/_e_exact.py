import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

L = []
ROOT = jsbsim.get_default_root_dir()
f = jsbsim.FGFDMExec(ROOT, None)
with H.SuppressJSBSimOutput():
    f.set_property_value('simulation/notify-level', 0)
    f.set_property_value('simulation/log-level', 0)
    f.disable_output()
    f.load_model('helispike', True)
    f.load_ic('reset00.xml', True)
    f.run_ic()
    f.run_ic()  # ref-style second IC
    f.set_property_value('propulsion/engine[0]/set-running', 1.0)
    f.set_property_value('propulsion/engine[1]/set-running', 1.0)
    f.set_property_value('propulsion/engine[0]/clutch', 1.0)
    f.set_property_value('fcs/hs/afcs-enable', 1.0)
    f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for i in range(30000):
        f.run()
        if i > 1440 and f.get_property_value('propulsion/engine[0]/rotor-rpm') >= 495.0:
            break
    L.append('spool steps=%d' % i)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    hdg0 = f.get_property_value('attitude/psi-deg')
    f.set_property_value('fcs/collective-cmd-norm', 0.76)
    tip = False
    for i in range(12000):  # 100 s at default dt
        roll = f.get_property_value('attitude/phi-deg')
        pitch = f.get_property_value('attitude/theta-deg')
        hdg = f.get_property_value('attitude/psi-deg')
        ail = float(np.clip(0.06 * roll, -0.30, 0.30))
        ele = float(np.clip(0.06 * pitch, -0.30, 0.30))
        rud = float(np.clip(0.08 * ((hdg - hdg0 + 540) % 360 - 180), -0.60, 0.60))
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.set_property_value('fcs/rudder-cmd-norm', rud)
        f.run()
        if i % 1200 == 0:
            agl = f.get_property_value('position/h-agl-ft')*H.FT2M - agl0
            L.append('t+%5.1fs agl=%7.1f phi=%+8.1f theta=%+8.1f' % (i/120.0, agl, deg(roll), deg(pitch)))
        if not math.isfinite(roll) or abs(roll) > 60 or abs(pitch) > 60:
            L.append('FAIL t+%.1fs' % (i/120.0)); tip = True; break
    if not tip:
        L.append('SURVIVED 100s')
env.close() if False else None
with open('_e_exact_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
