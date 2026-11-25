# -*- coding: utf-8 -*-
"""E baseline probe: bypass Phase2 entirely. Spool -> coll=0.50 -> free 5 s.
If helo stays level & near hover, FDM is healthy and the bug is the Phase2
climb script; if it diverges, the FDM itself regressed since the 8/30 SPIKE pass.
ASCII output only. Writes to stdout in small bursts.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import helispike_env as H
import train_jsbsim

def deg(x):
    return math.degrees(x)

def main():
    for seed in [42, 123, 7]:
        env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=True, seed=seed)()
        u = env.unwrapped
        f = u.fdm
        with H.SuppressJSBSimOutput():
            f.load_ic('reset00.xml', True)
            f.run_ic()
            f.set_dt(H.DT_PHYSICS)
            u._start_engine()
            f.disable_output()
            f.set_property_value('simulation/notify-level', 0)
            f.set_property_value('simulation/log-level', 0)
            f.set_property_value('atmosphere/wind-north-fps', 0.0)
            f.set_property_value('atmosphere/wind-east-fps', 0.0)
            # spool up
            f.set_property_value('fcs/collective-cmd-norm', 0.0)
            for _ in range(1100):
                f.run()
            u._psi_prev = math.radians(f.get_property_value('attitude/psi-deg'))
            u._phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
            u._theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
            u._p_diff = u._q_diff = u._r_diff = 0.0
            u._agl_integrated = 0.0
            u._agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
            # direct hover collective, zero cyclic, 5 s free flight
            f.set_property_value('fcs/collective-cmd-norm', 0.50)
            f.set_property_value('fcs/elevator-cmd-norm', 0.0)
            f.set_property_value('fcs/aileron-cmd-norm', 0.0)
            f.set_property_value('fcs/rudder-cmd-norm', 0.0)
            track = []
            for i in range(1000):  # 5 s at 200 Hz
                f.run()
                if i % 200 == 0:
                    s = u._raw()
                    track.append((i * 0.005, deg(s['pitch']), deg(s['roll']), s['agl'], s['vz']))
        env.close()
        print('seed=%d coll=0.50 zero-cyclic 5s:' % seed)
        for t, th, ph, agl, vz in track:
            print('  t=%.1fs theta=%6.1f phi=%6.1f agl=%6.2f vz=%5.2f' % (t, th, ph, agl, vz))
        last = track[-1]
        ok = abs(last[2]) < 15 and abs(last[1]) < 15
        print('  -> %s' % ('LEVEL-STABLE' if ok else 'DIVERGED'))
        sys.stdout.flush()

if __name__ == '__main__':
    main()
