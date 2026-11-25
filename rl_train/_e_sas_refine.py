# -*- coding: utf-8 -*-
"""E A' refine: fine grid around the STABLE region (k~5-60, kd~0-0.1)
plus a LONG 60 s soak + disturbance test at the best point, to produce the
production SAS gains for helispike. ASCII output only.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import helispike_env as H
import jsbsim

ROOT = H._find_jsbsim_root()
L = []
DT = H.DT_PHYSICS


def run_case(k, kd, seconds=30.0, gust=None, log_tail=False):
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
        f.set_dt(DT)
        agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
        f.set_property_value('fcs/collective-cmd-norm', 0.706)
        phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
        theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
        steps = int(seconds / DT)
        max_att = 0.0
        tip_at = None
        att_track = []
        for i in range(steps):
            phi = math.radians(f.get_property_value('attitude/phi-deg'))
            theta = math.radians(f.get_property_value('attitude/theta-deg'))
            p_diff = (phi - phi_prev) / DT
            q_diff = (theta - theta_prev) / DT
            phi_prev, theta_prev = phi, theta
            ail = float(np.clip(-k * phi - kd * p_diff, -0.30, 0.30))
            ele = float(np.clip(+k * theta - kd * q_diff, -0.30, 0.30))
            if gust and i == int(0.5 * steps):
                # one-shot 0.5 s full-authority aileron disturbance
                ail = 0.30
            f.set_property_value('fcs/aileron-cmd-norm', ail)
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.set_property_value('fcs/rudder-cmd-norm', 0.0)
            f.run()
            dphi = f.get_property_value('attitude/phi-deg')
            dtheta = f.get_property_value('attitude/theta-deg')
            if not math.isfinite(dphi) or not math.isfinite(dtheta):
                tip_at = i * DT
                break
            m = max(abs(dphi), abs(dtheta))
            if i > int(2.0 / DT):
                max_att = max(max_att, m)
                if log_tail and i % int(5.0 / DT) == 0:
                    att_track.append(m)
            if m > 60.0:
                tip_at = i * DT
                break
        agl_end = f.get_property_value('position/h-agl-ft') * H.FT2M - agl0
        vz = f.get_property_value('velocities/h-dot-fps') * H.FPS2MPS
        return (tip_at is None), max_att, agl_end, vz, tip_at, att_track


def main():
    L.append('refine grid (30 s hover, coll=0.706):')
    L.append('   k     kd    verdict  max|att| end_agl    vz')
    best = None
    for k in (5.0, 10.0, 20.0, 40.0, 60.0):
        for kd in (0.0, 0.02, 0.05, 0.10):
            ok, max_att, agl_end, vz, tip_at, _ = run_case(k, kd)
            verdict = 'STABLE' if (ok and max_att < 15.0 and agl_end > 5.0) else (
                'marginal' if ok else 'TIP@%.1fs' % tip_at)
            L.append('%6.1f  %5.3f  %-8s %7.1f %7.2f %+6.2f' % (k, kd, verdict, max_att, agl_end, vz))
            sys.stdout.flush()
            if verdict == 'STABLE':
                score = max_att
                if best is None or score < best[0]:
                    best = (score, k, kd)
    L.append('BEST: %s' % (best,))
    if best:
        k, kd = best[1], best[2]
        L.append('--- soak test: 60 s at best (k=%.1f kd=%.3f) ---' % (k, kd))
        ok, max_att, agl_end, vz, tip_at, _ = run_case(k, kd, seconds=60.0)
        L.append('60 s: ok=%s max|att|=%.1f agl=%.1f vz=%+.2f' % (ok, max_att, agl_end, vz))
        L.append('--- gust test: 0.5 s full aileron at t=15 s ---')
        ok, max_att, agl_end, vz, tip_at, track = run_case(k, kd, seconds=40.0, gust=True, log_tail=True)
        L.append('gust: ok=%s max|att|=%.1f agl=%.1f vz=%+.2f' % (ok, max_att, agl_end, vz))
        L.append('  |att| every 5 s after gust: ' + ', '.join('%.1f' % v for v in track))
    print('\n'.join(L))


if __name__ == '__main__':
    main()
