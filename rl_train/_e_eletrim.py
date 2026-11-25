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
for ele_hold in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
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
        f.set_property_value('fcs/aileron-cmd-norm', 0.0)
        f.set_property_value('fcs/elevator-cmd-norm', ele_hold)
        thetas = []
        for i in range(2000):  # 10 s static ele
            f.run()
            thetas.append(deg(f.get_property_value('attitude/theta-deg')))
        tail = float(np.mean(thetas[-200:]))
        L.append('static ele=%+.2f -> tail theta=%+7.1f deg (min %.1f max %.1f)'
                 % (ele_hold, tail, min(thetas), max(thetas)))
env.close() if False else None
with open('_e_eletrim_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
