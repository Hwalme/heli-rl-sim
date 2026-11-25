/* ==========================================================================
   驾驶舱操纵机构 · Cockpit Controls
   器具：周期变距杆（摇杆）/ 总距杆 / 脚蹬 / 按压开关 / 旋钮开关
   动力学依据：
     - platform/b4_cockpit_assets/B4_PROPOSAL.md §4：摇杆弹簧、总距杆档位/摩擦、脚踏回弹
     - interfaces/action-space.md v1-frozen §3.2/§3.3/§3.4/§4：
         速率上限 1.5 unit/s、P 增益 2.0 /s（τ_eq 0.5 s）、L0/L1 延迟档、
         collective=(j0+1.5)/3、cyclic/pedal=clip(j/0.8, −1, 1)、
         中位 (0.5, 0, 0, 0)、ESD-L2 覆盖为 (collective_当前, 0, 0, 0)
   ========================================================================== */
(function (global) {
  'use strict';

  var MONO = '"Consolas","JetBrains Mono","Courier New",monospace';

  /* ---------------- 手动操作记录 ---------------- */
  var INPUT_LOG_KEY = 'cockpit_input_log_v1';
  function recordInput(type, id, value) {
    try {
      var arr = JSON.parse(global.localStorage.getItem(INPUT_LOG_KEY) || '[]');
      arr.push({ t: Date.now(), type: type, id: id, value: +value, mode: (global.COCKPIT_STATE && global.COCKPIT_STATE.mode) || 'unknown' });
      if (arr.length > 5000) arr = arr.slice(arr.length - 5000);
      global.localStorage.setItem(INPUT_LOG_KEY, JSON.stringify(arr));
    } catch (e) { /* 隐私/容量模式禁用则静默 */ }
  }
  global.__recordCockpitInput = recordInput;

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
  function lerp(a, b, t) { return a + (b - a) * t; }

  /* ------------------------------------------------------------------ *
   * 单轴物理：spring（弹簧回中）/ friction（摩擦锁 + 档位）
   * ------------------------------------------------------------------ */
  function Axis(opts) {
    opts = opts || {};
    this.min = opts.min === undefined ? -1 : opts.min;
    this.max = opts.max === undefined ? 1 : opts.max;
    this.mode = opts.mode || 'spring';      // spring | friction
    this.k = opts.k === undefined ? 34 : opts.k;   // 弹簧刚度
    this.c = opts.c === undefined ? 7.5 : opts.c;  // 阻尼
    this.detents = opts.detents || 0;       // 机械档位数（0 = 无级）
    this.stiction = opts.stiction || 0.0;   // 静摩擦阈值（需克服才开始动）
    this.value = opts.value === undefined ? (this.mode === 'friction' ? (this.min + this.max) / 2 : 0) : opts.value;
    this.target = this.value;
    this.vel = 0;
    this.grabbed = false;
    this._pending = null;
  }

  Axis.prototype.range = function () { return this.max - this.min; };

  Axis.prototype.norm = function () {
    return (this.value - this.min) / (this.range() || 1);
  };

  /** 由指针位置（0..1 归一化行程）直接驱动，手指/鼠标跟手 */
  Axis.prototype.dragTo = function (n) {
    var raw = this.min + clamp(n, 0, 1) * this.range();
    if (this.detents > 0) {
      var step = this.range() / this.detents;
      raw = this.min + Math.round((raw - this.min) / step) * step;
    }
    if (this.stiction > 0 && !this.grabbed) {
      var d = raw - this.value;
      if (Math.abs(d) < this.stiction) return;
    }
    var old = this.value;
    this.value = clamp(raw, this.min, this.max);
    this.target = this.value;
    this.vel = 0;
    if (this._onChange && Math.abs(this.value - old) > 0.001) this._onChange(this.value);
  };

  Axis.prototype.release = function (rest) {
    // 手仍握在杆上时不得回弹——Controls.step() 每帧都会调用本函数，
    // 若此处不判 grabbed，弹簧会在拖拽过程中把杆拽回中位（表现为"拖不动"）。
    if (this.grabbed) return;
    // spring：松手回到中立位（rest）；friction：松手保持
    if (this.mode === 'spring') this.target = rest === undefined ? 0 : rest;
  };

  Axis.prototype.step = function (dt) {
    if (this.mode !== 'spring') return;   // 摩擦锁：位置保持不变
    var a = -this.k * (this.value - this.target) - this.c * this.vel;
    this.vel += a * dt;
    this.value += this.vel * dt;
    if (Math.abs(this.value - this.target) < 0.0015 && Math.abs(this.vel) < 0.02) {
      this.value = this.target;
      this.vel = 0;
    }
    this.value = clamp(this.value, this.min, this.max);
  };

  Axis.prototype.set = function (v) {
    this.value = clamp(v, this.min, this.max);
    this.target = this.value;
    this.vel = 0;
  };

  /* ------------------------------------------------------------------ *
   * 按压开关（自复位 momentary / 自锁 latching）
   * ------------------------------------------------------------------ */
  function PushButton(cfg) {
    this.id = cfg.id;
    this.label = cfg.label;
    this.cn = cfg.cn || '';
    this.mode = cfg.mode || 'latch';       // latch | momentary
    this.style = cfg.style || 'amber';     // amber | green | red | cyan
    this.on = !!cfg.on;
    this.danger = !!cfg.danger;            // 需二次确认
    this.el = null;
    this.onChange = cfg.onChange || null;
    this._pressed = false;
  }

  PushButton.prototype.mount = function (host) {
    var self = this;
    var b = document.createElement('button');
    b.className = 'sw-btn ' + this.style + (this.on ? ' on' : '');
    b.type = 'button';
    b.innerHTML = '<span class="sw-led"></span>' +
      '<span class="sw-txt"><b>' + this.label + '</b><i>' + this.cn + '</i></span>';
    b.addEventListener('pointerdown', function (e) { e.preventDefault(); self.press(); });
    b.addEventListener('pointerup', function () { self.unpress(); });
    b.addEventListener('pointerleave', function () { self.unpress(); });
    this.el = b;
    host.appendChild(b);
    return b;
  };

  PushButton.prototype.press = function () {
    this._pressed = true;
    this.el.classList.add('pressed');
    if (this.mode === 'momentary') {
      this.on = true;
      this._sync();
    }
  };

  PushButton.prototype.unpress = function () {
    if (!this._pressed) return;
    this._pressed = false;
    this.el.classList.remove('pressed');
    if (this.mode === 'momentary') {
      this.on = false;
      this._sync();
      this._fire();
      return;
    }
    // 自锁：按下抬起时翻转状态
    this.on = !this.on;
    this._sync();
    this._fire();
  };

  /* 外部强制置位（如急停被契约逻辑触发） */
  PushButton.prototype.set = function (v) {
    if (this.on === v) return;
    this.on = v;
    this._sync();
  };

  PushButton.prototype._sync = function () {
    if (this.el) this.el.classList.toggle('on', this.on);
  };

  PushButton.prototype._fire = function () {
    if (this.onChange) this.onChange(this.on, this);
    recordInput('button', this.id, this.on ? 1 : 0);
  };

  /* ------------------------------------------------------------------ *
   * 旋钮开关（多档位）
   * ------------------------------------------------------------------ */
  function Knob(cfg) {
    this.id = cfg.id;
    this.label = cfg.label;
    this.cn = cfg.cn || '';
    this.positions = cfg.positions || ['OFF', 'ON'];
    this.index = cfg.index || 0;
    this.onChange = cfg.onChange || null;
    this.el = null;
  }

  Knob.prototype.mount = function (host) {
    var self = this;
    var wrap = document.createElement('div');
    wrap.className = 'sw-knob';
    wrap.innerHTML =
      '<div class="knob-dial" tabindex="0"><div class="knob-ptr"></div></div>' +
      '<div class="knob-meta"><b>' + this.label + '</b><i>' + this.cn + '</i>' +
      '<span class="knob-pos">' + this.positions[this.index] + '</span></div>';
    var dial = wrap.querySelector('.knob-dial');
    var posEl = wrap.querySelector('.knob-pos');

    function rotate() {
      var t = self.positions.length > 1 ? self.index / (self.positions.length - 1) : 0;
      dial.style.setProperty('--ang', (-135 + t * 270).toFixed(1) + 'deg');
      posEl.textContent = self.positions[self.index];
    }
    this._rotate = rotate;
    rotate();

    function next(dir) {
      var n = self.positions.length;
      self.index = ((self.index + dir) % n + n) % n;
      rotate();
      if (self.onChange) self.onChange(self.index, self.positions[self.index], self);
      recordInput('knob', self.id, self.index);
    }
    dial.addEventListener('click', function () { next(1); });
    dial.addEventListener('wheel', function (e) {
      e.preventDefault();
      next(e.deltaY > 0 ? 1 : -1);
    }, { passive: false });
    dial.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { next(1); e.preventDefault(); }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { next(-1); e.preventDefault(); }
    });
    this.el = wrap;
    host.appendChild(wrap);
    return wrap;
  };

  /* ------------------------------------------------------------------ *
   * 主控制器：三套操纵机构 + 开关组 + 契约映射
   * ------------------------------------------------------------------ */
  function Controls(cfg) {
    cfg = cfg || {};
    // 周期变距杆：前后 = 俯仰，左右 = 滚转（弹簧回中）
    this.cyclicPitch = new Axis({ mode: 'spring', k: 34, c: 7.5, min: -1, max: 1 });
    this.cyclicPitch._inputId = 'cyclicPitch';
    this.cyclicRoll = new Axis({ mode: 'spring', k: 34, c: 7.5, min: -1, max: 1 });
    this.cyclicRoll._inputId = 'cyclicRoll';
    // 总距杆：[0,1]，摩擦锁 + 20 档机械档位，中位 0.5（契约 §4 中位语义）
    this.collective = new Axis({ mode: 'friction', min: 0, max: 1, value: 0.5, detents: 20, stiction: 0.004 });
    this.collective._inputId = 'collective';
    // 脚蹬：[-1,1]，左右踏板联动（左 = p，右 = −p），回弹刚度高于摇杆
    this.pedal = new Axis({ mode: 'spring', k: 46, c: 9.0, min: -1, max: 1 });
    this.pedal._inputId = 'pedal';

    this.trimPitch = 0;     // 配平（改变摇杆中立位）
    this.trimRoll = 0;
    this.lagModel = 'L0';   // L0 = 直接跟手；L1 = 一阶跟踪 + 速率钳位（契约 §3.3）
    this.estop = false;     // ESD 急停
    this.collectiveHold = 0.5;

    // arm 执行器状态（契约 §2/§3）：归一化关节目标 j0..j3
    this.j = [0, 0, 0, 0];
    this.jTarget = [0, 0, 0, 0];
    this.rateMax = 1.5;     // unit/s（契约 §3.2）
    this.pGain = 2.0;       // 1/s（契约 §3.3）
    this.holdSteps = 1;
    this._hold = [];

    this.follow = true;      // true = 跟随外部数据（FG 实际 / RL 回放），禁止手动操作
    this.takeover = false;   // true = 允许把操作写回仿真
    this.onOutput = cfg.onOutput || null;

    this.buttons = {};
    this.knobs = {};
    this._buildSwitches();
  }

  Controls.prototype._buildSwitches = function () {
    var self = this;
    this.switches = [
      new PushButton({
        id: 'starter', label: 'STARTER', cn: '启动按压', mode: 'momentary', style: 'green',
        onChange: function (on) { if (on) self._flash('启动马达已接通（瞬时）'); }
      }),
      new PushButton({
        id: 'brake', label: 'ROTOR BRK', cn: '旋翼刹车', mode: 'latch', style: 'amber'
      }),
      new PushButton({
        id: 'stabilizer', label: 'STAB AUG', cn: '增稳', mode: 'latch', style: 'green', on: true
      }),
      new PushButton({
        id: 'estop', label: 'EMER STOP', cn: '急停 ESD', mode: 'latch', style: 'red', danger: true,
        onChange: function (on) { self.estop = on; if (on) self.collectiveHold = self.collective.value; }
      })
    ];
    this.switchKnobs = [
      new Knob({
        id: 'mag', label: 'MAG SWITCH', cn: '磁电机', positions: ['OFF', 'R', 'L', 'BOTH', 'START'],
        index: 3
      }),
      new Knob({
        id: 'fuel', label: 'FUEL VALVE', cn: '燃油阀', positions: ['OFF', 'ON'], index: 1
      }),
      new Knob({
        id: 'lag', label: 'LAG MODEL', cn: '延迟档', positions: ['L0', 'L1'], index: 0,
        onChange: function (i, name) { self.lagModel = name; }
      }),
      new Knob({
        id: 'fric', label: 'COLL FRICTION', cn: '总距摩擦', positions: ['低', '中', '高'], index: 1,
        onChange: function (i) { self.collective.stiction = [0.002, 0.006, 0.014][i]; }
      })
    ];
    this.switches.forEach(function (b) { self.buttons[b.id] = b; });
    this.switchKnobs.forEach(function (k) { self.knobs[k.id] = k; });
  };

  Controls.prototype.mountSwitches = function (hostBtn, hostKnob) {
    var self = this;
    this.switches.forEach(function (b) { b.mount(hostBtn); });
    this.switchKnobs.forEach(function (k) { k.mount(hostKnob); });
    this._flashEl = document.createElement('div');
    this._flashEl.className = 'sw-flash';
    hostBtn.parentNode.appendChild(this._flashEl);
    return self;
  };

  Controls.prototype._flash = function (msg) {
    if (!this._flashEl) return;
    var el = this._flashEl;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(this._flashT);
    this._flashT = setTimeout(function () { el.classList.remove('show'); }, 2200);
  };

  /** 外部数据驱动（FG 实际值 / RL 回放动作），follow=true 时生效 */
  Controls.prototype.setFromData = function (d) {
    if (!this.follow) return;
    if (d.collective !== null && d.collective !== undefined) this.collective.set(d.collective);
    if (d.cyclicPitch !== null && d.cyclicPitch !== undefined) this.cyclicPitch.set(d.cyclicPitch);
    if (d.cyclicRoll !== null && d.cyclicRoll !== undefined) this.cyclicRoll.set(d.cyclicRoll);
    if (d.pedal !== null && d.pedal !== undefined) this.pedal.set(d.pedal);
  };

  Controls.prototype.step = function (dt) {
    dt = clamp(dt, 0, 0.05);
    this.cyclicPitch.release(-this.trimPitch);
    this.cyclicRoll.release(-this.trimRoll);
    this.pedal.release(0);
    this.cyclicPitch.step(dt);
    this.cyclicRoll.step(dt);
    this.pedal.step(dt);
    this._stepActuator(dt);
  };

  /** 契约 §3.2/§3.3：关节目标 → 速率钳位 + 一阶跟踪（L1）；L0 直通 */
  Controls.prototype._stepActuator = function (dt) {
    // 期望操纵量 → 关节目标（§4 逆映射）
    this.jTarget[0] = this.collective.value * 3.0 - 1.5;
    this.jTarget[1] = this.cyclicPitch.value * 0.8;
    this.jTarget[2] = this.cyclicRoll.value * 0.8;
    this.jTarget[3] = this.pedal.value * 0.8;

    if (this.lagModel === 'L0') {
      for (var i = 0; i < 4; i++) this.j[i] = this.jTarget[i];
      return;
    }
    for (var k = 0; k < 4; k++) {
      var err = this.jTarget[k] - this.j[k];
      var rate = clamp(this.pGain * err, -this.rateMax, this.rateMax);   // §3.2 先钳位再积分
      this.j[k] += rate * dt;
    }
  };

  /** 输出操纵量（§4 正映射）+ ESD 覆盖（§3.4） */
  Controls.prototype.output = function () {
    var coll = clamp((this.j[0] + 1.5) / 3.0, 0, 1);
    var cp = clamp(this.j[1] / 0.8, -1, 1);
    var cr = clamp(this.j[2] / 0.8, -1, 1);
    var pd = clamp(this.j[3] / 0.8, -1, 1);
    if (this.estop) {
      // ESD-L2：总距保持防掉高，姿态/航向指令归零
      coll = this.collectiveHold;
      cp = cr = pd = 0;
    }
    return {
      collective: coll, cyclicPitch: cp, cyclicRoll: cr, pedals: pd,
      joints: this.j.slice(),
      estop: this.estop,
      brake: this.buttons.brake.on,
      stabilizer: this.buttons.stabilizer.on,
      lag: this.lagModel
    };
  };

  /** 关节软限位告警（§3.1：j1–j3 ±1.45，j0 ±1.5） */
  Controls.prototype.softLimitWarn = function () {
    var lim = [1.5, 1.45, 1.45, 1.45];
    for (var i = 0; i < 4; i++) if (Math.abs(this.jTarget[i]) > lim[i]) return 'j' + i;
    return null;
  };

  /* ------------------------------------------------------------------ *
   * 绘制：周期变距杆 / 总距杆 / 脚蹬（Canvas）
   * ------------------------------------------------------------------ */
  function fit(canvas) {
    var cssW = canvas.clientWidth || canvas.parentNode.clientWidth || 200;
    var cssH = canvas.clientHeight || canvas.parentNode.clientHeight || 120;
    cssW = Math.round(cssW); cssH = Math.round(cssH);
    var dpr = Math.min(global.devicePixelRatio || 1, 2.5);
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    var ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    return { ctx: ctx, W: cssW, H: cssH };
  }

  function metalGrad(ctx, x0, y0, x1, y1) {
    var g = ctx.createLinearGradient(x0, y0, x1, y1);
    g.addColorStop(0, '#4a5568');
    g.addColorStop(0.28, '#8d9bb0');
    g.addColorStop(0.5, '#c9d6e6');
    g.addColorStop(0.72, '#7b889c');
    g.addColorStop(1, '#3d4655');
    return g;
  }

  function text(ctx, x, y, t, size, color, align) {
    ctx.save();
    ctx.font = size + 'px ' + MONO;
    ctx.fillStyle = color;
    ctx.textAlign = align || 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(t, x, y);
    ctx.restore();
  }

  /** 周期变距杆：俯视 + 侧倾的杆身，随操纵量倾斜，底部万向节 */
  function drawCyclic(canvas, ctl, label) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cx = W / 2, baseY = H * 0.80;
    var len = Math.min(W * 0.30, H * 0.55);
    var px = ctl.cyclicRoll.value, py = ctl.cyclicPitch.value;

    // 底座
    ctx.save();
    ctx.translate(cx, baseY);
    var bg = ctx.createRadialGradient(0, -2, 2, 0, 0, Math.min(W, H) * 0.30);
    bg.addColorStop(0, '#232c3a');
    bg.addColorStop(1, '#0c1119');
    ctx.beginPath();
    ctx.ellipse(0, 0, W * 0.30, H * 0.13, 0, 0, Math.PI * 2);
    ctx.fillStyle = bg;
    ctx.fill();
    ctx.strokeStyle = '#2b3546';
    ctx.lineWidth = 1;
    ctx.stroke();
    // 行程十字刻度
    ctx.strokeStyle = 'rgba(53,200,240,.22)';
    ctx.beginPath();
    ctx.moveTo(-W * 0.20, 0); ctx.lineTo(W * 0.20, 0);
    ctx.moveTo(0, -H * 0.09); ctx.lineTo(0, H * 0.09);
    ctx.stroke();
    ctx.restore();

    // 杆身（随操纵量倾斜）
    var tipX = cx + px * W * 0.26;
    var tipY = baseY - len + py * H * 0.16;
    ctx.save();
    ctx.lineCap = 'round';
    ctx.strokeStyle = metalGrad(ctx, cx - 6, baseY, cx + 6, baseY - len);
    ctx.lineWidth = Math.max(4, Math.min(W, H) * 0.075);
    ctx.beginPath();
    ctx.moveTo(cx, baseY);
    ctx.lineTo(tipX, tipY);
    ctx.stroke();
    // 杆身阴影
    ctx.strokeStyle = 'rgba(0,0,0,.35)';
    ctx.lineWidth = Math.max(1, Math.min(W, H) * 0.012);
    ctx.beginPath();
    ctx.moveTo(cx + 2, baseY);
    ctx.lineTo(tipX + 2, tipY);
    ctx.stroke();
    ctx.restore();

    // 万向节球
    ctx.beginPath();
    ctx.arc(cx, baseY, Math.max(4, Math.min(W, H) * 0.075), 0, Math.PI * 2);
    ctx.fillStyle = '#1a212c';
    ctx.fill();
    ctx.strokeStyle = '#4a5568';
    ctx.lineWidth = 1;
    ctx.stroke();

    // 握把
    ctx.save();
    ctx.translate(tipX, tipY);
    ctx.rotate(px * 0.35);
    var gw = Math.max(10, Math.min(W, H) * 0.20), gh = Math.max(14, Math.min(W, H) * 0.30);
    var gg = ctx.createLinearGradient(-gw / 2, 0, gw / 2, 0);
    gg.addColorStop(0, '#14181f');
    gg.addColorStop(0.35, '#39414f');
    gg.addColorStop(0.6, '#222833');
    gg.addColorStop(1, '#0e1218');
    ctx.beginPath();
    ctx.moveTo(-gw / 2, gh * 0.35);
    ctx.quadraticCurveTo(-gw / 2, -gh * 0.55, 0, -gh * 0.55);
    ctx.quadraticCurveTo(gw / 2, -gh * 0.55, gw / 2, gh * 0.35);
    ctx.quadraticCurveTo(0, gh * 0.55, -gw / 2, gh * 0.35);
    ctx.closePath();
    ctx.fillStyle = gg;
    ctx.fill();
    ctx.strokeStyle = '#5b6779';
    ctx.lineWidth = 1;
    ctx.stroke();
    // 握把顶部按钮（可按压的配平释放键）
    ctx.beginPath();
    ctx.arc(0, -gh * 0.34, Math.max(2, gw * 0.17), 0, Math.PI * 2);
    ctx.fillStyle = ctl.buttons.estop.on ? '#ff4d4d' : '#ffc043';
    ctx.fill();
    ctx.restore();

    // 读数
    var fs = clamp(Math.min(H * 0.12, W * 0.055), 8, 12);
    text(ctx, 6, H * 0.10, (label || 'CYCLIC') + ' 周期变距杆', fs, '#61728c');
    text(ctx, 6, H * 0.26, 'PITCH ' + (py >= 0 ? '+' : '') + py.toFixed(2), fs * 1.05, '#ffc043');
    text(ctx, W - 6, H * 0.26, 'ROLL ' + (px >= 0 ? '+' : '') + px.toFixed(2), fs * 1.05, '#ffc043', 'right');
    text(ctx, W / 2, H * 0.95, ctl.follow ? '跟随仿真数据（只读）' : (ctl.takeover ? '接管中 · 输出至仿真' : '本地演示（未写入）'),
      fs * 0.92, ctl.takeover ? '#35e08a' : '#5c6b83', 'center');
  }

  /** 总距杆：垂直行程，摩擦锁 + 档位刻度 */
  function drawCollective(canvas, ctl, label) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var px = W * 0.46;
    var top = H * 0.14, bot = H * 0.90;
    var travel = bot - top;
    var n = clamp(ctl.collective.norm(), 0, 1);
    var handleY = bot - n * travel;

    // 弧形行程槽
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.07)';
    ctx.lineWidth = Math.max(6, W * 0.055);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(px, top);
    ctx.lineTo(px, bot);
    ctx.stroke();
    // 档位齿
    if (ctl.collective.detents > 0) {
      ctx.strokeStyle = 'rgba(53,200,240,.20)';
      ctx.lineWidth = 1;
      for (var i = 0; i <= ctl.collective.detents; i++) {
        var y = bot - (i / ctl.collective.detents) * travel;
        var long = (i % 5 === 0);
        ctx.beginPath();
        ctx.moveTo(px + W * 0.035, y);
        ctx.lineTo(px + W * 0.035 + (long ? W * 0.10 : W * 0.05), y);
        ctx.stroke();
      }
    }
    ctx.restore();

    // 杆身（绕底部枢轴摆动）
    var pivotX = W * 0.16, pivotY = H * 0.94;
    ctx.save();
    ctx.lineCap = 'round';
    ctx.strokeStyle = metalGrad(ctx, pivotX - 4, pivotY, px + 4, handleY);
    ctx.lineWidth = Math.max(4, Math.min(W, H) * 0.070);
    ctx.beginPath();
    ctx.moveTo(pivotX, pivotY);
    ctx.lineTo(px, handleY);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(pivotX, pivotY, Math.max(3, W * 0.030), 0, Math.PI * 2);
    ctx.fillStyle = '#2b3546';
    ctx.fill();
    ctx.strokeStyle = '#6b7a90';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();

    // 总距握把（带旋转油门端）
    ctx.save();
    ctx.translate(px, handleY);
    var gw = Math.max(12, W * 0.26), gh = Math.max(9, H * 0.115);
    var gg = ctx.createLinearGradient(0, -gh / 2, 0, gh / 2);
    gg.addColorStop(0, '#3b4453');
    gg.addColorStop(0.45, '#1d232d');
    gg.addColorStop(1, '#0d1116');
    ctx.beginPath();
    ctx.moveTo(-gw * 0.5, -gh * 0.5);
    ctx.lineTo(gw * 0.5, -gh * 0.5);
    ctx.quadraticCurveTo(gw * 0.62, 0, gw * 0.5, gh * 0.5);
    ctx.lineTo(-gw * 0.5, gh * 0.5);
    ctx.quadraticCurveTo(-gw * 0.38, 0, -gw * 0.5, -gh * 0.5);
    ctx.closePath();
    ctx.fillStyle = gg;
    ctx.fill();
    ctx.strokeStyle = '#5b6779';
    ctx.lineWidth = 1;
    ctx.stroke();
    // 握把上的滚转thumb wheel
    ctx.beginPath();
    ctx.arc(gw * 0.30, 0, Math.max(2, gh * 0.28), 0, Math.PI * 2);
    ctx.fillStyle = '#0a0e14';
    ctx.fill();
    ctx.strokeStyle = '#7b889c';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();

    var fs = clamp(Math.min(H * 0.10, W * 0.055), 7.5, 11.5);
    text(ctx, 6, H * 0.055, (label || 'COLLECTIVE') + ' 总距杆', fs, '#61728c');
    text(ctx, W - 6, H * 0.055, (n * 100).toFixed(0) + '%', fs * 1.25, '#35e08a', 'right');
    text(ctx, 6, H * 0.97, '摩擦锁 · ' + ctl.collective.detents + ' 档', fs * 0.92, '#5c6b83');
    text(ctx, W - 6, H * 0.97, 'j0 ' + (ctl.j[0] >= 0 ? '+' : '') + ctl.j[0].toFixed(2), fs * 0.92, '#8fa3bd', 'right');
  }

  /** 脚蹬：左右踏板联动，中央枢轴，回弹 */
  function drawPedals(canvas, ctl, label) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cy = H * 0.60;
    var p = clamp(ctl.pedal.value, -1, 1);
    var pedalW = Math.max(16, W * 0.24), pedalH = Math.max(10, H * 0.19);
    var travel = Math.max(6, W * 0.10);
    var cxL = W * 0.26, cxR = W * 0.74;

    // 联动横杆
    ctx.save();
    ctx.strokeStyle = '#4a5568';
    ctx.lineWidth = Math.max(3, H * 0.035);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cxL + p * travel, cy);
    ctx.lineTo(cxR + p * travel, cy);
    ctx.stroke();
    ctx.restore();

    // 中央枢轴
    ctx.beginPath();
    ctx.arc(W / 2, cy, Math.max(4, H * 0.055), 0, Math.PI * 2);
    ctx.fillStyle = '#232c3a';
    ctx.fill();
    ctx.strokeStyle = '#6b7a90';
    ctx.lineWidth = 1;
    ctx.stroke();

    // 左右踏板（一进一退）
    function pedal(cx, active) {
      ctx.save();
      ctx.translate(cx + p * travel, cy + (active ? -H * 0.02 : H * 0.02));
      var g = ctx.createLinearGradient(0, -pedalH / 2, 0, pedalH / 2);
      g.addColorStop(0, '#39414f');
      g.addColorStop(1, '#12171f');
      ctx.beginPath();
      ctx.moveTo(-pedalW / 2, -pedalH / 2);
      ctx.lineTo(pedalW / 2, -pedalH / 2);
      ctx.lineTo(pedalW / 2, pedalH / 2);
      ctx.lineTo(-pedalW / 2, pedalH / 2);
      ctx.closePath();
      ctx.fillStyle = g;
      ctx.fill();
      ctx.strokeStyle = active ? '#ffc043' : '#5b6779';
      ctx.lineWidth = active ? 1.8 : 1;
      ctx.stroke();
      // 防滑纹
      ctx.strokeStyle = 'rgba(255,255,255,.10)';
      ctx.lineWidth = 1;
      for (var i = 1; i < 4; i++) {
        var yy = -pedalH / 2 + (pedalH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(-pedalW / 2 + 2, yy);
        ctx.lineTo(pedalW / 2 - 2, yy);
        ctx.stroke();
      }
      ctx.restore();
    }
    pedal(cxL, p < 0);
    pedal(cxR, p > 0);

    var fs = clamp(Math.min(H * 0.12, W * 0.05), 7.5, 11.5);
    text(ctx, 6, H * 0.13, (label || 'PEDALS') + ' 脚蹬（联动）', fs, '#61728c');
    text(ctx, W - 6, H * 0.13, (p >= 0 ? '+' : '') + p.toFixed(2), fs * 1.2, '#ffc043', 'right');
    text(ctx, W / 2, H * 0.93, '左 ' + (-p).toFixed(2) + '　右 ' + p.toFixed(2), fs * 0.95, '#8fa3bd', 'center');
  }

  /* ------------------------------------------------------------------ *
   * 指针拖拽绑定
   * ------------------------------------------------------------------ */
  function bindAxis(canvas, axis, readFn, controls) {
    var dragging = false;

    function pos(ev) {
      var r = canvas.getBoundingClientRect();
      return { x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height };
    }
    function down(ev) {
      if (controls.follow) { controls._flash('当前为跟随模式：请先在顶栏点「接管操纵」，回放期间不可手动操纵'); return; }
      dragging = true;
      axis.grabbed = true;
      canvas.setPointerCapture && canvas.setPointerCapture(ev.pointerId);
      canvas.classList.add('dragging');
      axis.dragTo(readFn(pos(ev)));
      recordInput('axis', axis._inputId || 'axis', axis.value);
      ev.preventDefault();
    }
    function move(ev) {
      if (!dragging) return;
      axis.dragTo(readFn(pos(ev)));
      recordInput('axis', axis._inputId || 'axis', axis.value);
      ev.preventDefault();
    }
    function up() {
      if (!dragging) return;
      dragging = false;
      axis.grabbed = false;
      canvas.classList.remove('dragging');
    }
    canvas.addEventListener('pointerdown', down);
    canvas.addEventListener('pointermove', move);
    canvas.addEventListener('pointerup', up);
    canvas.addEventListener('pointercancel', up);
    canvas.style.touchAction = 'none';
    canvas.style.cursor = 'grab';
  }

  /** 周期变距杆：单指针同时驱动俯仰（前后）与滚转（左右）两轴 */
  function bindStick(canvas, ctl) {
    var dragging = false;

    function pos(ev) {
      var r = canvas.getBoundingClientRect();
      return { x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height };
    }
    function apply(p) {
      // 向后拉杆 = 抬头为正；向右压杆 = 右滚为正
      ctl.cyclicRoll.dragTo(p.x);
      ctl.cyclicPitch.dragTo(p.y);
    }
    function down(ev) {
      if (ctl.follow) {
        ctl._flash('当前为跟随模式：请先在顶栏点「接管操纵」，回放期间不可手动操纵');
        return;
      }
      dragging = true;
      ctl.cyclicRoll.grabbed = true;
      ctl.cyclicPitch.grabbed = true;
      canvas.setPointerCapture && canvas.setPointerCapture(ev.pointerId);
      canvas.classList.add('dragging');
      apply(pos(ev));
      recordInput('stick', 'cyclic', [ctl.cyclicPitch.value, ctl.cyclicRoll.value]);
      ev.preventDefault();
    }
    function move(ev) {
      if (dragging) {
        apply(pos(ev));
        recordInput('stick', 'cyclic', [ctl.cyclicPitch.value, ctl.cyclicRoll.value]);
        ev.preventDefault();
      }
    }
    function up() {
      if (!dragging) return;
      dragging = false;
      ctl.cyclicRoll.grabbed = false;
      ctl.cyclicPitch.grabbed = false;
      canvas.classList.remove('dragging');
    }
    canvas.addEventListener('pointerdown', down);
    canvas.addEventListener('pointermove', move);
    canvas.addEventListener('pointerup', up);
    canvas.addEventListener('pointercancel', up);
    canvas.style.touchAction = 'none';
  }

  global.CockpitControls = {
    Axis: Axis,
    bindStick: bindStick,
    PushButton: PushButton,
    Knob: Knob,
    Controls: Controls,
    drawCyclic: drawCyclic,
    drawCollective: drawCollective,
    drawPedals: drawPedals,
    bindAxis: bindAxis,
    clamp: clamp,
    lerp: lerp
  };
})(window);
