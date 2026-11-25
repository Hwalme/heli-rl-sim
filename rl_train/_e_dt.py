import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import jsbsim

def deg(x):
    return math.degrees(x)

def trial(dt_ctrl, label, filename):
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
        if dt_ctrl != 0.01:
            f.set_dt(dt_ctrl)
        agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
        f.set_property_value('fcs/collective-cmd-norm', 0.706)
        crashed = False
        for i in range(10000):  # 50 s (or 100 s at 0.01)
            theta = f.get_property_value('attitude/theta-deg')
            phi = f.get_property_value('attitude/phi-deg')
            ail = float(np.clip(0.06 * phi, -0.30, 0.30))
            ele = float(np.clip(0.06 * theta, -0.30, 0.30))
            f.set_property_value('fcs/aileron-cmd-norm', ail)
            f.set_property_value('fcs/elevator-cmd-norm', ele)
            f.run()
            if not math.isfinite(phi):
                L.append('NaN at step %d' % i)
                crashed = True
                break
            if abs(phi) > math.radians(60) or abs(theta) > math.radians(60):
                L.append('TIP>60deg at step %d (phi=%.0f theta=%.0f)' % (i, deg(phi), deg(theta)))
                crashed = True
                break
        agl = f.get_property_value('position/h-agl-ft')*H.FT2M - agl0
        vz = f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS
        L.append('%s dt=%.3f: survived=%s agl=%.2f vz=%+.2f phi=%.1f theta=%.1f'
                 % (label, dt_ctrl, not crashed, agl, vz,
                    deg(f.get_property_value('attitude/phi-deg')),
                    deg(f.get_property_value('attitude/theta-deg'))))
    with open(filename, 'w') as fo:
        fo.write('\n'.join(L) + '\n')
    print('\n'.join(L))

trial(0.005, 'SAS dt=0.005', '_e_dt005.txt')
trial(0.01,  'SAS dt=0.010', '_e_dt010.txt')
