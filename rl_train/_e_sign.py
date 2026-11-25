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
for ele_cmd in (-0.30, +0.30):
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
        f.set_property_value('fcs/collective-cmd-norm', 0.706)
        f.set_property_value('fcs/elevator-cmd-norm', ele_cmd)
        th0 = deg(f.get_property_value('attitude/theta-deg'))
        traj = []
        for i in range(200):  # 1 s
            f.run()
            traj.append(deg(f.get_property_value('attitude/theta-deg')))
        L.append('ele=%+.2f: theta %.1f -> %.1f (dtheta/dt %+.1f deg/s over 1s)' % (
            ele_cmd, th0, traj[-1], (traj[-1]-th0)/1.0))
env.close() if False else None
with open('_e_sign_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
