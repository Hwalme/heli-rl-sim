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
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    # tiny impulse: aileron 0.01 for 0.2 s, then watch p-radps and phi
    f.set_property_value('fcs/aileron-cmd-norm', 0.01)
    for _ in range(40):
        f.run()
    f.set_property_value('fcs/aileron-cmd-norm', 0.0)
    L.append('impulse +0.01 ail applied (40 steps), watching free response:')
    for i in range(400):
        f.run()
        if i % 80 == 0:
            L.append('t+%05.2fs p=%+8.4f rad/s phi=%+8.1f deg lat-ctrl=%+7.4f rad'
                     % (i*0.005,
                        f.get_property_value('velocities/p-radps'),
                        deg(f.get_property_value('attitude/phi-deg')),
                        f.get_property_value('propulsion/engine/lateral-ctrl-rad')))
    # Now check the actual lateral command pipeline: is our aileron even alive?
    f.set_property_value('fcs/aileron-cmd-norm', 0.20)
    f.run()
    L.append('cmd ail=0.20 -> lat-ctrl-rad=%.4f (expect ~0.20 if pipeline alive)'
             % f.get_property_value('propulsion/engine/lateral-ctrl-rad'))
with open('_e_p_rad_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
