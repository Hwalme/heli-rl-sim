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
    L.append('post-run_ic: theta=%.3f phi=%.3f agl-ft=%.3f' % (
        deg(f.get_property_value('attitude/theta-deg')),
        deg(f.get_property_value('attitude/phi-deg')),
        f.get_property_value('position/h-agl-ft')))
    f.set_dt(0.01)
    f.set_property_value('propulsion/engine[0]/set-running', 1.0)
    f.set_property_value('propulsion/engine[1]/set-running', 1.0)
    f.set_property_value('propulsion/engine[0]/clutch', 1.0)
    f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for i in range(100):
        f.run()
    L.append('after 1s: theta=%.3f phi=%.3f' % (
        deg(f.get_property_value('attitude/theta-deg')),
        deg(f.get_property_value('attitude/phi-deg'))))
    # is theta stable at coll=0 on ground? continue 20 s, no controls
    for i in range(2000):
        f.run()
        if i % 400 == 0:
            L.append('t+%.1fs theta=%8.2f phi=%8.2f (coll=0, on ground)' % (
                i*0.005, deg(f.get_property_value('attitude/theta-deg')),
                deg(f.get_property_value('attitude/phi-deg'))))
env.close() if False else None
with open('_e_ic_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
