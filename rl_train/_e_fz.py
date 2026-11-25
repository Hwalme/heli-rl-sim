import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

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
    # candidate thrust properties
    cands = ['propulsion/thruster[0]/thrust-lbs','propulsion/engine[0]/thrust-lbs',
             'forces/fbz-aero-lbs','forces/fbz-total-lbs','forces/fbz-gear-lbs',
             'propulsion/installed-power-hp','propulsion/engine[0]/power-hp',
             'propulsion/engine[0]/torque-ftlb','propulsion/engine[0]/px-radps']
    for c in (0.0, 0.50, 0.706):
        f.set_property_value('fcs/collective-cmd-norm', c)
        for _ in range(300):
            f.run()
        L.append('coll=%.3f:' % c)
        for k in cands:
            v = f.get_property_value(k)
            L.append('  %-42s = %.4f' % (k, v))
env.close() if False else None
with open('_e_fz_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
