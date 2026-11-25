import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

L = []
root = H._find_jsbsim_root()
f = jsbsim.FGFDMExec(root, None)
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
    for i in range(6000):  # 30 s
        theta = f.get_property_value('attitude/theta-deg')
        phi = f.get_property_value('attitude/phi-deg')
        ail = float(np.clip(-0.30 if phi > 0.02 else 0.30, -0.30, 0.30)) if abs(phi) > 0.02 else 0.0
        ele = float(np.clip(-0.30 if theta > 0.02 else 0.30, -0.30, 0.30)) if abs(theta) > 0.02 else 0.0
        f.set_property_value('fcs/aileron-cmd-norm', ail)   # <-- explicitly every step
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.run()
        if not math.isfinite(theta) or abs(phi) > math.radians(60) or abs(theta) > math.radians(60):
            L.append('FAIL at %d: phi=%.0f theta=%.0f' % (i, deg(phi), deg(theta)))
            tip = True
            break
    if not tip:
        L.append('SURVIVED 30 s: phi=%.1f theta=%.1f agl=%.2f vz=%+.2f' % (
            deg(f.get_property_value('attitude/phi-deg')),
            deg(f.get_property_value('attitude/theta-deg')),
            f.get_property_value('position/h-agl-ft')*H.FT2M - agl0,
            f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS))
env.close() if False else None
with open('_e_ail_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
