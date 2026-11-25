# -*- coding: utf-8 -*-
"""驾驶舱操纵台行为验证（Playwright）。
校验点全部来自 interfaces/action-space.md v1-frozen §3.1/§3.2/§3.3/§3.4/§4
以及 platform/b4_cockpit_assets/B4_PROPOSAL.md §4 机械特性。
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8777/cockpit.html"
results = []


def chk(name, ok, detail=""):
    results.append((ok, name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))


def main():
    with sync_playwright() as p:
        br = p.chromium.launch(args=['--no-sandbox'])
        page = br.new_page(viewport={'width': 1920, 'height': 1080})
        errs = []
        page.on('pageerror', lambda e: errs.append("pageerror: " + str(e)))
        page.on('console', lambda m: errs.append("console.error: " + m.text)
                if m.type == 'error' else None)
        # 页面有 SSE 长连接，networkidle 永不触发
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_function("() => !!(window.CockpitControls && window.COCKPIT_CTL)", timeout=15000)
        page.wait_for_timeout(1200)

        print("\n=== A. 结构就位 ===")
        chk("CockpitControls + COCKPIT_CTL 已加载",
            page.evaluate("() => !!(window.CockpitControls && window.COCKPIT_CTL)"))
        nb = page.eval_on_selector_all('.sw-btn', "els => els.length")
        nk = page.eval_on_selector_all('.sw-knob', "els => els.length")
        chk("按压开关数量 >= 4", nb >= 4, "实际 %d" % nb)
        chk("旋钮开关数量 >= 4", nk >= 4, "实际 %d" % nk)
        sw = page.evaluate("() => Object.keys(window.COCKPIT_CTL.buttons)")
        kn = page.evaluate("() => Object.keys(window.COCKPIT_CTL.knobs)")
        print("     开关:", sw, " 旋钮:", kn)

        print("\n=== B. 三个操纵机构画布确有绘制 ===")
        ink_js = """(sel) => {
            const c = document.querySelector(sel);
            if (!c) return null;
            const g = c.getContext('2d');
            const d = g.getImageData(0, 0, c.width, c.height).data;
            let ink = 0, n = 0; const set = new Set();
            for (let i = 0; i < d.length; i += 4 * 13) {
                n++;
                if (d[i+3] > 8) { ink++; set.add((d[i]>>4)+','+(d[i+1]>>4)+','+(d[i+2]>>4)); }
            }
            return { w: c.width, h: c.height, ratio: ink / Math.max(1,n), colors: set.size };
        }"""
        for sel, label in [('#cvCyclic', '周期变距杆'), ('#cvCollective', '总距杆'), ('#cvPedals', '脚蹬')]:
            r = page.evaluate(ink_js, sel)
            if not r:
                chk(label + " 画布存在", False, "未找到 " + sel)
                continue
            # 画布透明（面板底色由 CSS 提供），器具本体占 10%~60% 属正常
            chk(label + " 画布已绘制",
                r['w'] > 40 and r['h'] > 40 and 0.08 < r['ratio'] < 0.95 and r['colors'] > 8,
                "%dx%d 覆盖%.0f%% 色阶%d" % (r['w'], r['h'], r['ratio'] * 100, r['colors']))

        print("\n=== C. 切到本地操纵（只读服务端下应可操纵） ===")
        page.click('#btnTakeover')
        page.wait_for_timeout(300)
        st = page.evaluate("() => ({t: window.COCKPIT_CTL.takeover, f: window.COCKPIT_CTL.follow,"
                           " note: (document.getElementById('npNote')||{}).textContent})")
        print("     takeover=%s follow=%s 铭牌=%s" % (st['t'], st['f'], st['note']))
        chk("只读模式下仍可进入本地操纵", st['t'] is True and st['f'] is False)
        chk("铭牌标明不回写", '不回写' in (st['note'] or ''), st['note'])

        print("\n=== D. 推拉周期变距杆 → §4 映射 ===")
        # 画布 y 轴向下：屏幕下方 = 向后拉杆 = 抬头为正
        box = page.locator('#cvCyclic').bounding_box()
        cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.move(cx + box['width'] * 0.30, cy + box['height'] * 0.30, steps=12)
        page.wait_for_timeout(700)
        out = page.evaluate("() => window.COCKPIT_CTL.output()")
        print("     右下拖输出 =", json.dumps(out, ensure_ascii=False))
        chk("右压 → 横向周期变距为正", out['cyclicRoll'] > 0.3, "cyclicRoll=%.3f" % out['cyclicRoll'])
        chk("后拉 → 纵向周期变距为正（抬头）", out['cyclicPitch'] > 0.3,
            "cyclicPitch=%.3f" % out['cyclicPitch'])
        # 反向：前推应为负
        page.mouse.move(cx, cy - box['height'] * 0.30, steps=10)
        page.wait_for_timeout(400)
        outb = page.evaluate("() => window.COCKPIT_CTL.output()")
        print("     前推输出 =", json.dumps(outb, ensure_ascii=False))
        chk("前推 → 纵向周期变距为负（低头）", outb['cyclicPitch'] < -0.3,
            "cyclicPitch=%.3f" % outb['cyclicPitch'])
        # 保持期间不应被弹簧拽回（回归：release 需在 grabbed 时让位）
        page.mouse.move(cx + box['width'] * 0.30, cy + box['height'] * 0.30, steps=6)
        page.wait_for_timeout(900)
        held = page.evaluate("() => window.COCKPIT_CTL.output()")
        print("     按住 0.9s 后仍应保持 =", json.dumps(held, ensure_ascii=False))
        chk("按住期间不被弹簧拉回中位", held['cyclicRoll'] > 0.3 and held['cyclicPitch'] > 0.3,
            "roll=%.3f pitch=%.3f" % (held['cyclicRoll'], held['cyclicPitch']))

        print("\n=== E. 松手回中（弹簧回弹，B4 §4） ===")
        page.mouse.up()
        page.wait_for_timeout(1600)
        out2 = page.evaluate("() => window.COCKPIT_CTL.output()")
        print("     松手后 =", json.dumps(out2, ensure_ascii=False))
        chk("松手后周期变距回中",
            abs(out2['cyclicRoll']) < 0.08 and abs(out2['cyclicPitch']) < 0.08,
            "roll=%.4f pitch=%.4f" % (out2['cyclicRoll'], out2['cyclicPitch']))

        print("\n=== F. 提放总距杆 + 摩擦保持（B4 §4 档位/摩擦） ===")
        cbox = page.locator('#cvCollective').bounding_box()
        mx, my = cbox['x'] + cbox['width'] / 2, cbox['y'] + cbox['height'] / 2
        page.mouse.move(mx, my)
        page.mouse.down()
        page.mouse.move(mx, my - cbox['height'] * 0.32, steps=10)   # 上提 = 增大
        page.wait_for_timeout(400)
        c_hi = page.evaluate("() => window.COCKPIT_CTL.collective.value")
        page.mouse.up()
        page.wait_for_timeout(1300)
        c_hold = page.evaluate("() => window.COCKPIT_CTL.collective.value")
        print("     上提时=%.3f  松手1.3s后=%.3f" % (c_hi, c_hold))
        chk("上提使总距增大", c_hi > 0.75, "%.3f" % c_hi)
        chk("松手后摩擦保持不回弹", abs(c_hold - c_hi) < 0.05, "Δ=%.4f" % abs(c_hold - c_hi))
        # 下压
        page.mouse.move(mx, my)
        page.mouse.down()
        page.mouse.move(mx, my + cbox['height'] * 0.32, steps=10)
        page.wait_for_timeout(400)
        c_lo = page.evaluate("() => window.COCKPIT_CTL.collective.value")
        page.mouse.up()
        print("     下压后=%.3f" % c_lo)
        chk("下压使总距减小", c_lo < 0.25, "%.3f" % c_lo)

        print("\n=== G. 脚蹬联动 ===")
        pbox = page.locator('#cvPedals').bounding_box()
        px, py = pbox['x'] + pbox['width'] / 2, pbox['y'] + pbox['height'] / 2
        page.mouse.move(px, py)
        page.mouse.down()
        page.mouse.move(px + pbox['width'] * 0.3, py, steps=10)
        page.wait_for_timeout(600)
        pv = page.evaluate("() => ({v: window.COCKPIT_CTL.pedal.value, o: window.COCKPIT_CTL.output().pedals})")
        page.mouse.up()
        page.wait_for_timeout(1300)
        pv2 = page.evaluate("() => window.COCKPIT_CTL.pedal.value")
        print("     右踩 pedal=%.3f → 输出 pedals=%.3f ；松手后=%.3f" % (pv['v'], pv['o'], pv2))
        chk("脚蹬右踩产生正航向指令", pv['o'] > 0.3, "%.3f" % pv['o'])
        chk("脚蹬松手回中（回弹）", abs(pv2) < 0.08, "%.4f" % pv2)

        print("\n=== H. 速率限制 |Δj| <= 1.5 unit/s（§3.2，先钳位再积分） ===")
        rl = page.evaluate("""() => {
            const c = window.COCKPIT_CTL;
            c.lagModel = 'L1'; c.estop = false;
            c.collective.value = 0.0;
            for (let i = 0; i < 400; i++) c.step(0.02);     // 稳定到 j0 = -1.5
            const j0start = c.j[0];
            c.collective.value = 1.0;                        // 阶跃到 j0 = +1.5（跨度 3.0）
            let maxRate = 0, prev = c.j[0];
            for (let i = 0; i < 400; i++) {
                c.step(0.01);
                const r = Math.abs(c.j[0] - prev) / 0.01;
                if (r > maxRate) maxRate = r;
                prev = c.j[0];
            }
            return { j0start: j0start, j0end: c.j[0], maxRate: maxRate };
        }""")
        print("     j0: %.3f → %.3f，峰值速率 %.4f unit/s" % (rl['j0start'], rl['j0end'], rl['maxRate']))
        chk("峰值速率 <= 1.5 unit/s", rl['maxRate'] <= 1.5 + 0.01, "%.4f" % rl['maxRate'])
        chk("速率限制确实生效（非瞬跳）", 1.4 < rl['maxRate'] <= 1.51, "%.4f" % rl['maxRate'])
        chk("2s 后到达目标 j0=+1.5", abs(rl['j0end'] - 1.5) < 0.02, "%.4f" % rl['j0end'])

        print("\n=== I. L0 / L1 延迟档（§3.3，P=2.0/s，τ_eq≈0.5s） ===")
        lag = page.evaluate("""() => {
            const c = window.COCKPIT_CTL;
            c.estop = false;
            const probe = (mode) => {
                c.lagModel = mode;
                c.collective.value = 0.5;
                for (let i = 0; i < 400; i++) c.step(0.02);
                const j0 = c.j[0];
                c.collective.value = 0.9;
                c.step(0.02);
                const one = Math.abs(c.j[0] - j0);
                for (let i = 0; i < 50; i++) c.step(0.02);   // 再走 1.0 s
                return { mode: mode, step1: one, after1s: Math.abs(c.j[0] - j0) };
            };
            return [probe('L0'), probe('L1')];
        }""")
        for r in lag:
            print("     %s：单步位移 %.5f，1.0s 后累计 %.4f" % (r['mode'], r['step1'], r['after1s']))
        l0 = next((r for r in lag if r['mode'] == 'L0'), None)
        l1 = next((r for r in lag if r['mode'] == 'L1'), None)
        if l0 and l1:
            chk("L0 无延迟（单步即到位）", abs(l0['step1'] - l0['after1s']) < 1e-6 and l0['step1'] > 0.02,
                "step1=%.5f" % l0['step1'])
            chk("L1 有跟随滞后（单步位移明显更小）", l1['step1'] < l0['step1'] * 0.2,
                "L0=%.5f L1=%.5f" % (l0['step1'], l1['step1']))
            chk("L1 一阶跟踪最终收敛", l1['after1s'] > l0['after1s'] * 0.8,
                "L0=%.4f L1=%.4f" % (l0['after1s'], l1['after1s']))
        else:
            chk("L0/L1 延迟档可切换", False, "未取到探针数据")

        print("\n=== J. ESD-L2 紧急保护（§3.4 → (collective_hold, 0, 0, 0)） ===")
        esd = page.evaluate("""() => {
            const c = window.COCKPIT_CTL;
            c.lagModel = 'L1'; c.estop = false; c.follow = false;
            c.collective.value = 0.8; c.cyclicPitch.value = 0.6;
            c.cyclicRoll.value = 0.6; c.pedal.value = 0.6;
            for (let i = 0; i < 400; i++) c.step(0.02);
            const normal = c.output();
            c.estop = true; c.collectiveHold = c.collective.value;
            for (let i = 0; i < 200; i++) c.step(0.02);
            const cut = c.output();
            c.estop = false;
            return { normal: normal, cut: cut };
        }""")
        print("     正常:", json.dumps(esd['normal'], ensure_ascii=False))
        print("     保护:", json.dumps(esd['cut'], ensure_ascii=False))
        chk("保护后周期变距/脚蹬归零",
            abs(esd['cut']['cyclicPitch']) < 1e-9 and abs(esd['cut']['cyclicRoll']) < 1e-9
            and abs(esd['cut']['pedals']) < 1e-9)
        chk("保护后总距保持防掉高（不归零）", esd['cut']['collective'] > 0.5,
            "collective=%.3f" % esd['cut']['collective'])

        print("\n=== K. 中立位契约 (0.5, 0, 0, 0)（§4） ===")
        neu = page.evaluate("""() => {
            const c = window.COCKPIT_CTL;
            c.lagModel = 'L1'; c.estop = false;
            c.collective.value = 0.5; c.cyclicPitch.value = 0;
            c.cyclicRoll.value = 0; c.pedal.value = 0;
            for (let i = 0; i < 500; i++) c.step(0.02);
            return c.output();
        }""")
        print("     中立输出 =", json.dumps(neu, ensure_ascii=False))
        chk("中立位 = (0.5, 0, 0, 0)",
            abs(neu['collective'] - 0.5) < 1e-6 and abs(neu['cyclicPitch']) < 1e-6
            and abs(neu['cyclicRoll']) < 1e-6 and abs(neu['pedals']) < 1e-6)

        print("\n=== L. 软限位（§3.1 j0±1.5 / j1-3±1.45） ===")
        sl = page.evaluate("""() => {
            const c = window.COCKPIT_CTL;
            c.estop = false; c.lagModel = 'L0';
            // 正常操纵范围内 jTarget 恒在限位内（j1-3 满舵仅 ±0.8）
            c.collective.value = 1.0; c.cyclicPitch.value = 1.0;
            c.cyclicRoll.value = 1.0; c.pedal.value = 1.0;
            c.step(0.02);
            const inRange = c.softLimitWarn();
            // 强制关节目标越限，检验守卫逻辑本身
            c.jTarget = [1.49, 1.44, 0, 0]; const justIn = c.softLimitWarn();
            c.jTarget = [1.51, 0, 0, 0];    const overJ0 = c.softLimitWarn();
            c.jTarget = [0, 1.46, 0, 0];    const overJ1 = c.softLimitWarn();
            c.jTarget = [0, 0, 0, 0];
            return { inRange: inRange, justIn: justIn, overJ0: overJ0, overJ1: overJ1 };
        }""")
        print("     ", sl)
        chk("满舵不误报（j1-3 仅 ±0.8）", sl['inRange'] is None, str(sl['inRange']))
        chk("限位内不告警", sl['justIn'] is None, str(sl['justIn']))
        chk("j0 越 ±1.5 告警", sl['overJ0'] == 'j0', str(sl['overJ0']))
        chk("j1 越 ±1.45 告警", sl['overJ1'] == 'j1', str(sl['overJ1']))

        print("\n=== M. 按压开关（真实指针事件） ===")
        # 按钮绑定 pointerdown/pointerup，合成 click() 不触发，必须用真实鼠标
        nbtn = page.eval_on_selector_all('.sw-btn', "els => els.length")
        modes = page.evaluate("() => { const o = {}; for (const k in window.COCKPIT_CTL.buttons) "
                              "o[k] = window.COCKPIT_CTL.buttons[k].mode; return o; }")
        bt = {}
        for i in range(nbtn):
            el = page.locator('.sw-btn').nth(i)
            name = el.locator('b').inner_text()
            bb = el.bounding_box()
            before = page.evaluate("i => document.querySelectorAll('.sw-btn')[i].classList.contains('on')", i)
            page.mouse.move(bb['x'] + bb['width'] / 2, bb['y'] + bb['height'] / 2)
            page.mouse.down()
            page.wait_for_timeout(80)
            mid = page.evaluate("i => document.querySelectorAll('.sw-btn')[i].classList.contains('on')", i)
            # 自锁按钮按下期间不跳变，只在松开时翻转；被按下本身体现为 .pressed
            pressed = page.evaluate(
                "i => document.querySelectorAll('.sw-btn')[i].classList.contains('pressed')", i)
            page.mouse.up()
            page.wait_for_timeout(80)
            released = page.evaluate(
                "i => !document.querySelectorAll('.sw-btn')[i].classList.contains('pressed')", i)
            page.wait_for_timeout(80)
            after = page.evaluate("i => document.querySelectorAll('.sw-btn')[i].classList.contains('on')", i)
            key = next((k for k, m in modes.items() if el.locator('b').inner_text().startswith(k.split()[0])), name)
            bt[name] = {'before': before, 'mid': mid, 'after': after,
                        'pressed': pressed, 'released': released}
        for k, v in bt.items():
            print("     %-12s 按下前=%-5s 按住中=%-5s 松开后=%-5s | 按下态=%s 抬起态=%s"
                  % (k, v['before'], v['mid'], v['after'], v['pressed'], v['released']))
        # STARTER 为瞬时：按住才通，松开即断；其余为自锁：松开后翻转并保持
        chk("按住期间按钮呈按下态", all(v['pressed'] for v in bt.values()),
            str({k: v['pressed'] for k, v in bt.items()}))
        chk("松开后按下态解除", all(v['released'] for v in bt.values()))
        chk("STARTER 瞬时：按住接通、松开断开",
            bt.get('STARTER', {}).get('mid') is True and bt.get('STARTER', {}).get('after') is False,
            str(bt.get('STARTER')))
        latches = {k: v for k, v in bt.items() if k != 'STARTER'}
        chk("自锁按钮松开后状态翻转",
            all(v['after'] != v['before'] for v in latches.values()),
            str({k: (v['before'], v['after']) for k, v in latches.items()}))
        # 再按一次应复位
        for i in range(nbtn):
            el = page.locator('.sw-btn').nth(i)
            if el.locator('b').inner_text() == 'STARTER':
                continue
            bb = el.bounding_box()
            page.mouse.click(bb['x'] + bb['width'] / 2, bb['y'] + bb['height'] / 2)
            page.wait_for_timeout(60)
        rst = page.evaluate("() => { const o = {}; for (const k in window.COCKPIT_CTL.buttons) "
                            "o[k] = window.COCKPIT_CTL.buttons[k].on; return o; }")
        print("     二次按压后状态 =", rst)
        chk("自锁按钮二次按压可复位",
            rst.get('brake') is False and rst.get('stabilizer') is True and rst.get('estop') is False,
            str(rst))

        kn = page.evaluate("""() => {
            const ks = Array.from(document.querySelectorAll('.sw-knob'));
            const out = [];
            for (const k of ks) {
                const dial = k.querySelector('.knob-dial');
                const pos = k.querySelector('.knob-pos');
                const name = k.querySelector('b').textContent;
                const before = pos.textContent, a0 = dial.style.getPropertyValue('--ang');
                dial.click();
                const mid = pos.textContent, a1 = dial.style.getPropertyValue('--ang');
                out.push({ name, before, mid, a0, a1,
                           all: Array.from(k.querySelectorAll('.knob-pos')).map(e => e.textContent) });
            }
            return out;
        }""")
        for k in kn:
            print("     %s: %s -> %s  (%s -> %s)" % (k['name'], k['before'], k['mid'], k['a0'], k['a1']))
        chk("全部旋钮可换档", all(k['before'] != k['mid'] for k in kn))
        chk("换档伴随指针转角变化", all(k['a0'] != k['a1'] for k in kn))

        print("\n=== N. 旋钮联动生效（延迟档 → lagModel，摩擦档 → stiction） ===")
        link = page.evaluate("""() => {
            const c = window.COCKPIT_CTL;
            const lagK = c.knobs.lag, friK = c.knobs.fric;
            const r = {};
            lagK.index = 1; lagK.onChange(1, 'L1'); r.lag1 = c.lagModel;
            lagK.index = 0; lagK.onChange(0, 'L0'); r.lag0 = c.lagModel;
            friK.index = 0; friK.onChange(0); r.fricLow = c.collective.stiction;
            friK.index = 2; friK.onChange(2); r.fricHigh = c.collective.stiction;
            friK.index = 1; friK.onChange(1); r.fricMid = c.collective.stiction;
            return r;
        }""")
        print("     ", link)
        chk("延迟档旋钮改变 lagModel", link['lag1'] == 'L1' and link['lag0'] == 'L0', str(link))
        chk("摩擦档旋钮改变总距静摩擦",
            link['fricLow'] < link['fricMid'] < link['fricHigh'],
            "低=%.4f 中=%.4f 高=%.4f" % (link['fricLow'], link['fricMid'], link['fricHigh']))

        print("\n=== O. 单屏无滚动 ===")
        for w, h in [(1920, 1080), (1600, 900), (1440, 900), (1366, 768)]:
            page.set_viewport_size({'width': w, 'height': h})
            page.wait_for_timeout(450)
            m = page.evaluate("""() => ({
                v: document.documentElement.scrollHeight - window.innerHeight,
                h: document.documentElement.scrollWidth - window.innerWidth })""")
            chk("%dx%d 无滚动条" % (w, h), m['v'] <= 1 and m['h'] <= 1,
                "溢出 v=%d h=%d" % (m['v'], m['h']))

        print("\n=== P. 运行期错误 ===")
        chk("无 JS 运行期错误", len(errs) == 0, errs[:4])

        br.close()

    print("\n" + "=" * 64)
    ok = sum(1 for r in results if r[0])
    print("结果: %d/%d 通过" % (ok, len(results)))
    if ok < len(results):
        print("未通过项:")
        for r in results:
            if not r[0]:
                print("  - %s :: %s" % (r[1], r[2]))
    print("=" * 64)
    return 0 if ok == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
