# -*- coding: utf-8 -*-
"""Trace the env's own Phase2 (not a re-implementation): log collective, vz, agl
every 100 substeps by monkey-patching nothing -- we instrument via a subclass
that reuses the real _reset_attempt through reset(), sampling fdm inside a
callback injected on the env instance.
"""
import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import helispike_env as H
import train_jsbsim

L = []

class Traced(H.HelispikeHoverEnv):
    def _reset_attempt(self, use_contract_dt):
        # wrap fdm.run to sample state each call
        f = self.fdm
        inner = f.run
        counter = {'i': 0}
        def traced_run():
            inner()
            counter['i'] += 1
            if counter['i'] % 100 == 0:
                s = self._raw()
                L.append('  i=%4d coll=%.3f agl=%6.2f vz=%+6.2f phi=%+6.1f theta=%+6.1f rpm=%.0f'
                         % (counter['i'], f.get_property_value('fcs/collective-cmd-norm'),
                            s['agl'], s['vz'],
                            math.degrees(s['roll']), math.degrees(s['pitch']), s['rpm']))
        f.run = traced_run
        try:
            return super()._reset_attempt(use_contract_dt)
        finally:
            f.run = inner

def main():
    env = Traced(action_mode='direct', wind=True, target_range=(15.0, 15.0))
    L.append('target=15 m; Phase2 trace (every 100 physics steps = 0.5 s):')
    obs, info = env.reset(seed=42)
    L.append('HANDOVER: ' + ' '.join('%.3f' % v for v in obs[:6]))
    s = env._raw()
    L.append('handover state: agl=%.2f vz=%+.2f phi=%.1f theta=%.1f rpm=%.0f target=%.1f'
             % (s['agl'], s['vz'], math.degrees(s['roll']),
                math.degrees(s['pitch']), s['rpm'], env.target_alt))
    env.close()
    print('\n'.join(L))

if __name__ == '__main__':
    main()
