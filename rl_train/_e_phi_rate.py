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
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    # high-rate sampling: every physics step for 1.0 s after a tiny impulse
    f.set_property_value('fcs/aileron-cmd-norm', 0.02)
    for _ in range(20):
        f.run()
    f.set_property_value('fcs/aileron-cmd-norm', 0.0)
    prev_phi = deg(f.get_property_value('attitude/phi-deg'))
    L.append('post-impulse phi=%.2f deg' % prev_phi)
    for i in range(100):  # 0.5 s at 200 Hz
        f.run()
        phi = deg(f.get_property_value('attitude/phi-deg'))
        if i % 10 == 0:
            L.append('t+%04.2fs phi=%+9.2f deg  dphi/dt=%+8.2f deg/s' % (i*0.005, phi, (phi-prev_phi)/(0.005*10)))
        prev_phi = phi
with open('_e_phi_rate_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
