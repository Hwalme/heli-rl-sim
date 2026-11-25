import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

L = []
root = H._find_jsbsim_root()
f = jsbsim.FGFDMExec(root, None)
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
    for i in range(4000):
        f.run()
        if f.get_property_value('propulsion/engine[0]/rotor-rpm') >= 495.0:
            break
    f.set_dt(H.DT_PHYSICS)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    # PURE yaw/heading hold test: hold coll 0.706, feed back ONLY heading,
    # use pitch AND roll channels together with the SAME sign to see which
    # combination resists the flip (the flip axis is probably pitch<->roll mixed)
    hdg0 = f.get_property_value('attitude/psi-deg')
    L.append('initial: theta=%.2f phi=%.2f psi=%.1f' % (
        deg(f.get_property_value('attitude/theta-deg')),
        deg(f.get_property_value('attitude/phi-deg')), hdg0))
    for i in range(2000):  # 10 s
        theta = f.get_property_value('attitude/theta-deg')
        phi = f.get_property_value('attitude/phi-deg')
        psi = f.get_property_value('attitude/psi-deg')
        # heading hold via BOTH channels (coordinate attitude hold on velocity vector)
        # simply damp phi and theta to zero with clipped saturated authority
        ail = float(np.clip(-0.30 if phi > 0 else 0.30, -0.30, 0.30)) if abs(phi) > 0.02 else 0.0
        ele = float(np.clip(-0.30 if theta > 0 else 0.30, -0.30, 0.30)) if abs(theta) > 0.02 else 0.0
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.run()
        if i % 200 == 0:
            L.append('t+%.1fs phi=%+9.1f theta=%+9.1f ail=%+5.2f ele=%+5.2f'
                     % (i*0.005, deg(phi), deg(theta), ail, ele))
            if not math.isfinite(phi):
                break
env.close() if False else None
with open('_e_bang_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
