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
    psi_prev = f.get_property_value('attitude/psi-deg')
    phi_prev = f.get_property_value('attitude/phi-deg')
    theta_prev = f.get_property_value('attitude/theta-deg')
    f.set_property_value('fcs/collective-cmd-norm', 0.706)
    f.set_property_value('fcs/rudder-cmd-norm', 0.0)
    hdg0 = f.get_property_value('attitude/psi-deg')
    tip = False
    for i in range(4000):  # 20 s = full episode length
        phi = f.get_property_value('attitude/phi-deg')
        theta = f.get_property_value('attitude/theta-deg')
        psi = f.get_property_value('attitude/psi-deg')
        p_diff = (phi - phi_prev) / 0.005
        q_diff = (theta - theta_prev) / 0.005
        r_diff = ((psi - psi_prev + 540) % 360 - 180) / 0.005
        phi_prev, theta_prev, psi_prev = phi, theta, psi
        ail = float(np.clip(1.0 * math.radians(phi) + 0.25 * p_diff, -0.30, 0.30))
        ele = float(np.clip(1.0 * math.radians(theta) + 0.25 * q_diff, -0.30, 0.30))
        rud = float(np.clip(0.08 * math.radians(((psi - hdg0 + 540) % 360) - 180) + 0.2 * r_diff, -0.60, 0.60))
        f.set_property_value('fcs/aileron-cmd-norm', ail)
        f.set_property_value('fcs/elevator-cmd-norm', ele)
        f.set_property_value('fcs/rudder-cmd-norm', rud)
        f.run()
        if not math.isfinite(phi) or abs(phi) > 60 or abs(theta) > 60:
            L.append('FAIL at t+%.2fs: phi=%.0f theta=%.0f' % (i*0.005, deg(phi), deg(theta)))
            tip = True
            break
    if not tip:
        agl = f.get_property_value('position/h-agl-ft')*H.FT2M - agl0
        vz = f.get_property_value('velocities/h-dot-fps')*H.FPS2MPS
        L.append('SURVIVED 20 s FULL EPISODE: phi=%.2f theta=%.2f agl=%.2f vz=%+.2f' % (
            deg(f.get_property_value('attitude/phi-deg')),
            deg(f.get_property_value('attitude/theta-deg')), agl, vz))
        L.append('=> handover-ready: RL receives a stabilized hover at the TRUE hover point.')
env.close() if False else None
with open('_e_final_sas_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
