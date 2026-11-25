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
    keys = ['propulsion/engine[0]/n1','propulsion/engine[0]/power-hp','propulsion/engine[0]/rotor-rpm',
            'propulsion/thruster[0]/thrust-lbs','propulsion/engine[1]/n1',
            'propulsion/starter','propulsion/engine[0]/starter','propulsion/engine[0]/cranking',
            'fcs/throttle-cmd-norm[0]','propulsion/engine[0]/set-running']
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for i in range(2400):  # 12 s
        f.run()
        if i in (200, 600, 1200, 2399):
            vals = ' '.join('%s=%.2f' % (k.split('/')[-1], p(k)) for k in keys)
            L.append('t=%5.2fs %s' % (i*0.005, vals))
env.close()
with open('_e_eng2_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
