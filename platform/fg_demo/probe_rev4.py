# -*- coding: utf-8 -*-
"""probe_rev4.py -- B1 rev4 Nasal inner-loop liveness probe (E track, 2026-08-28).

Usage: python probe_rev4.py [port]
Requires fgfs running with telnet on 127.0.0.1:5501 (see start_fg_demo.bat).
Reads /demo/stabilizer/ticks twice, 3 s apart. Growing ticks = inner loop alive.
Based on B1交接文档 §10 toolbox. ASCII output only (GBK console safe).
"""
import re
import socket
import sys
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5501


def main():
    s = socket.create_connection(('127.0.0.1', PORT), timeout=15)
    s.settimeout(2.0)
    time.sleep(0.5)

    def rd():
        out = b''
        while True:
            try:
                c = s.recv(65536)
            except socket.timeout:
                break
            if not c:
                break
            out += c
            if out.rstrip().endswith(b'>'):
                break
        return out.decode('utf-8', 'replace')

    rd()  # banner

    def get(p):
        s.sendall(f'get {p}\r\n'.encode())
        time.sleep(0.08)
        m = re.findall(r"'([^']*)'", rd())
        v = m[-1].strip() if m else '?'
        if v.lower() == 'true':
            return 1.0
        if v.lower() == 'false':
            return 0.0
        try:
            return float(v)
        except ValueError:
            return v

    t1 = get('/demo/stabilizer/ticks')
    time.sleep(3.0)
    t2 = get('/demo/stabilizer/ticks')

    print(f'ticks: {t1} -> {t2}')
    alive = isinstance(t1, float) and isinstance(t2, float) and t2 > t1
    if alive:
        print(f'INNER LOOP ALIVE  ({(t2 - t1) / 3.0:.1f} Hz effective)')
    else:
        print('INNER LOOP DEAD OR FROZEN (ticks not growing / unknown)')

    rpm = get('/rotors/main/rpm')
    crashed = get('/sim/crashed')
    agl = get('/position/altitude-agl-ft')
    active = get('/demo/stabilizer/active')
    print(f'rpm={rpm}  crashed={crashed}  agl_ft={agl}  stab_active={active}')
    print('VERDICT: ' + ('ALIVE -> route A continue (sign calibration next)'
                       if alive else 'DEAD -> check fgfs*.log, else close Nasal route (route B-shell mainline)'))


if __name__ == '__main__':
    main()
