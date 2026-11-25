# -*- coding: utf-8 -*-
"""E A' grid: find an attitude-hold law that STABILIZES helispike in real hover.

Physics (calibrated by E static probes 2026-09-05):
  - positive aileron  -> phi increases   (roll restoring sign = MINUS on phi)
  - positive elevator -> theta decreases (pitch restoring sign = PLUS on theta)
  - p/q/r-radps are DEAD (const 0) in this build -> rates must come from
    attitude differences (same trick the env already uses in _update_diffs).
Law tested:  ail = clip(-k*phi_rad - kd*p_diff)
             ele = clip(+k*theta_rad - kd*q_diff)
Grid over (k, kd); each cell = spool to 495 rpm, then 30 s real liftoff hover.
ASCII output only.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

ROOT = H._find_jsbsim_root()
L = []

def run_case(k, kd, seconds=30.0, rud_on=False):
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
        hdg0 = f.get_property_value('attitude/psi-deg')
        f.set_property_value('fcs/collective-cmd-norm', 0.706)
        phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
        theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
        steps = int(seconds / H.DT_PHYSICS)
        max_att = 0.0
        airborne_steps = 0
        tip_at = None
        for i in range(steps):
            phi = math.radians(f.get_property_value('attitude/phi-deg'))
            theta = math.radians(f.get_property_value('attitude/theta-deg'))
            hdg = f.get_property_value('attitude/psi-deg')
            p_diff = (phi - phi_prev) / H.DT_PHYSICS
            q_diff = (theta - theta_prev) / H.DT_PHYSICS
            phi_prev, theta_prev = phi, theta
            ail = float(np.clip(-k * phi - kd * p_diff, -0.30, 0.30))
            ele = float(np.clip(+k * theta - kd * q_diff, -0.30, 0.30))
            if rud_on:
                rud = float(np.clip(0.08 * ((hdg - hdg0 + 540) % 360 - 180), -0.60, 0.60))
            else:
                rud = 0.0
            f.set_property_value('fcs/aileron-cmd-norm', ail)
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.set_property_value('fcs/rudder-cmd-norm', rud)
            f.run()
            dphi = f.get_property_value('attitude/phi-deg')
            dtheta = f.get_property_value('attitude/theta-deg')
            agl = f.get_property_value('position/h-agl-ft') * H.FT2M - agl0
            if agl > 2.0:
                airborne_steps += 1
            if not math.isfinite(dphi) or not math.isfinite(dtheta):
                tip_at = i * H.DT_PHYSICS
                break
            m = max(abs(dphi), abs(dtheta))
            if i > int(2.0 / H.DT_PHYSICS):  # ignore the initial pitch-up transient
                max_att = max(max_att, m)
            if m > 60.0:
                tip_at = i * H.DT_PHYSICS
                break
        if tip_at is None:
            agl_end = f.get_property_value('position/h-agl-ft') * H.FT2M - agl0
            vz = f.get_property_value('velocities/h-dot-fps') * H.FPS2MPS
            ok = (max_att < 15.0) and airborne_steps > steps * 0.8
            return ('STABLE' if ok else 'marginal'), max_att, agl_end, vz, tip_at
        return 'TIP', max_att, 0.0, 0.0, tip_at

def main():
    L.append('grid: ail=-k*phi-kd*p , ele=+k*theta-kd*q , 30 s real hover at coll=0.706')
    L.append(' k      kd     verdict   max|att|  end_agl   vz      tip_at')
    best = None
    for k in (0.5, 2.0, 5.0, 20.0):
        for kd in (0.05, 0.2, 0.5, 2.0, 5.0):
            verdict, max_att, agl_end, vz, tip_at = run_case(k, kd)
            L.append('%5.2f  %5.2f  %-9s %7.1f  %7.2f  %+6.2f  %s'
                     % (k, kd, verdict, max_att, agl_end, vz,
                        ('%.1fs' % tip_at) if tip_at is not None else '-'))
            sys.stdout.flush()
            if verdict == 'STABLE' and best is None:
                best = (k, kd)
    L.append('BEST_STABLE: %s' % (best,))
    print('\n'.join(L))

if __name__ == '__main__':
    main()
