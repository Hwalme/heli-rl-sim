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
    f.set_dt(H.DT_PHYSICS)
    f.set_property_value('propulsion/engine[0]/set-running', 1.0)
    f.set_property_value('propulsion/engine[1]/set-running', 1.0)
    f.set_property_value('propulsion/engine[0]/clutch', 1.0)
    f.set_property_value('fcs/throttle-cmd-norm[0]', 1.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    for i in range(10000):
        f.run()
        if f.get_property_value('propulsion/engine[0]/rotor-rpm') >= 495.0:
            break
    L.append('spool: rpm=%.0f t=%.1fs' % (f.get_property_value('propulsion/engine[0]/rotor-rpm'), i*0.005))
    agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    # Phase A: bang-bang attitude capture (10 s), same as the test that worked
    for i in range(2000):
        theta = f.get_property_value('attitude/theta-deg')
        phi = f.get_property_value('attitude/phi-deg')
        ail = float(np.clip(-0.30 if phi > 0.02 else 0.30, -0.30, 0.30)) if abs(phi) > 0.02 else 0.0
        ele = float(np.clip(-0.30 if theta > 0.02 else 0.30, -0.30, 0.30)) if abs(theta) > 0.02 else 0.0
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.run()
    L.append('after bang capture 10s: phi=%.2f theta=%.2f agl=%.2f' % (
        deg(f.get_property_value('attitude/phi-deg')),
        deg(f.get_property_value('attitude/theta-deg')),
        f.get_property_value('position/h-agl-ft')*H.FT2M - agl0))
    # Phase B: soft SAS around captured state (ele trim via slow integral)
    ele_trim = 0.0
    ail_trim = 0.0
    for i in range(6000):  # 30 s
        theta = f.get_property_value('attitude/theta-deg')
        phi = f.get_property_value('attitude/phi-deg')
        q = f.get_property_value('velocities/q-radps')
        p = f.get_property_value('velocities/p-radps')
        ele_trim += 0.00002 * theta   # slow trim integrator
        ail_trim += 0.00002 * phi
        ele_trim = float(np.clip(ele_trim, -0.30, 0.30))
        ail_trim = float(np.clip(ail_trim, -0.30, 0.30))
        ele = float(np.clip(ele_trim - 0.5*q, -0.30, 0.30))
        ail = float(np.clip(ail_trim - 0.5*p, -0.30, 0.30))
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        if not math.isfinite(theta):
            L.append('NaN at %d' % i); break
        if abs(theta) > math.radians(60) or abs(phi) > math.radians(60):
            L.append('TIP at %d: phi=%.0f theta=%.0f ele_trim=%.3f ail_trim=%.3f' % (i, deg(phi), deg(theta), ele_trim, ail_trim))
            break
    else:
        L.append('PHASE B SURVIVED 30 s: phi=%.2f theta=%.2f ele_trim=%+.3f ail_trim=%+.3f agl=%.2f vz=%+.2f' % (
            deg(f.get_property_value('attitude/phi-deg')),
            deg(f.get_property_value('attitude/theta-deg')),
            ele_trim, ail_trim,
            f.get_property_value('position/h-agl-ft')*H.FT2M - agl0,
            f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS))
env.close() if False else None
with open('_e_capture_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
