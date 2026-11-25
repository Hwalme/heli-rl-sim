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
for trial in range(3):
    f = jsbsim.FGFDMExec(root, None)
    with H.SuppressJSBSimOutput():
        f.set_property_value('simulation/notify-level', 0)
        f.set_property_value('simulation/log-level', 0)
        f.disable_output()
        f.load_model('helispike', True)
        f.load_ic('reset00.xml', True)
        f.run_ic()
        f.set_dt(H.DT_PHYSICS)
        f.set_property_value('propulsion/engine[0]/set-running', 1.0)
        f.set_property_value('propulsion/engine[1]/set-running', 1.0)
        f.set_property_value('propulsion/engine[0]/clutch', 1.0)
        f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
        f.set_property_value('fcs/collective-cmd-norm', 0.0)
        for i in range(10000):
            f.run()
            if f.get_property_value('propulsion/engine[0]/rotor-rpm') >= 495.0:
                break
        f.set_property_value('fcs/collective-cmd-norm', 0.706)
        died = None
        for i in range(2000):
            theta = f.get_property_value('attitude/theta-deg')
            phi = f.get_property_value('attitude/phi-deg')
            ele = float(np.clip(-0.30 if theta > 0.02 else 0.30, -0.30, 0.30)) if abs(theta) > 0.02 else 0.0
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.run()
            if not math.isfinite(theta):
                died = i; break
        L.append('trial %d: died_at=%s' % (trial, died))
env.close() if False else None
with open('_e_det_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
