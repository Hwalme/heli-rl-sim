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
    for _ in range(400):  # 2 s at hover collective
        f.run()
    L.append('after 2s at coll=0.706:')
    L.append('  main thruster thrust-lbs = %.2f  (weight=1160 lb)' % f.get_property_value('propulsion/thruster[0]/thrust-lbs'))
    L.append('  main rotor-rpm = %.1f' % f.get_property_value('propulsion/engine[0]/rotor-rpm'))
    L.append('  main collective-ctrl-rad = %.4f' % f.get_property_value('propulsion/engine/collective-ctrl-rad'))
    # sweep the collective and record thrust at each point (1 s hold each)
    for c in (0.0, 0.30, 0.50, 0.60, 0.70, 0.75, 0.80):
        f.set_property_value('fcs/collective-cmd-norm', c)
        for _ in range(200):
            f.run()
        L.append('coll=%.2f: thrust=%8.2f lbs rpm=%.0f' % (
            c, f.get_property_value('propulsion/thruster[0]/thrust-lbs'),
            f.get_property_value('propulsion/engine[0]/rotor-rpm')))
env.close() if False else None
with open('_e_thrust_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
