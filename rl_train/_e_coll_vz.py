# -*- coding: utf-8 -*-
"""Measure steady vertical speed vs collective IN THE AIR (climb to ~15 m first).
This is the authoritative trim table for the altitude-hold loop: it tells us the
true hover collective and whether reducing collective actually arrests vz.
Attitude is held by the validated SAS (K_ATT=40) throughout.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

L = []
ROOT = H._find_jsbsim_root()
f = jsbsim.FGFDMExec(ROOT, None)
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
    for _ in range(4000):
        f.run()
        if f.get_property_value('propulsion/engine[0]/rotor-rpm') >= 495.0:
            break
    f.set_dt(H.DT_PHYSICS)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M

    def sas(coll, steps):
        phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
        theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
        for _ in range(steps):
            phi = math.radians(f.get_property_value('attitude/phi-deg'))
            theta = math.radians(f.get_property_value('attitude/theta-deg'))
            p = (phi - phi_prev) / H.DT_PHYSICS
            q = (theta - theta_prev) / H.DT_PHYSICS
            phi_prev, theta_prev = phi, theta
            ail = float(np.clip(-H.SAS_K_ATT * phi - H.SAS_K_RATE * p, -0.30, 0.30))
            ele = float(np.clip(+H.SAS_K_ATT * theta - H.SAS_K_RATE * q, -0.30, 0.30))
            f.set_property_value('fcs/aileron-cmd-norm', ail)
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.set_property_value('fcs/rudder-cmd-norm', 0.0)
            f.set_property_value('fcs/collective-cmd-norm', coll)
            f.run()

    # climb to ~15 m
    L.append('climbing to 15 m at coll=0.80 with SAS...')
    sas(0.80, 1600)
    L.append('  agl=%.1f m' % (f.get_property_value('position/h-agl-ft') * H.FT2M - agl0))
    L.append('')
    L.append('coll   steady_vz(m/s)  agl(m)   note')
    for coll in (0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45, 0.40):
        sas(coll, 800)          # 4 s settle
        vzs, agls = [], []
        phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
        theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
        for _ in range(400):    # 2 s measure
            phi = math.radians(f.get_property_value('attitude/phi-deg'))
            theta = math.radians(f.get_property_value('attitude/theta-deg'))
            p = (phi - phi_prev) / H.DT_PHYSICS
            q = (theta - theta_prev) / H.DT_PHYSICS
            phi_prev, theta_prev = phi, theta
            ail = float(np.clip(-H.SAS_K_ATT * phi - H.SAS_K_RATE * p, -0.30, 0.30))
            ele = float(np.clip(+H.SAS_K_ATT * theta - H.SAS_K_RATE * q, -0.30, 0.30))
            f.set_property_value('fcs/aileron-cmd-norm', ail)
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.set_property_value('fcs/collective-cmd-norm', coll)
            f.run()
            vzs.append(f.get_property_value('velocities/h-dot-fps') * H.FPS2MPS)
            agls.append(f.get_property_value('position/h-agl-ft') * H.FT2M - agl0)
        vz = float(np.mean(vzs))
        note = 'CLIMB' if vz > 0.5 else ('DESCEND' if vz < -0.5 else 'HOVER~')
        L.append('%.2f   %+8.2f      %6.2f   %s' % (coll, vz, float(np.mean(agls)), note))
        sys.stdout.flush()
env.close() if False else None
with open('_e_collvz_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
