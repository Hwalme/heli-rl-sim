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
    L.append('spool ok rpm=%.0f' % f.get_property_value('propulsion/engine[0]/rotor-rpm'))
    f.set_dt(H.DT_PHYSICS)
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    f.set_property_value('fcs/aileron-cmd-norm', 0.0)
    f.set_property_value('fcs/elevator-cmd-norm', 0.0)
    f.set_property_value('fcs/rudder-cmd-norm', 0.0)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    for i in range(2000):  # 10 s free at true hover coll, zero cyclic, NO SAS
        f.run()
        if i % 200 == 0:
            phi = deg(f.get_property_value('attitude/phi-deg'))
            theta = deg(f.get_property_value('attitude/theta-deg'))
            agl = f.get_property_value('position/h-agl-ft')*H.FT2M - agl0
            vz = f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS
            p_r = f.get_property_value('velocities/p-radps')
            L.append('t+%.1fs agl=%6.2f vz=%+5.2f phi=%+7.1f theta=%+7.1f p=%+6.2frad/s'
                     % (i*0.005, agl, vz, phi, theta, p_r))
env.close() if False else None
with open('_e_roll_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
