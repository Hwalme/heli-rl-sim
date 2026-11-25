import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

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
    hdg0 = f.get_property_value('attitude/psi-deg')
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    roll_sign, pitch_sign, hdg_sign = -1.0, +1.0, +1.0
    def sas(coll):
        roll = f.get_property_value('attitude/phi-deg')
        pitch = f.get_property_value('attitude/theta-deg')
        hdg = f.get_property_value('attitude/psi-deg')
        p = f.get_property_value('velocities/p-radps')
        q = f.get_property_value('velocities/q-radps')
        ail = roll_sign * (0.06 * roll + 0.15 * p)
        ele = pitch_sign * (0.06 * pitch + 0.15 * q)
        rud = hdg_sign * 0.08 * ((hdg - hdg0 + 540) % 360 - 180)
        f.set_property_value('fcs/aileron-cmd-norm', float(np.clip(ail, -0.3, 0.3)))
        f.set_property_value('fcs/elevator-cmd-norm', float(np.clip(ele, -0.3, 0.3)))
        f.set_property_value('fcs/rudder-cmd-norm', float(np.clip(rud, -0.6, 0.6)))
    # establishment 12 s at coll=0.72 (probeB exact)
    f.set_property_value('fcs/collective-cmd-norm', 0.72)
    for i in range(1440):
        sas(0.72); f.run()
        if i % 288 == 0:
            L.append('est t+%4.1fs agl=%6.1f phi=%+6.1f theta=%+6.1f' % (
                i/120.0, f.get_property_value('position/h-agl-ft')*H.FT2M-agl0,
                deg(f.get_property_value('attitude/phi-deg')),
                deg(f.get_property_value('attitude/theta-deg'))))
    # climb 60 s at 0.80
    f.set_property_value('fcs/collective-cmd-norm', 0.80)
    tip = False
    for i in range(7200):
        sas(0.80); f.run()
        if i % 720 == 0:
            L.append('clb t+%4.1fs agl=%6.1f phi=%+6.1f theta=%+6.1f' % (
                i/120.0, f.get_property_value('position/h-agl-ft')*H.FT2M-agl0,
                deg(f.get_property_value('attitude/phi-deg')),
                deg(f.get_property_value('attitude/theta-deg'))))
        roll = f.get_property_value('attitude/phi-deg')
        pitch = f.get_property_value('attitude/theta-deg')
        if not math.isfinite(roll) or abs(roll) > 30 or abs(pitch) > 30:
            L.append('FLIP t+%.1fs roll=%.0f pitch=%.0f' % (i/120.0, deg(roll), deg(pitch)))
            tip = True; break
    if not tip:
        L.append('PROBE-B PROFILE PASSED: no flip for 60s')
env.close() if False else None
with open('_e_pb_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
