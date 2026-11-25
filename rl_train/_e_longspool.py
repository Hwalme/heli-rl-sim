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
    # LONG spool 90 s like the reference probe
    for i in range(9000):
        f.run()
    rpm_series = []
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    f.set_dt(H.DT_PHYSICS)
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    tip = False
    for i in range(4000):  # 20 s episode
        rpm_series.append(f.get_property_value('propulsion/engine[0]/rotor-rpm'))
        phi = f.get_property_value('attitude/phi-deg')
        theta = f.get_property_value('attitude/theta-deg')
        ail = float(np.clip(0.06 * math.radians(phi), -0.30, 0.30))
        ele = float(np.clip(0.06 * math.radians(theta), -0.30, 0.30))
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.run()
        if not math.isfinite(phi) or abs(phi) > 60 or abs(theta) > 60:
            L.append('FAIL t+%.2fs phi=%.0f theta=%.0f' % (i*0.005, deg(phi), deg(theta)))
            tip = True
            break
    if not tip:
        L.append('SURVIVED: phi=%.1f theta=%.1f agl=%.2f vz=%+.2f' % (
            deg(f.get_property_value('attitude/phi-deg')),
            deg(f.get_property_value('attitude/theta-deg')),
            f.get_property_value('position/h-agl-ft')*H.FT2M - agl0,
            f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS))
    L.append('rpm tail mean=%.1f' % float(np.mean(rpm_series[-200:])))
env.close() if False else None
with open('_e_longspool_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
