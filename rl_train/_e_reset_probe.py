# -*- coding: utf-8 -*-
"""E reset-handover probe: is the env ALREADY tipped when the policy takes over?

Prints handover state after reset() for 5 seeds, then steps with ZERO action
and prints attitude/agl each control step. Decisive for the step-1 crash root cause.
ASCII output only.
"""
import sys, os, math, warnings, traceback
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import train_jsbsim

def deg(x):
    try:
        return math.degrees(x)
    except Exception:
        return float('nan')

def dump(tag, s):
    print('  %s theta=%7.1f deg  phi=%7.1f deg  agl=%6.2f m  vz=%6.2f m/s  rpm=%6.1f  wow=%.1f'
          % (tag, deg(s['pitch']), deg(s['roll']), s['agl'], s['vz'], s['rpm'], s.get('wow', float('nan'))))

def main():
    for seed in [42, 123, 7, 0, 99]:
        print('seed=%d' % seed)
        try:
            env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=True, seed=seed)()
            obs, info = env.reset(seed=seed)
            u = env.unwrapped
            s = u._raw()
            print('  target_alt=%.1f m' % info.get('target_alt', float('nan')))
            dump('HANDOVER', s)
            r_sum = 0.0
            for i in range(8):
                obs, r, te, tr, i2 = env.step(np.zeros(4, dtype=np.float32))
                r_sum += r
                s2 = u._raw()
                dump('step%d' % (i + 1), s2)
                if te or tr:
                    print('  EPISODE END after step %d  cum_r=%.1f  crashed=%s' % (i + 1, r_sum, i2.get('crashed')))
                    break
            if not (te or tr):
                print('  survived 8 steps, cum_r=%.1f' % r_sum)
            env.close()
        except Exception as e:
            print('  PROBE ERROR: %r' % e)
            traceback.print_exc()

if __name__ == '__main__':
    main()
