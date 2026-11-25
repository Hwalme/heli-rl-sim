import sys, os, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import helispike_env as H
import train_jsbsim

def deg(x):
    return math.degrees(x)

L = []
env = train_jsbsim.make_env(task='heli_hover', action_mode='direct', wind=False, seed=42)()
u = env.unwrapped
f = u.fdm
with H.SuppressJSBSimOutput():
    f.load_ic('reset00.xml', True)
    f.run_ic()
    f.set_dt(0.01)
    u._start_engine()
    f.disable_output()
    f.set_property_value('atmosphere/wind-north-fps', 0.0)
    f.set_property_value('atmosphere/wind-east-fps', 0.0)
    f.set_property_value('fcs/collective-cmd-norm', 0.0)
    rpm = 0.0
    for i in range(4000):
        f.run()
        rpm = f.get_property_value('propulsion/engine[0]/rotor-rpm')
        if rpm >= 495.0:
            break
    L.append('SPOOL done: rpm=%.1f' % rpm)
    f.set_dt(H.DT_PHYSICS)
    u._agl_integrated = 0.0
    u._agl0 = f.get_property_value('position/h-agl-ft') * H.FT2M
    u._psi_prev = math.radians(f.get_property_value('attitude/psi-deg'))
    u._phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))
    u._theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))
    u._p_diff = u._q_diff = u._r_diff = 0.0

    def sas_step(coll_cmd, hdg_ref, dt=H.DT_PHYSICS):
        roll = f.get_property_value('attitude/phi-deg')
        pitch = f.get_property_value('attitude/theta-deg')
        hdg = f.get_property_value('attitude/psi-deg')
        ail = H.ROLL_SIGN * 0.06 * roll
        ele = H.PITCH_SIGN * 0.06 * pitch
        rud = H.HDG_SIGN * 0.08 * math.sin(math.radians(hdg - hdg_ref))
        f.set_property_value('fcs/aileron-cmd-norm', float(np.clip(ail, -0.3, 0.3)))
        f.set_property_value('fcs/elevator-cmd-norm', float(np.clip(ele, -0.3, 0.3)))
        f.set_property_value('fcs/rudder-cmd-norm', float(np.clip(rud, -0.6, 0.6)))
        f.set_property_value('fcs/collective-cmd-norm', coll_cmd)
        f.run()
        u._update_diffs()

    hdg0 = f.get_property_value('attitude/psi-deg')
    # decisive liftoff: coll straight to 0.76 with SAS, 10 s
    for _ in range(2000):
        sas_step(0.76, hdg0)
        u._update_diffs()
    s = u._raw()
    L.append('LIFTOFF@0.76 10s: agl=%.2f vz=%+.2f att=%.1f rpm=%.1f'
             % (s['agl'], s['vz'], max(abs(deg(s['pitch'])), abs(deg(s['roll']))), s['rpm']))
    # trim hover: adjust coll to hold agl, 15 s
    coll = 0.76
    for i in range(3000):
        s = u._raw()
        vz = s['vz']
        coll += (-0.02 if vz > 0.5 else (0.02 if vz < -0.5 else 0.0))
        coll = float(np.clip(coll, 0.60, 0.90))
        sas_step(coll, hdg0)
    s = u._raw()
    L.append('TRIM 15s: coll=%.3f agl=%.2f vz=%+.2f att=%.1f rpm=%.1f'
             % (coll, s['agl'], s['vz'], max(abs(deg(s['pitch'])), abs(deg(s['roll']))), s['rpm']))
    L.append('NOTE: heli_alt integration uses _update_diffs (agl_integrated).')
env.close()
with open('_e_sas_result.txt', 'w') as fo:
    fo.write('\n'.join(L) + '\n')
print('\n'.join(L))
