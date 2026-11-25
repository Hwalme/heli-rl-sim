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
    f.set_dt(0.01)
    f.set_property_value('propulsion/engine[0]/set-running', 1.0)
    f.set_property_value('propulsion/engine[1]/set-running', 1.0)
    f.set_property_value('propulsion/engine[0]/clutch', 1.0)
    f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for i in range(4000):
        f.run()
        if f.get_property_value('propulsion/engine[0]/rotor-rpm') >= 495.0:
            break
    f.set_dt(H.DT_PHYSICS)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    tip = False
    for i in range(6000):  # 30 s at 0.005
        theta = f.get_property_value('attitude/theta-deg')
        phi = f.get_property_value('attitude/phi-deg')
        ail = float(np.clip(-0.30 if phi > 0 else 0.30, -0.30, 0.30)) if abs(phi) > 0.02 else 0.0
        ele = float(np.clip(-0.30 if theta > 0 else 0.30, -0.30, 0.30)) if abs(theta) > 0.02 else 0.0
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        # NO rudder writes
        f.run()
        if i % 600 == 0:
            L.append('t+%5.1fs phi=%+7.1f theta=%+7.1f agl=%6.1f' % (
                i*0.005, deg(phi), deg(theta),
                f.get_property_value('position/h-agl-ft')*H.FT2M-agl0))
        if not math.isfinite(phi) or abs(phi) > 60 or abs(theta) > 60:
            L.append('FAIL t+%.2fs phi=%.0f theta=%.0f' % (i*0.005, deg(phi), deg(theta)))
            tip = True; break
    if not tip:
        L.append('SURVIVED 30s (bang laws, no rudder writes)')
env.close() if False else None
with open('_e_bb30c_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
