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
for ail_cmd in (-0.10, +0.10):
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
        f.set_property_value('fcs/aileron-cmd-norm', ail_cmd)
        ph0 = deg(f.get_property_value('attitude/phi-deg'))
        traj = []
        for i in range(100):  # 0.5 s
            f.run()
            traj.append(deg(f.get_property_value('attitude/phi-deg')))
        L.append('ail=%+.2f: phi %.2f -> %.1f (rate %+.1f deg/s)' % (
            ail_cmd, ph0, traj[-1], (traj[-1]-ph0)/0.5))
env.close() if False else None
with open('_e_ailsign_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
