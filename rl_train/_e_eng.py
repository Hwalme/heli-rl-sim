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
    # engine[1] (tail rotor drive?) and engine[0] states
    L.append('t=0   e0-running=%.1f e1-running=%.1f e0-rpm=%.1f thr0-rpm=%.1f throttle=%.3f clutch0=%.1f'
      % (p('propulsion/engine[0]/set-running'), p('propulsion/engine[1]/set-running'),
         p('propulsion/engine[0]/rotor-rpm'), p('propulsion/thruster[0]/thruster-rpm'),
         p('fcs/throttle-cmd-norm[0]'), p('propulsion/engine[0]/clutch')))
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for i in range(1100):
        f.run()
        if i in (100, 400, 1099):
            L.append('t=%.2fs e0-rpm=%.1f thr0-rpm=%.1f e0-power=%.1f hp? thr0-thrust=%.1f wow=%.1f'
              % (i*0.005, p('propulsion/engine[0]/rotor-rpm'), p('propulsion/thruster[0]/thruster-rpm'),
                 p('propulsion/engine[0]/power-hp'), p('propulsion/thruster[0]/thrust-lbs'),
                 p('gear/unit[0]/WOW')))
    L.append('engine0 idle-rpm? nom? peek engine file params:')
env.close()
with open('_e_eng_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
