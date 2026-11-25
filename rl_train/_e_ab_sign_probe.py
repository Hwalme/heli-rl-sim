# -*- coding: utf-8 -*-
"""E A/B sign probe: is Phase2 roll feedback sign flipped?

For ROLL_SIGN in (+1, -1): reset heli_hover, dump handover state,
then 8 zero-action steps. Healthy handover = |phi|<15deg, |theta|<15deg,
agl>1m. Decides the one-line fix. ASCII output only.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

def deg(x):
    try:
        return math.degrees(x)
    except Exception:
        return float('nan')

def run_case(sign):
    import helispike_env
    import train_jsbsim
    helispike_env.ROLL_SIGN = float(sign)
    helispike_env.PITCH_SIGN = float(sign)  # flip pitch too: same suspicion applies
    print('===== ROLL_SIGN=PITCH_SIGN=%+d =====' % sign)
    results = []
    for seed in [42, 123, 7]:
        env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=True, seed=seed)()
        obs, info = env.reset(seed=seed)
        u = env.unwrapped
        s = u._raw()
        th, ph = deg(s['pitch']), deg(s['roll'])
        ok_handover = (abs(ph) < 15.0 and abs(th) < 15.0 and s['agl'] > 1.0)
        line1 = ('  seed=%d target=%.1fm HANDOVER theta=%6.1f phi=%6.1f agl=%5.2f vz=%5.2f rpm=%5.1f -> %s'
                 % (seed, info.get('target_alt', float('nan')), th, ph, s['agl'], s['vz'], s['rpm'],
                    'OK' if ok_handover else 'BAD'))
        r_sum = 0.0
        end = 'survived8'
        for i in range(8):
            obs, r, te, tr, i2 = env.step(np.zeros(4, dtype=np.float32))
            r_sum += r
            s2 = u._raw()
            if te or tr:
                end = 'END@%d crashed=%s r=%.1f' % (i + 1, i2.get('crashed'), r_sum)
                break
        if end == 'survived8':
            s3 = u._raw()
            end = 'survived8 (theta=%.1f phi=%.1f) cum_r=%.1f' % (deg(s3['pitch']), deg(s3['roll']), r_sum)
        print(line1)
        print('    zero-action: %s' % end)
        results.append(ok_handover)
        env.close()
    return all(results)

def main():
    verdict = {}
    for sign in (+1, -1):
        verdict[sign] = run_case(sign)
    print('===== VERDICT =====')
    for sign, ok in verdict.items():
        print('ROLL_SIGN=%+d : handover %s' % (sign, 'HEALTHY' if ok else 'UNHEALTHY'))

if __name__ == '__main__':
    main()
