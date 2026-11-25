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
    # hold att via high-gain SAS directly in loop at 200Hz (like ref probe but stronger)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    hdg0 = f.get_property_value('attitude/psi-deg')
    for i in range(3000):  # 15 s
        phi = f.get_property_value('attitude/phi-deg')
        theta = f.get_property_value('attitude/theta-deg')
        ail = float(np.clip(1.2 * phi + 0.3 * f.get_property_value('velocities/p-radps'), -0.30, 0.30))
        ele = float(np.clip(1.2 * theta + 0.3 * f.get_property_value('velocities/q-radps'), -0.30, 0.30))
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.run()
        if i % 300 == 0:
            agl = f.get_property_value('position/h-agl-ft')*H.FT2M - agl0
            vz = f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS
            L.append('t+%.1fs agl=%7.2f vz=%+6.2f phi=%+8.2f theta=%+8.2f ail=%+6.3f'
                     % (i*0.005, agl, vz, deg(phi), deg(theta), ail))
env.close() if False else None
with open('_e_sas2_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
