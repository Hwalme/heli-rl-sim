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
    # liveness: pulse aileron, read p-radps
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    for _ in range(60):
        f.run()
    f.set_property_value('fcs/aileron-cmd-norm', 0.05)
    for _ in range(20):
        f.run()
    f.set_property_value('fcs/aileron-cmd-norm', 0.0)
    vals = []
    for _ in range(40):
        f.run()
        vals.append(f.get_property_value('velocities/p-radps'))
    L.append('default-root p-radps during roll: max|p|=%.4f mean=%.5f -> %s'
             % (max(abs(v) for v in vals), float(np.mean([abs(v) for v in vals])),
                'ALIVE' if max(abs(v) for v in vals) > 1e-3 else 'DEAD'))
    # now full mixed-sign SAS with p/q rate terms (deg/s as v2 used: *57.3 handled by gains)
    f2_ready = True
    # reset for the real run
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
    roll_sign, pitch_sign, hdg_sign = -1.0, +1.0, +1.0
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    tip = False
    for i in range(12000):
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
        f.run()
        if i % 1200 == 0:
            agl = f.get_property_value('position/h-agl-ft')*H.FT2M - agl0
            L.append('t+%5.1fs agl=%7.1f phi=%+8.1f theta=%+8.1f' % (i/120.0, agl, deg(roll), deg(pitch)))
        if not math.isfinite(roll) or abs(roll) > 60 or abs(pitch) > 60:
            L.append('FAIL t+%.1fs' % (i/120.0)); tip = True; break
    if not tip:
        L.append('SURVIVED 100s: agl=%.1f phi=%.1f theta=%.1f' % (
            f.get_property_value('position/h-agl-ft')*H.FT2M - agl0,
            deg(f.get_property_value('attitude/phi-deg')),
            deg(f.get_property_value('attitude/theta-deg'))))
env.close() if False else None
with open('_e_alive_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
