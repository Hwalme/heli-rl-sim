import re
src = open('_e_baseline_probe.py', encoding='utf-8').read()
anchor = "            u._agl_integrated = 0.0\n"
inject = ("            u._psi_prev = math.radians(f.get_property_value('attitude/psi-deg'))\n"
          "            u._phi_prev = math.radians(f.get_property_value('attitude/phi-deg'))\n"
          "            u._theta_prev = math.radians(f.get_property_value('attitude/theta-deg'))\n"
          "            u._p_diff = u._q_diff = u._r_diff = 0.0\n")
src = src.replace(anchor, inject + anchor, 1)
open('_e_baseline_probe.py', 'w', encoding='utf-8').write(src)
print('patched')
