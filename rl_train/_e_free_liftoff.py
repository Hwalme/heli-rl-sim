import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H

def deg(x):
    return math.degrees(x)

L = []
import jsbsim
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
    f.set_property_value('atmosphere/wind-north-fps', 0.0)
    f.set_property_value('atmosphere/wind-east-fps', 0.0)
    rpm = 0.0
    for i in range(4000):
        f.run()
        rpm = f.get_property_value('propulsion/engine[0]/rotor-rpm')
        if rpm >= 495.0:
            break
    L.append('SPOOL: rpm=%.1f @ t=%.1fs' % (rpm, i*0.01))
    f.set_dt(H.DT_PHYSICS)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    def props():
        return dict(n1=f.get_property_value('propulsion/engine[0]/n1'),
                    hp=f.get_property_value('propulsion/engine[0]/power-hp'),
                    rpm=f.get_property_value('propulsion/engine[0]/rotor-rpm'),
                    agl=f.get_property_value('position/h-agl-ft')*H.FT2M - agl0,
                    vz=f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS,
                    phi=deg(f.get_property_value('attitude/phi-deg')),
                    theta=deg(f.get_property_value('attitude/theta-deg')))
    # liftoff at 0.76 no SAS, 6 s, log every 1 s
    f.set_property_value('fcs/collective-cmd-norm', 0.76)
    for i in range(1200):
        f.run()
        if i % 200 == 0:
            d = props()
            L.append('t+%.1fs rpm=%.0f agl=%.2f vz=%+.2f phi=%+.1f theta=%+.1f hp=%.1f n1=%.0f'
                     % (i*0.005, d['rpm'], d['agl'], d['vz'], d['phi'], d['theta'], d['hp'], d['n1']))
            if not math.isfinite(d['phi']):
                L.append('NAN exploded'); break

with open('_e_free_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))

