import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import helispike_env as H

L = []
import jsbsim
root = H._find_jsbsim_root()
with H.SuppressJSBSimOutput():
    f = jsbsim.FGFDMExec(root, None)
    f.set_property_value('simulation/notify-level', 0)
    f.set_property_value('simulation/log-level', 0)
    f.disable_output()
    f.load_model('helispike', True)
    f.load_ic('reset00.xml', True)
    f.run_ic()
    f.set_dt(H.DT_PHYSICS)
    f.set_property_value('propulsion/engine[0]/set-running', 1.0)
    f.set_property_value('propulsion/engine[0]/clutch', 1.0)
    f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    def p(name):
        return f.get_property_value(name)
    for i in range(6000):  # 30 s
        f.run()
        if i % 600 == 0:
            L.append('t=%5.1fs n1=%.1f power-hp=%.2f rotor-rpm=%.1f thr-rpm=%.1f thrust-lbs=%.1f'
              % (i*0.005, p('propulsion/engine[0]/n1'), p('propulsion/engine[0]/power-hp'),
                 p('propulsion/engine[0]/rotor-rpm'), p('propulsion/thruster[0]/thruster-rpm'),
                 p('propulsion/thruster[0]/thrust-lbs')))

with open('_e_long_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')

