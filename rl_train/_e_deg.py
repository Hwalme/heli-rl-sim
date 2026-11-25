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
    hdg0 = f.get_property_value('attitude/psi-deg')
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    tip = False
    phis, thetas, agls = [], [], []
    for i in range(4000):  # 20 s
        roll = f.get_property_value('attitude/phi-deg')
        pitch = f.get_property_value('attitude/theta-deg')
        hdg = f.get_property_value('attitude/psi-deg')
        ail = float(np.clip(0.06 * roll, -0.30, 0.30))       # 0.06 per DEGREE
        ele = float(np.clip(0.06 * pitch, -0.30, 0.30))      # 0.06 per DEGREE
        rud = float(np.clip(0.08 * ((hdg - hdg0 + 540) % 360 - 180), -0.60, 0.60))
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.set_property_value('fcs/rudder-cmd-norm', rud)
        f.run()
        phis.append(roll); thetas.append(pitch)
        agls.append(f.get_property_value('position/h-agl-ft')*H.FT2M - agl0)
        if not math.isfinite(roll) or abs(roll) > 60 or abs(pitch) > 60:
            L.append('FAIL t+%.2fs' % (i*0.005)); tip = True; break
    if not tip:
        L.append('SURVIVED 20s with ref-identical SAS (0.06/deg):')
        L.append('  tail phi=%.2f theta=%.2f agl=%.2f vz=%+.2f' % (
            float(np.mean(phis[-100:])), float(np.mean(thetas[-100:])),
            float(np.mean(agls[-100:])),
            f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS))
        L.append('  max |phi|=%.1f max |theta|=%.1f' % (max(abs(p) for p in phis), max(abs(t) for t in thetas)))
env.close() if False else None
with open('_e_deg_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
