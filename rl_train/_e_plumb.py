import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import helispike_env as H
import train_jsbsim

L = []
env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=False, seed=42)()
u = env.unwrapped
f = u.fdm
with H.SuppressJSBSimOutput():
    f.load_ic('reset00.xml', True)
    f.run_ic()
    f.set_dt(H.DT_PHYSICS)
    u._start_engine()
    f.disable_output()
    def p(name):
        return f.get_property_value(name)
    L.append('BEFORE SPOOL: rotor-rpm=%.1f throttle-cmd=%.3f coll-cmd=%.3f coll-rad=%.4f'
          % (p('propulsion/engine[0]/rotor-rpm'), p('fcs/throttle-cmd-norm[0]'),
             p('fcs/collective-cmd-norm'), p('propulsion/engine/collective-ctrl-rad')))
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for _ in range(1100):
        f.run()
    L.append('AFTER SPOOL:  rotor-rpm=%.1f throttle-cmd=%.3f coll-cmd=%.3f coll-rad=%.4f'
          % (p('propulsion/engine[0]/rotor-rpm'), p('fcs/throttle-cmd-norm[0]'),
             p('fcs/collective-cmd-norm'), p('propulsion/engine/collective-ctrl-rad')))
    u._agl_integrated = 0.0
    u._agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    u._psi_prev = math.radians(p('attitude/psi-deg'))
    u._phi_prev = math.radians(p('attitude/phi-deg'))
    u._theta_prev = math.radians(p('attitude/theta-deg'))
    u._p_diff = u._q_diff = u._r_diff = 0.0
    f.set_property_value('fcs/collective-cmd-norm', 0.80)
    f.run()
    L.append('AFTER SET 0.80: coll-cmd=%.3f coll-rad=%.4f'
          % (p('fcs/collective-cmd-norm'), p('propulsion/engine/collective-ctrl-rad')))
    for i in range(400):
        f.run()
    L.append('AFTER 2s @0.80: rotor-rpm=%.1f coll-rad=%.4f agl-ft=%.2f vz-fps=%.2f'
          % (p('propulsion/engine[0]/rotor-rpm'), p('propulsion/engine/collective-ctrl-rad'),
             p('position/h-agl-ft'), p('velocities/h-dot-fps')))
env.close()
with open('_e_plumb_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
