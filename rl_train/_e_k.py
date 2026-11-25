import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

def trial(k, label, steps=4000):
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
        f.set_property_value('fcs/rudder-cmd-norm', 0.0)
        for i in range(steps):
            phi = f.get_property_value('attitude/phi-deg')
            theta = f.get_property_value('attitude/theta-deg')
            ail = float(np.clip(k * math.radians(phi), -0.30, 0.30))
            ele = float(np.clip(k * math.radians(theta), -0.30, 0.30))
            f.set_property_value('fcs/aileron-cmd-norm', ail)
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.run()
            if not math.isfinite(phi) or abs(phi) > 60 or abs(theta) > 60:
                L.append('%s k=%.2f: FAIL t+%.2fs phi=%.0f theta=%.0f' % (label, k, i*0.005, deg(phi), deg(theta)))
                break
        else:
            L.append('%s k=%.2f: SURVIVED %ds phi=%.1f theta=%.1f agl=%.2f vz=%+.2f' % (
                label, k, int(steps*0.005),
                deg(f.get_property_value('attitude/phi-deg')),
                deg(f.get_property_value('attitude/theta-deg')),
                f.get_property_value('position/h-agl-ft')*H.FT2M - agl0,
                f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS))
    return L

out = []
for k in (0.08, 0.15, 0.30):
    out += trial(k, 'P-only')
with open('_e_k_result.txt', 'w') as fo:
    fo.write('\n'.join(out) + '\n')
print('\n'.join(out))
