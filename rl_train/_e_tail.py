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
    def dump(tag):
        L.append('%s' % tag)
    dump('props check:')
    for name in ['propulsion/engine[1]/rotor-rpm','propulsion/thruster[1]/thruster-rpm',
                 'propulsion/thruster[1]/thrust-lbs','propulsion/engine[1]/n1',
                 'propulsion/engine[1]/set-running','propulsion/engine[1]/clutch']:
        L.append('  %s = %.4f' % (name, f.get_property_value(name)))
    # tail rotor rpm during spool: engine[1] electric 1hp direct drive?
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    for _ in range(200):
        f.run()
    L.append('after 1s at hover coll:')
    for name in ['propulsion/thruster[1]/thruster-rpm','propulsion/thruster[1]/thrust-lbs',
                 'propulsion/engine[0]/rotor-rpm','propulsion/thruster[0]/thrust-lbs']:
        L.append('  %s = %.4f' % (name, f.get_property_value(name)))
    # moments: what torques act? check p-radps after all damping inputs known zero
    f.set_property_value('fcs/aileron-cmd-norm', 0.0)
    for _ in range(100):
        f.run()
    L.append('p-radps=%.5f (should be 0 in perfectly trimmed hover if no residual torque)'
             % f.get_property_value('velocities/p-radps'))
    L.append('lateral-ctrl-rad=%.5f  roll-trim=%.5f'
             % (f.get_property_value('propulsion/engine/lateral-ctrl-rad'),
                f.get_property_value('fcs/hs/roll-trim')))
    L.append('Main rotor sense=-1 with orient pitch=90: check if JSBSim FGRotor applies')
    L.append('an H-force / rolling moment sign per sense. p>0 => rolling right.')
env.close() if False else None
with open('_e_tail_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
