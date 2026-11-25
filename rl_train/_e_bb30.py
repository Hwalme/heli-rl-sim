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
    f.set_property_value('propulsion/engine[0]/set-running', 1.0)
    f.set_property_value('propulsion/engine[1]/set-running', 1.0)
    f.set_property_value('propulsion/engine[0]/clutch', 1.0)
    f.set_property_value('fcs/hs/afcs-enable', 1.0)
    f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for i in range(30000):
        f.run()
        rpm = f.get_property_value('propulsion/engine[0]/rotor-rpm')
        if 495 <= rpm <= 565:
            break
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    hdg0 = f.get_property_value('attitude/psi-deg')
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    tip = False
    for i in range(3600):  # 30 s
        theta = f.get_property_value('attitude/theta-deg')
        phi = f.get_property_value('attitude/phi-deg')
        hdg = f.get_property_value('attitude/psi-deg')
        # EXACT bang-bang law that survived: pitch only, roll zeroed
        ele = float(np.clip(-0.30 if theta > 0.02 else 0.30, -0.30, 0.30)) if abs(theta) > 0.02 else 0.0
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.set_property_value('fcs/aileron-cmd-norm', 0.0)
        f.set_property_value('fcs/rudder-cmd-norm', float(np.clip(0.08 * ((hdg - hdg0 + 540) % 360 - 180), -0.6, 0.6)))
        f.run()
        if i % 600 == 0:
            L.append('t+%5.1fs phi=%+7.1f theta=%+7.1f agl=%6.1f' % (
                i/120.0, deg(phi), deg(theta), f.get_property_value('position/h-agl-ft')*H.FT2M-agl0))
        if not math.isfinite(phi) or abs(phi) > 60 or abs(theta) > 60:
            L.append('FAIL t+%.1fs phi=%.0f theta=%.0f' % (i/120.0, deg(phi), deg(theta)))
            tip = True; break
    if not tip:
        L.append('SURVIVED 30s pitch-bang + roll-zero')
env.close() if False else None
with open('_e_bb30_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
