import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

def trial(k, coll, label, steps=3600, filename=None):
    L = []
    ROOT = jsbsim.get_default_root_dir()
    f = jsbsim.FGFDMExec(ROOT, None)
    with H.SuppressJSBSimOutput():
        f.set_property_value('simulation/notify-level', 0)
        f.set_property_value('simulation/log-level', 0)
        f.disable_output()
        f.load_model('helispike', True)
        f.load_ic('reset00.xml', True)
        f.run_ic()
        f.set_property_value('propulsion/engine[0]/set-running', 1.0)
        f.set_property_value('propulsion/engine[1]/set-running', 1.0)
        f.set_property_value('propulsion/engine[0]/clutch', 1.0)
        f.set_property_value('fcs/hs/afcs-enable', 1.0)
        f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
        f.set_property_value('fcs/collective-cmd-norm', 0.0)
        for i in range(30000):
            f.run()
            rpm = f.get_property_value('propulsion/engine[0]/rotor-rpm')
            if 495 <= rpm <= 565:
                break
        agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
        hdg0 = f.get_property_value('attitude/psi-deg')
        f.set_property_value('fcs/collective-cmd-norm', coll)
        tip = False
        for i in range(steps):  # 30 s at 1/120
            phi = f.get_property_value('attitude/phi-deg')
            theta = f.get_property_value('attitude/theta-deg')
            hdg = f.get_property_value('attitude/psi-deg')
            ail = float(np.clip(k * math.radians(phi), -0.3, 0.3))
            ele = float(np.clip(k * math.radians(theta), -0.3, 0.3))
            rud = float(np.clip(0.08 * ((hdg - hdg0 + 540) % 360 - 180), -0.6, 0.6))
            f.set_property_value('fcs/aileron-cmd-norm', ail)
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.set_property_value('fcs/rudder-cmd-norm', rud)
            f.run()
            if not math.isfinite(phi) or abs(phi) > 45 or abs(theta) > 45:
                L.append('%s k=%.2f coll=%.3f: FAIL t+%.1fs phi=%.0f theta=%.0f'
                         % (label, k, coll, i/120.0, deg(phi), deg(theta)))
                tip = True; break
        if not tip:
            agl = f.get_property_value('position/h-agl-ft')*H.FT2M - agl0
            vz = f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS
            L.append('%s k=%.2f coll=%.3f: SURVIVED 30s phi=%+.1f theta=%+.1f agl=%.1f vz=%+.2f'
                     % (label, k, coll,
                        deg(f.get_property_value('attitude/phi-deg')),
                        deg(f.get_property_value('attitude/theta-deg')), agl, vz))
    return L

out = []
out += trial(1.2, 0.706, 'pos-k')
out += trial(1.2, 0.72,  'pos-k')
out += trial(2.0, 0.72,  'pos-k')
with open('_e_posk_result.txt', 'w') as fo:
    fo.write('\n'.join(out) + '\n')
print('\n'.join(out))
