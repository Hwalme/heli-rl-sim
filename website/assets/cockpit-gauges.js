/* ==========================================================================
   驾驶舱仪表绘制库（Canvas 2D，零依赖）
   所有 draw* 函数签名统一为 (ctx, W, H, s)，W/H 为 CSS 逻辑像素。
   s 为归一化后的飞行状态对象，缺失字段一律为 null —— 绘制时输出 "–"，绝不补零。
   ========================================================================== */
(function (global) {
  'use strict';

  var C = {
    face: '#0b111a',
    face2: '#070c13',
    rim: '#2a3444',
    rimHi: '#4a5a72',
    tick: '#c3d2e6',
    tickDim: '#5f708a',
    txt: '#e2ecf8',
    txtDim: '#7e91ab',
    amber: '#ffc043',
    amberDim: '#8a6819',
    green: '#35e08a',
    greenDim: '#14603a',
    cyan: '#35c8f0',
    red: '#ff4d4d',
    warn: '#ffa32a',
    sky: '#2f86d8',
    sky2: '#9ad8f7',
    ground: '#96602c',
    ground2: '#d09a55',
    white: '#ffffff'
  };

  var MONO = '"Consolas","JetBrains Mono","Courier New",monospace';

  /* ------------------------- 基础工具 ------------------------- */

  function num(v, d) {
    if (v === null || v === undefined || (typeof v === 'number' && !isFinite(v))) return '–';
    return Number(v).toFixed(d === undefined ? 0 : d);
  }

  function fmt(v, d, dash) {
    var s = num(v, d);
    if (s === '–' && dash) return dash;
    return s;
  }

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function rad(d) { return d * Math.PI / 180; }

  /** DPR 自适应：尺寸由容器决定（单屏布局下高度由 grid 分配，不再用固定宽高比） */
  function fit(canvas) {
    var host = canvas.parentNode;
    var cssW = canvas.clientWidth || (host ? host.clientWidth : 0);
    var cssH = canvas.clientHeight || (host ? host.clientHeight : 0);
    if (!(cssW > 8) || !(cssH > 8)) {
      var rb = canvas.getBoundingClientRect();
      cssW = rb.width || 240; cssH = rb.height || 160;
    }
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

  /** 圆形表盘的玻璃面：外圈金属环 + 内表面渐变 + 顶部高光 */
  function dialFace(ctx, cx, cy, r) {
    var g = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.45, r * 0.1, cx, cy, r * 1.15);
    g.addColorStop(0, '#1a2331');
    g.addColorStop(0.55, C.face);
    g.addColorStop(1, C.face2);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = g;
    ctx.fill();

    ctx.lineWidth = Math.max(2, r * 0.045);
    ctx.strokeStyle = C.rim;
    ctx.stroke();
    ctx.lineWidth = Math.max(1, r * 0.014);
    ctx.strokeStyle = 'rgba(255,255,255,.10)';
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.985, Math.PI * 0.75, Math.PI * 1.6);
    ctx.stroke();
  }

  /** 刻度线：a0/a1 为角度（度，0 = 12 点方向，顺时针） */
  function tick(ctx, cx, cy, a0, rIn, rOut, w, color) {
    var a = rad(a0 - 90);
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * rIn, cy + Math.sin(a) * rIn);
    ctx.lineTo(cx + Math.cos(a) * rOut, cy + Math.sin(a) * rOut);
    ctx.lineWidth = w;
    ctx.strokeStyle = color;
    ctx.stroke();
  }

  function label(ctx, cx, cy, a0, rr, text, size, color, bold) {
    var a = rad(a0 - 90);
    ctx.save();
    ctx.font = (bold ? '600 ' : '') + size + 'px ' + MONO;
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
    ctx.restore();
  }

  /** 指针（从中心指向角度 a0） */
  function needle(ctx, cx, cy, a0, rTail, rTip, w, color, glow) {
    var a = rad(a0 - 90);
    ctx.save();
    if (glow) { ctx.shadowColor = color; ctx.shadowBlur = glow; }
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a + Math.PI / 2) * w, cy + Math.sin(a + Math.PI / 2) * w);
    ctx.lineTo(cx + Math.cos(a) * rTip, cy + Math.sin(a) * rTip);
    ctx.lineTo(cx + Math.cos(a - Math.PI / 2) * w, cy + Math.sin(a - Math.PI / 2) * w);
    ctx.lineTo(cx + Math.cos(a + Math.PI) * rTail, cy + Math.sin(a + Math.PI) * rTail);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.restore();
  }

  /** 数字读数窗（黑色底 + 描边框） */
  function readout(ctx, x, y, w, h, text, size, color, align) {
    ctx.save();
    ctx.fillStyle = '#05080d';
    ctx.strokeStyle = 'rgba(255,255,255,.13)';
    ctx.lineWidth = 1;
    var rx = align === 'right' ? x - w : (align === 'center' ? x - w / 2 : x);
    ctx.beginPath();
    var r = Math.min(4, h / 3);
    ctx.moveTo(rx + r, y);
    ctx.lineTo(rx + w - r, y);
    ctx.quadraticCurveTo(rx + w, y, rx + w, y + r);
    ctx.lineTo(rx + w, y + h - r);
    ctx.quadraticCurveTo(rx + w, y + h, rx + w - r, y + h);
    ctx.lineTo(rx + r, y + h);
    ctx.quadraticCurveTo(rx, y + h, rx, y + h - r);
    ctx.lineTo(rx, y + r);
    ctx.quadraticCurveTo(rx, y, rx + r, y);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.font = '600 ' + size + 'px ' + MONO;
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, rx + w / 2, y + h / 2 + 0.5);
    ctx.restore();
  }

  function smallText(ctx, x, y, text, size, color, align) {
    ctx.save();
    ctx.font = size + 'px ' + MONO;
    ctx.fillStyle = color;
    ctx.textAlign = align || 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y);
    ctx.restore();
  }

  /** 彩色弧段（绿区 / 黄区 / 红区） */
  function arc(ctx, cx, cy, rr, a0, a1, color, w) {
    ctx.beginPath();
    ctx.arc(cx, cy, rr, rad(a0 - 90), rad(a1 - 90));
    ctx.lineWidth = w;
    ctx.strokeStyle = color;
    ctx.stroke();
  }

  /* ------------------------- 1. 姿态指引仪 ADI ------------------------- */

  function drawADI(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 6;

    var roll = s.roll === null ? 0 : s.roll;
    var pitch = s.pitch === null ? 0 : s.pitch;

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.clip();

    // 天地球
    var pxPerDeg = r * 0.055;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(-roll));
    ctx.translate(0, clamp(pitch, -90, 90) * pxPerDeg);

    var horizonY = 0;
    var sky = ctx.createLinearGradient(0, -r * 3, 0, horizonY);
    sky.addColorStop(0, '#1b6cc4');
    sky.addColorStop(0.7, C.sky);
    sky.addColorStop(1, C.sky2);
    ctx.fillStyle = sky;
    ctx.fillRect(-r * 3, -r * 4, r * 6, r * 4);

    var gnd = ctx.createLinearGradient(0, horizonY, 0, r * 3);
    gnd.addColorStop(0, C.ground2);
    gnd.addColorStop(0.45, C.ground);
    gnd.addColorStop(1, '#5d3a17');
    ctx.fillStyle = gnd;
    ctx.fillRect(-r * 3, horizonY, r * 6, r * 4);

    // 地平线
    ctx.beginPath();
    ctx.moveTo(-r * 1.6, horizonY);
    ctx.lineTo(r * 1.6, horizonY);
    ctx.lineWidth = Math.max(1.6, r * 0.016);
    ctx.strokeStyle = '#ffffff';
    ctx.stroke();

    // 俯仰刻度：-30 … +30
    ctx.strokeStyle = 'rgba(255,255,255,.88)';
    ctx.fillStyle = 'rgba(255,255,255,.92)';
    ctx.font = '600 ' + Math.max(9, r * 0.115) + 'px ' + MONO;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (var p = -30; p <= 30; p += 5) {
      if (p === 0) continue;
      var y = horizonY - p * pxPerDeg;
      var isMajor = (p % 10 === 0);
      var half = isMajor ? r * 0.30 : r * 0.15;
      ctx.beginPath();
      ctx.lineWidth = isMajor ? Math.max(1.4, r * 0.014) : Math.max(1, r * 0.009);
      if (p < 0) { // 俯冲刻度画虚线（真实 ADI 惯例）
        ctx.setLineDash([r * 0.045, r * 0.035]);
      } else {
        ctx.setLineDash([]);
      }
      ctx.moveTo(-half, y);
      ctx.lineTo(half, y);
      ctx.stroke();
      ctx.setLineDash([]);
      if (isMajor) {
        ctx.fillText(String(Math.abs(p)), -half - r * 0.115, y);
        ctx.fillText(String(Math.abs(p)), half + r * 0.115, y);
      }
    }
    ctx.restore();

    // 滚转刻度弧（固定，不随姿态转）
    ctx.save();
    ctx.translate(cx, cy);
    var ra = r * 0.80;
    arc(ctx, 0, 0, ra, -60, 60, 'rgba(255,255,255,.28)', Math.max(1, r * 0.012));
    for (var a = -60; a <= 60; a += 10) {
      var len = (Math.abs(a) === 30 || a === 0) ? r * 0.075 : r * 0.045;
      var wgt = (a === 0) ? Math.max(1.8, r * 0.02) : Math.max(1.2, r * 0.012);
      tick(ctx, 0, 0, a, ra, ra + len, wgt, 'rgba(255,255,255,.85)');
    }
    ctx.restore();

    // 滚转指针（顶部三角，随机体滚转指向刻度）
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(-roll));
    ctx.beginPath();
    ctx.moveTo(0, -r * 0.985);
    ctx.lineTo(-r * 0.055, -r * 0.90);
    ctx.lineTo(r * 0.055, -r * 0.90);
    ctx.closePath();
    ctx.fillStyle = C.amber;
    ctx.shadowColor = 'rgba(255,192,67,.6)';
    ctx.shadowBlur = r * 0.1;
    ctx.fill();
    ctx.restore();

    // 固定飞机符号
    ctx.save();
    ctx.translate(cx, cy);
    var wSpan = r * 0.42;
    ctx.strokeStyle = C.amber;
    ctx.lineWidth = Math.max(2.4, r * 0.032);
    ctx.lineCap = 'round';
    ctx.shadowColor = 'rgba(0,0,0,.7)';
    ctx.shadowBlur = r * 0.05;
    ctx.beginPath();
    ctx.moveTo(-wSpan, 0);
    ctx.lineTo(-wSpan * 0.34, 0);
    ctx.moveTo(wSpan * 0.34, 0);
    ctx.lineTo(wSpan, 0);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-wSpan, 0);
    ctx.lineTo(-wSpan * 0.78, r * 0.055);
    ctx.moveTo(wSpan, 0);
    ctx.lineTo(wSpan * 0.78, r * 0.055);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(2, r * 0.022), 0, Math.PI * 2);
    ctx.fillStyle = C.amber;
    ctx.fill();
    ctx.restore();

    // 飞行指引指令杆（FG demo 增稳目标姿态；RL 模式无此信号）
    if (s.fdPitch !== null && s.fdRoll !== null) {
      ctx.save();
      ctx.translate(cx, cy);
      var fdx = clamp(-s.fdRoll, -25, 25) * (r * 0.020);
      var fdy = clamp(s.fdPitch, -20, 20) * (r * 0.020);
      ctx.strokeStyle = '#31ff9b';
      ctx.lineWidth = Math.max(2.6, r * 0.030);
      ctx.shadowColor = 'rgba(49,255,155,.55)';
      ctx.shadowBlur = r * 0.08;
      ctx.beginPath();
      ctx.moveTo(fdx - r * 0.40, fdy);
      ctx.lineTo(fdx - r * 0.10, fdy);
      ctx.moveTo(fdx + r * 0.10, fdy);
      ctx.lineTo(fdx + r * 0.40, fdy);
      ctx.stroke();
      ctx.restore();
    }

    // 侧滑指示器（底部小槽 + 小球；量程 ±12，超过则停在端点）
    var by = cy + r * 0.735;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.5)';
    ctx.lineWidth = Math.max(1, r * 0.012);
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.16, by);
    ctx.lineTo(cx + r * 0.16, by);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.16, by - r * 0.045);
    ctx.lineTo(cx - r * 0.16, by + r * 0.045);
    ctx.moveTo(cx + r * 0.16, by - r * 0.045);
    ctx.lineTo(cx + r * 0.16, by + r * 0.045);
    ctx.stroke();
    if (s.slip !== null) {
      var bx = cx + clamp(s.slip / 12, -1, 1) * r * 0.16;
      ctx.beginPath();
      ctx.arc(bx, by, r * 0.036, 0, Math.PI * 2);
      ctx.fillStyle = C.amber;
      ctx.fill();
    }
    ctx.restore();

    ctx.restore(); // 解除 clip

    // 外圈 + 玻璃反光
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.lineWidth = Math.max(2.5, r * 0.05);
    ctx.strokeStyle = '#39465c';
    ctx.stroke();
    var gl = ctx.createLinearGradient(cx - r, cy - r, cx + r * 0.3, cy + r * 0.5);
    gl.addColorStop(0, 'rgba(255,255,255,.14)');
    gl.addColorStop(0.45, 'rgba(255,255,255,.02)');
    gl.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = gl;
    ctx.fill();

    // 数字读数
    var fs = Math.max(10, r * 0.135);
    readout(ctx, cx - r * 0.92, cy - r * 1.02, r * 0.52, r * 0.20,
      fmt(s.roll, 1) + '°', fs, s.roll === null ? '#4a5568' : C.txt);
    readout(ctx, cx + r * 0.40, cy - r * 1.02, r * 0.52, r * 0.20,
      fmt(s.pitch, 1) + '°', fs, s.pitch === null ? '#4a5568' : C.txt);
    smallText(ctx, cx - r * 0.66, cy - r * 1.02 + r * 0.10, 'ROLL', fs * 0.62, C.txtDim, 'center');
    smallText(ctx, cx + r * 0.66, cy - r * 1.02 + r * 0.10, 'PITCH', fs * 0.62, C.txtDim, 'center');
  }

  /* ------------------------- 2. 水平状态指示器 HSI ------------------------- */

  function drawHSI(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cx = W / 2, cy = H / 2 + H * 0.02, r = Math.min(W, H * 0.92) / 2 - 8;

    dialFace(ctx, cx, cy, r);
    var hdg = s.heading === null ? 0 : ((s.heading % 360) + 360) % 360;

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.96, 0, Math.PI * 2);
    ctx.clip();

    // 罗盘卡
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(-hdg));
    var rr = r * 0.78;
    for (var a = 0; a < 360; a += 5) {
      var major = (a % 30 === 0);
      tick(ctx, 0, 0, a, rr, rr + (major ? r * 0.11 : r * 0.055),
        major ? Math.max(1.6, r * 0.018) : Math.max(1, r * 0.010),
        major ? C.tick : C.tickDim);
    }
    // 罗盘刻度与航向数字必须始终正向可读：在已旋转的罗盘卡坐标系中反向回正
    var cards = { 0: 'N', 30: '3', 60: '6', 90: 'E', 120: '12', 150: '15', 180: 'S', 210: '21', 240: '24', 270: 'W', 300: '30', 330: '33' };
    ctx.save();
    ctx.font = '600 ' + Math.max(10, r * 0.155) + 'px ' + MONO;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = C.txt;
    for (var k in cards) {
      var ca = parseFloat(k);
      ctx.save();
      ctx.rotate(rad(ca));
      ctx.translate(0, -rr + r * 0.10);
      // 反向旋转使文字在世界坐标系保持水平正立
      ctx.rotate(rad(hdg - ca));
      ctx.fillText(cards[k], 0, 0);
      ctx.restore();
    }
    ctx.restore();
    ctx.restore();

    // 航向游标（bug）：任务初始航向
    if (s.hdgBug !== null && s.hdgBug !== undefined) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(rad(s.hdgBug - hdg));
      ctx.beginPath();
      ctx.moveTo(0, -r * 0.93);
      ctx.lineTo(-r * 0.055, -r * 0.79);
      ctx.lineTo(r * 0.055, -r * 0.79);
      ctx.closePath();
      ctx.fillStyle = C.cyan;
      ctx.shadowColor = 'rgba(53,200,240,.7)';
      ctx.shadowBlur = r * 0.08;
      ctx.fill();
      ctx.restore();
    }

    // 顶部固定航向指针
    ctx.save();
    ctx.translate(cx, cy);
    ctx.beginPath();
    ctx.moveTo(0, -r * 0.955);
    ctx.lineTo(-r * 0.06, -r * 0.845);
    ctx.lineTo(r * 0.06, -r * 0.845);
    ctx.closePath();
    ctx.fillStyle = C.amber;
    ctx.fill();
    ctx.restore();

    // 中心机体符号
    ctx.save();
    ctx.translate(cx, cy);
    ctx.strokeStyle = C.white;
    ctx.lineWidth = Math.max(2, r * 0.026);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(0, -r * 0.16);
    ctx.lineTo(0, r * 0.16);
    ctx.moveTo(-r * 0.30, 0);
    ctx.lineTo(-r * 0.09, 0);
    ctx.moveTo(r * 0.09, 0);
    ctx.lineTo(r * 0.30, 0);
    ctx.moveTo(-r * 0.30, 0);
    ctx.lineTo(-r * 0.20, r * 0.09);
    ctx.moveTo(r * 0.30, 0);
    ctx.lineTo(r * 0.20, r * 0.09);
    ctx.stroke();
    ctx.restore();
    ctx.restore();

    // 读数窗口
    var fs = Math.max(10, r * 0.16);
    readout(ctx, cx, cy - r * 1.02, r * 0.62, r * 0.22,
      s.heading === null ? '–––' : String(Math.round(hdg)).padStart(3, '0'),
      fs, s.heading === null ? '#4a5568' : C.txt, 'center');
    smallText(ctx, cx, cy - r * 1.02 + r * 0.11 + fs * 0.72, 'HDG 航向', fs * 0.62, C.txtDim, 'center');

    var gs = s.gs_kt === null ? (s.ias_kt === null ? null : s.ias_kt) : s.gs_kt;
    readout(ctx, cx - r * 0.99, cy + r * 0.66, r * 0.62, r * 0.20,
      fmt(gs, 0), fs * 0.86, gs === null ? '#4a5568' : C.green, 'center');
    smallText(ctx, cx - r * 0.68, cy + r * 0.66 + r * 0.10 + fs * 0.6, 'GS kt 地速', fs * 0.56, C.txtDim, 'center');

    readout(ctx, cx + r * 0.37, cy + r * 0.66, r * 0.62, r * 0.20,
      fmt(s.yawRate, 1), fs * 0.86, s.yawRate === null ? '#4a5568' : C.green, 'center');
    smallText(ctx, cx + r * 0.68, cy + r * 0.66 + r * 0.10 + fs * 0.6, 'YAW °/s 偏航率', fs * 0.56, C.txtDim, 'center');
  }

  /* ------------------------- 3. 空速表 IAS ------------------------- */

  function drawIAS(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 8;
    dialFace(ctx, cx, cy, r);

    var MAXKT = 160, A0 = -150, A1 = 150;   // 表盘弧范围
    function ang(v) { return A0 + (clamp(v, 0, MAXKT) / MAXKT) * (A1 - A0); }

    // 色带：绿区 0-120，黄区 120-140，红区 140+（Bo105 VNE 量级，仅示意）
    arc(ctx, cx, cy, r * 0.86, ang(0), ang(120), 'rgba(53,224,138,.55)', r * 0.055);
    arc(ctx, cx, cy, r * 0.86, ang(120), ang(140), 'rgba(255,163,42,.6)', r * 0.055);
    arc(ctx, cx, cy, r * 0.86, ang(140), ang(MAXKT), 'rgba(255,77,77,.7)', r * 0.055);

    for (var v = 0; v <= MAXKT; v += 10) {
      var major = (v % 20 === 0);
      tick(ctx, cx, cy, ang(v), r * 0.74, r * (major ? 0.86 : 0.81),
        major ? Math.max(1.5, r * 0.018) : Math.max(1, r * 0.010), major ? C.tick : C.tickDim);
      if (major) label(ctx, cx, cy, ang(v), r * 0.60, String(v), Math.max(9, r * 0.135), C.txt, true);
    }

    var ias = s.ias_kt === null ? 0 : clamp(s.ias_kt, 0, MAXKT);
    needle(ctx, cx, cy, ang(ias), r * 0.12, r * 0.80, Math.max(2.2, r * 0.028), C.amber, r * 0.10);

    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.075, 0, Math.PI * 2);
    ctx.fillStyle = '#2a3444';
    ctx.fill();
    ctx.strokeStyle = '#4a5a72';
    ctx.lineWidth = 1;
    ctx.stroke();

    var fs = Math.max(10, r * 0.17);
    readout(ctx, cx, cy + r * 0.40, r * 0.70, r * 0.24, fmt(s.ias_kt, 1), fs,
      s.ias_kt === null ? '#4a5568' : C.txt, 'center');
    smallText(ctx, cx, cy + r * 0.40 + r * 0.12 + fs * 0.68, 'KT', fs * 0.6, C.txtDim, 'center');
    smallText(ctx, cx, cy - r * 0.52, fmt(s.ias_kmh, 1) + ' km/h', fs * 0.60,
      s.ias_kmh === null ? '#3d4759' : C.txtDim, 'center');
    smallText(ctx, cx, cy - r * 0.40, 'AIRSPEED 空速', fs * 0.58, C.txtDim, 'center');
  }

  /* ------------------------- 4. 高度表 ALT + 无线电高度 ------------------------- */

  function drawALT(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 8;
    dialFace(ctx, cx, cy, r);

    var MAXFT = 1000;                        // 单针 0–1000 ft
    function ang(v) { return (clamp(v, 0, MAXFT) / MAXFT) * 360; }

    for (var v = 0; v < MAXFT; v += 50) {
      var major = (v % 100 === 0);
      tick(ctx, cx, cy, ang(v), r * 0.74, r * (major ? 0.86 : 0.81),
        major ? Math.max(1.5, r * 0.018) : Math.max(1, r * 0.010), major ? C.tick : C.tickDim);
      if (major) label(ctx, cx, cy, ang(v), r * 0.60, String(v / 100), Math.max(9, r * 0.14), C.txt, true);
    }

    if (s.alt_ft !== null) {
      needle(ctx, cx, cy, ang(s.alt_ft % MAXFT), r * 0.12, r * 0.80, Math.max(2.2, r * 0.028), C.amber, r * 0.10);
    }
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.075, 0, Math.PI * 2);
    ctx.fillStyle = '#2a3444';
    ctx.fill();
    ctx.strokeStyle = '#4a5a72';
    ctx.lineWidth = 1;
    ctx.stroke();

    var fs = Math.max(10, r * 0.16);
    readout(ctx, cx, cy + r * 0.30, r * 0.78, r * 0.22, fmt(s.alt_ft, 0), fs,
      s.alt_ft === null ? '#4a5568' : C.txt, 'center');
    smallText(ctx, cx, cy + r * 0.30 + r * 0.11 + fs * 0.7, 'FT 气压高度', fs * 0.56, C.txtDim, 'center');

    // 无线电高度窗口（真实高度，直升机作业更关心 AGL；负值截为 0）
    var ra = s.agl_m === null ? null : Math.max(0, s.agl_m);
    var raBoxW = r * 0.86, raBoxH = r * 0.26;
    var bx = cx - raBoxW / 2, by = cy - r * 0.14;
    ctx.save();
    ctx.fillStyle = '#05080d';
    ctx.strokeStyle = (ra !== null && ra < 5) ? 'rgba(255,163,42,.8)' : 'rgba(255,255,255,.16)';
    ctx.lineWidth = 1;
    ctx.fillRect(bx, by, raBoxW, raBoxH);
    ctx.strokeRect(bx, by, raBoxW, raBoxH);
    ctx.restore();
    smallText(ctx, bx + 6, by + raBoxH * 0.28, 'RAD ALT', fs * 0.52, C.txtDim, 'left');
    smallText(ctx, bx + raBoxW - 6, by + raBoxH * 0.28, 'm 无线电', fs * 0.5, C.txtDim, 'right');
    smallText(ctx, cx, by + raBoxH * 0.70, fmt(ra, 1), fs * 0.95,
      ra === null ? '#4a5568' : (ra < 5 ? C.warn : C.green), 'center');
    smallText(ctx, cx, cy - r * 0.30, 'ALTITUDE 高度', fs * 0.58, C.txtDim, 'center');
  }

  /* ------------------------- 5. 升降速度表 VSI ------------------------- */

  function drawVSI(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 8;
    dialFace(ctx, cx, cy, r);

    var MAXF = 2000;
    function ang(v) {
      var t = clamp(v / MAXF, -1, 1);
      return -150 + (t + 1) / 2 * 300;
    }
    for (var v = -MAXF; v <= MAXF; v += 250) {
      var major = (Math.abs(v) % 500 === 0);
      tick(ctx, cx, cy, ang(v), r * 0.74, r * (major ? 0.86 : 0.81),
        major ? Math.max(1.5, r * 0.018) : Math.max(1, r * 0.010), major ? C.tick : C.tickDim);
      if (major && v !== 0) label(ctx, cx, cy, ang(v), r * 0.60, String(Math.abs(v) / 100), Math.max(9, r * 0.13), C.txt, true);
    }
    tick(ctx, cx, cy, ang(0), r * 0.72, r * 0.90, Math.max(2, r * 0.024), C.tick);

    if (s.vs_fpm !== null) {
      needle(ctx, cx, cy, ang(s.vs_fpm), r * 0.12, r * 0.80, Math.max(2.2, r * 0.028), C.amber, r * 0.10);
    }
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.075, 0, Math.PI * 2);
    ctx.fillStyle = '#2a3444';
    ctx.fill();
    ctx.strokeStyle = '#4a5a72';
    ctx.lineWidth = 1;
    ctx.stroke();

    var fs = Math.max(10, r * 0.16);
    readout(ctx, cx, cy + r * 0.34, r * 0.80, r * 0.22,
      (s.vs_ms === null ? '–' : (s.vs_ms > 0 ? '+' : '') + s.vs_ms.toFixed(2)), fs,
      s.vs_ms === null ? '#4a5568' : (Math.abs(s.vs_ms) < 0.5 ? C.green : C.txt), 'center');
    smallText(ctx, cx, cy + r * 0.34 + r * 0.11 + fs * 0.7, 'm/s 升降率', fs * 0.56, C.txtDim, 'center');
    smallText(ctx, cx, cy - r * 0.34, fmt(s.vs_fpm, 0) + ' ft/min', fs * 0.58,
      s.vs_fpm === null ? '#3d4759' : C.txtDim, 'center');
    smallText(ctx, cx, cy - r * 0.46, 'VERT SPEED 垂速', fs * 0.56, C.txtDim, 'center');
  }

  /* ------------------------- 6. 旋翼转速表 ROTOR RPM ------------------------- */

  function drawRPM(canvas, s, opts) {
    opts = opts || {};
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 8;
    dialFace(ctx, cx, cy, r);

    var A0 = -140, A1 = 140, MAXP = 120;
    function ang(p) { return A0 + (clamp(p, 0, MAXP) / MAXP) * (A1 - A0); }

    arc(ctx, cx, cy, r * 0.86, ang(0), ang(90), 'rgba(255,77,77,.55)', r * 0.055);
    arc(ctx, cx, cy, r * 0.86, ang(95), ang(105), 'rgba(53,224,138,.6)', r * 0.055);
    arc(ctx, cx, cy, r * 0.86, ang(105), ang(MAXP), 'rgba(255,163,42,.6)', r * 0.055);

    for (var p = 0; p <= MAXP; p += 10) {
      tick(ctx, cx, cy, ang(p), r * 0.74, r * (p % 20 === 0 ? 0.86 : 0.81),
        p % 20 === 0 ? Math.max(1.5, r * 0.018) : Math.max(1, r * 0.010), p % 20 === 0 ? C.tick : C.tickDim);
      if (p % 20 === 0) label(ctx, cx, cy, ang(p), r * 0.60, String(p), Math.max(9, r * 0.13), C.txt, true);
    }

    var hasRpm = s.mainRpmPct !== null && s.mainRpmPct !== undefined;
    if (hasRpm) {
      var col = s.mainRpmPct < 90 ? C.red : (s.mainRpmPct > 108 ? C.warn : C.green);
      needle(ctx, cx, cy, ang(s.mainRpmPct), r * 0.12, r * 0.80, Math.max(2.2, r * 0.028), col, r * 0.12);
    }
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.075, 0, Math.PI * 2);
    ctx.fillStyle = '#2a3444';
    ctx.fill();
    ctx.strokeStyle = '#4a5a72';
    ctx.lineWidth = 1;
    ctx.stroke();

    var fs = Math.max(10, r * 0.16);
    if (hasRpm) {
      readout(ctx, cx, cy + r * 0.26, r * 0.86, r * 0.22, fmt(s.mainRpm, 0), fs, C.green, 'center');
      smallText(ctx, cx, cy + r * 0.26 + r * 0.11 + fs * 0.7, 'RPM 主旋翼', fs * 0.56, C.txtDim, 'center');
      readout(ctx, cx, cy - r * 0.30, r * 0.56, r * 0.20, fmt(s.mainRpmPct, 1) + '%', fs * 0.85, C.txt, 'center');
    } else {
      // 无旋翼转速数据源时明确显示 N/A，不默认指向 0
      smallText(ctx, cx, cy + r * 0.12, 'NO DATA', fs * 1.35, '#3d4759', 'center');
      smallText(ctx, cx, cy + r * 0.12 + fs * 1.1, '主旋翼转速未记录', fs * 0.72, '#3d4759', 'center');
    }
    smallText(ctx, cx, cy - r * 0.52, (opts.title || 'ROTOR RPM') + ' 旋翼转速', fs * 0.56, C.txtDim, 'center');
    if (s.rpmNote) smallText(ctx, cx, cy - r * 0.52 + fs * 0.60, s.rpmNote, fs * 0.48, '#5c6b83', 'center');
  }

  /* ------------------------- 7. 扭矩 TORQUE ------------------------- */

  function drawTorque(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var fs = clamp(Math.min(H * 0.17, W * 0.042), 7.5, 12);
    var padX = Math.max(8, W * 0.05);
    var headY = fs * 1.25;
    var trackY = headY + fs * 0.9;
    var trackH = clamp(H * 0.30, 8, 26);
    var trackW = W - padX * 2;

    smallText(ctx, padX, headY * 0.72, 'TORQUE 旋翼扭矩', fs, C.txtDim, 'left');
    if (s.torqueNote) {
      smallText(ctx, W - padX, headY * 0.72, s.torqueNote, fs * 0.86, C.txtDim, 'right');
    }

    // 轨道
    ctx.fillStyle = '#080d14';
    ctx.strokeStyle = '#1b2434';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(padX, trackY, trackW, trackH);
    ctx.fill();
    ctx.stroke();

    // 色区：0-90 绿 / 90-100 黄 / >100 红
    function seg(a, b, col) {
      ctx.fillStyle = col;
      ctx.fillRect(padX + trackW * (a / 120), trackY + 1, trackW * ((b - a) / 120), trackH - 2);
    }
    seg(0, 90, 'rgba(53,224,138,.20)');
    seg(90, 100, 'rgba(255,163,42,.24)');
    seg(100, 120, 'rgba(255,77,77,.28)');

    for (var v = 0; v <= 120; v += 20) {
      var x = padX + trackW * (v / 120);
      ctx.beginPath();
      ctx.moveTo(x, trackY + trackH);
      ctx.lineTo(x, trackY + trackH + Math.max(2, fs * 0.35));
      ctx.strokeStyle = C.tickDim;
      ctx.lineWidth = 1;
      ctx.stroke();
      smallText(ctx, x, trackY + trackH + fs * 0.95, String(v), fs * 0.82, C.txtDim, 'center');
    }

    if (s.torquePct !== null) {
      var w = trackW * (clamp(s.torquePct, 0, 120) / 120);
      var g = ctx.createLinearGradient(padX, 0, padX + w, 0);
      g.addColorStop(0, '#2f7a4e');
      g.addColorStop(1, s.torquePct > 100 ? C.red : (s.torquePct > 90 ? C.warn : C.green));
      ctx.fillStyle = g;
      ctx.fillRect(padX, trackY + 1, w, trackH - 2);
      ctx.fillStyle = '#fff2d0';
      ctx.fillRect(padX + w - 1.5, trackY - 2, 3, trackH + 4);
    }

    var footY = trackY + trackH + fs * 1.9;
    if (s.torquePct !== null) {
      var boxW = Math.min(W * 0.42, 110), boxH = clamp(H * 0.26, 14, 26);
      readout(ctx, W - padX - boxW, Math.min(H - boxH - 2, footY), boxW, boxH,
        fmt(s.torquePct, 1) + ' %', clamp(fs * 1.35, 10, 16),
        s.torquePct > 100 ? C.red : C.green, 'center');
    } else {
      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#3d4759';
      ctx.font = '600 ' + clamp(fs * 1.5, 11, 17) + 'px ' + MONO;
      ctx.fillText('N/A', W / 2, trackY + trackH / 2);
      ctx.font = clamp(fs * 0.95, 8, 12) + 'px ' + MONO;
      ctx.fillText('未记录扭矩', W / 2, trackY + trackH / 2 + fs * 1.25);
      ctx.restore();
    }
    smallText(ctx, padX, H - fs * 0.7,
      s.torqueRaw === null ? '' : ('原始 ' + fmt(s.torqueRaw, 0) + ' ft·lb'), fs * 0.8, C.txtDim, 'left');
  }

  /* ------------------------- 8. 发动机 N1 / N2 / TOT ------------------------- */

  function drawEngines(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var engines = s.engines || [];
    var n = Math.max(1, engines.length);
    var colW = W / n;
    var fs = clamp(Math.min(H * 0.14, W * 0.05), 7.5, 12);
    var padX = Math.max(6, W * 0.05);
    var headY = fs * 1.3;
    smallText(ctx, padX, headY * 0.72, 'ENGINE 发动机', fs, C.txtDim, 'left');

    for (var i = 0; i < n; i++) {
      var e = engines[i] || {};
      var x0 = i * colW;
      var nameY = headY + fs * 0.85;
      smallText(ctx, x0 + colW / 2, nameY, 'ENG ' + (i + 1), fs,
        e.run === false ? C.red : C.txt, 'center');

      var rows = [
        { k: 'N1', v: e.n1, unit: '%', max: 110, warn: 101, color: C.cyan },
        { k: 'N2', v: e.n2, unit: '%', max: 110, warn: 101, color: C.cyan },
        { k: 'TOT', v: e.tot, unit: '°C', max: 1000, warn: 810, color: C.warn }
      ];
      var footRes = fs * 1.9;
      var avail = H - (nameY + fs * 0.9) - footRes;
      var barH = clamp(avail / 3 - fs * 0.55, 7, 24);
      var gap = fs * 0.55;
      var y = nameY + fs * 0.95;
      var labelW = Math.max(16, colW * 0.20);
      var valW = Math.max(28, colW * 0.30);

      for (var j = 0; j < rows.length; j++) {
        var rr = rows[j];
        var bx = x0 + padX * 0.5;
        var bw = Math.max(10, colW - padX - labelW - valW);
        smallText(ctx, bx, y + barH / 2, rr.k, fs * 0.92, C.txtDim, 'left');
        ctx.fillStyle = '#080d14';
        ctx.strokeStyle = '#1b2434';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.rect(bx + labelW, y, bw, barH);
        ctx.fill();
        ctx.stroke();
        if (rr.v !== null && rr.v !== undefined) {
          var frac = clamp(rr.v / rr.max, 0, 1);
          var g = ctx.createLinearGradient(bx + labelW, 0, bx + labelW + bw * frac, 0);
          g.addColorStop(0, 'rgba(255,255,255,.10)');
          g.addColorStop(1, rr.v > rr.warn ? C.red : rr.color);
          ctx.fillStyle = g;
          ctx.fillRect(bx + labelW + 1, y + 1, Math.max(1, bw * frac - 1), barH - 2);
        }
        var txt = (rr.v === null || rr.v === undefined) ? 'N/A' : Number(rr.v).toFixed(rr.k === 'TOT' ? 0 : 1);
        smallText(ctx, x0 + colW - padX * 0.5, y + barH / 2 - fs * 0.28, txt, fs * 1.1,
          (rr.v === null || rr.v === undefined) ? '#4a5568' : C.txt, 'right');
        smallText(ctx, x0 + colW - padX * 0.5, y + barH / 2 + fs * 0.72, rr.unit, fs * 0.8, C.txtDim, 'right');
        y += barH + gap;
      }
      if (e.rpm !== null && e.rpm !== undefined) {
        smallText(ctx, x0 + colW / 2, H - fs * 0.55, 'N2 ' + fmt(e.rpm, 0) + ' rpm', fs * 0.85, C.txtDim, 'center');
      }
    }
  }

  /* ------------------------- 9. 燃油 / 重量 / G ------------------------- */

  function drawFuel(canvas, s) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var fs = clamp(Math.min(H * 0.16, W * 0.05), 7.5, 12);
    var bx = Math.max(6, W * 0.05);
    var bw = W - bx * 2;
    var headY = fs * 1.3;
    smallText(ctx, bx, headY * 0.72, 'FUEL / MASS 燃油与重量', fs, C.txtDim, 'left');

    var by = headY + fs * 0.75;
    var footRes = fs * 3.4;
    var bh = Math.max(10, H - by - footRes);

    ctx.fillStyle = '#080d14';
    ctx.strokeStyle = '#1b2434';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(bx, by, bw, bh);
    ctx.fill();
    ctx.stroke();

    var capGal = 470;   // Bo105 油箱约 470 US gal（含副油箱）；仅作刻度参考
    if (s.fuelGal !== null) {
      var frac = clamp(s.fuelGal / capGal, 0, 1);
      var g = ctx.createLinearGradient(bx, 0, bx + bw, 0);
      g.addColorStop(0, '#2c6b4b');
      g.addColorStop(1, frac < 0.15 ? C.red : C.green);
      ctx.fillStyle = g;
      ctx.fillRect(bx + 1, by + 1, Math.max(1, (bw - 2) * frac), bh - 2);
    }
    smallText(ctx, bx + 6, by + bh / 2, fmt(s.fuelGal, 0) + ' gal', fs * 1.05,
      s.fuelGal === null ? '#4a5568' : '#eafff4', 'left');
    smallText(ctx, bx + bw - 6, by + bh / 2,
      (s.fuelGal === null ? 'N/A' : fmt(s.fuelGal * 3.7854, 0) + ' L'), fs * 0.95, '#cdeadb', 'right');

    var r1 = H - fs * 1.85, r2 = H - fs * 0.6;
    smallText(ctx, bx, r1, 'GROSS 总重 ' + fmt(s.grossKg, 0) + ' kg', fs * 0.92, C.txtDim, 'left');
    smallText(ctx, bx + bw, r1, 'G 载荷 ' + fmt(s.gLoad, 2), fs * 0.92,
      s.gLoad === null ? '#4a5568' : (Math.abs(s.gLoad - 1) > 0.6 ? C.warn : C.txtDim), 'right');
    smallText(ctx, bx, r2, 'WOW 接地 ' + (s.wow === null ? '–' : (s.wow ? '是' : '否')), fs * 0.92,
      s.wow ? C.green : C.txtDim, 'left');
    smallText(ctx, bx + bw, r2, '风 ' + fmt(s.windDir, 0) + '°/' + fmt(s.windKt, 1) + 'kt', fs * 0.92, C.txtDim, 'right');
  }

  /* ------------------------- 10. 航迹俯视图 ------------------------- */

  function drawTrack(canvas, s, track) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var fs = clamp(Math.min(H * 0.11, W * 0.035), 7, 11);
    var pad = Math.max(3, Math.min(W, H) * 0.05);
    var top = pad, bot = H - pad;

    ctx.fillStyle = '#04070c';
    ctx.strokeStyle = '#141c28';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(pad, top, W - pad * 2, bot - top);
    ctx.fill();
    ctx.stroke();

    // 网格
    ctx.strokeStyle = 'rgba(53,200,240,.07)';
    for (var gx = pad; gx < W - pad; gx += (W - pad * 2) / 6) {
      ctx.beginPath(); ctx.moveTo(gx, top); ctx.lineTo(gx, bot); ctx.stroke();
    }
    for (var gy = top; gy < bot; gy += (bot - top) / 4) {
      ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(W - pad, gy); ctx.stroke();
    }
    if (s.trackNote) smallText(ctx, W - pad - 2, top + fs * 0.75, s.trackNote, fs * 0.85, C.txtDim, 'right');

    if (!track || track.length < 2) {
      smallText(ctx, W / 2, H / 2, s.trackEmpty || '无航迹数据', fs * 1.15, '#3d4759', 'center');
      return;
    }

    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, i;
    for (i = 0; i < track.length; i++) {
      if (track[i].x < minX) minX = track[i].x;
      if (track[i].x > maxX) maxX = track[i].x;
      if (track[i].y < minY) minY = track[i].y;
      if (track[i].y > maxY) maxY = track[i].y;
    }
    var spanX = Math.max(maxX - minX, 20), spanY = Math.max(maxY - minY, 20);
    var span = Math.max(spanX, spanY) * 1.12;
    var cX = (minX + maxX) / 2, cY = (minY + maxY) / 2;
    var scale = Math.min(W - pad * 2, bot - top) / span;
    function px(x) { return W / 2 + (x - cX) * scale; }
    function py(y) { return (top + (bot - top) / 2) - (y - cY) * scale; }

    // 轨迹线（越接近当前时刻越亮）
    ctx.lineWidth = 1.6;
    for (i = 1; i < track.length; i++) {
      var a = track[i - 1], b = track[i];
      ctx.beginPath();
      ctx.moveTo(px(a.x), py(a.y));
      ctx.lineTo(px(b.x), py(b.y));
      ctx.strokeStyle = 'rgba(53,200,240,' + (0.18 + 0.55 * (i / track.length)) + ')';
      ctx.stroke();
    }

    // 起点 / 终点
    var p0 = track[0], pN = track[track.length - 1];
    var msz = clamp(Math.min(W, H) * 0.035, 2, 4);
    ctx.beginPath(); ctx.arc(px(p0.x), py(p0.y), msz, 0, Math.PI * 2);
    ctx.fillStyle = '#6ee7ff'; ctx.fill();
    smallText(ctx, px(p0.x) + msz * 2, py(p0.y) - msz * 2, '起点', fs * 0.9, '#6ee7ff', 'left');

    ctx.save();
    ctx.translate(px(pN.x), py(pN.y));
    ctx.rotate(rad(s.heading === null ? 0 : s.heading));
    ctx.beginPath();
    ctx.moveTo(0, -msz * 2.6); ctx.lineTo(-msz * 1.6, msz * 1.8);
    ctx.lineTo(0, msz * 0.9); ctx.lineTo(msz * 1.6, msz * 1.8);
    ctx.closePath();
    ctx.fillStyle = C.amber;
    ctx.shadowColor = 'rgba(255,192,67,.8)';
    ctx.shadowBlur = 8;
    ctx.fill();
    ctx.restore();

    // 比例尺
    var barM = Math.pow(10, Math.floor(Math.log10(span / 2)));
    var barPx = barM * scale;
    var sbY = bot - fs * 1.5;
    ctx.strokeStyle = 'rgba(255,255,255,.35)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad + 6, sbY); ctx.lineTo(pad + 6 + barPx, sbY);
    ctx.moveTo(pad + 6, sbY - fs * 0.45); ctx.lineTo(pad + 6, sbY + fs * 0.45);
    ctx.moveTo(pad + 6 + barPx, sbY - fs * 0.45); ctx.lineTo(pad + 6 + barPx, sbY + fs * 0.45);
    ctx.stroke();
    smallText(ctx, pad + 10 + barPx, sbY, (barM >= 1000 ? (barM / 1000) + ' km' : barM + ' m'), fs * 0.9, C.txtDim, 'left');

    smallText(ctx, W - pad - 2, top + fs * 0.75,
      (spanX >= 1000 ? (spanX / 1000).toFixed(2) + ' km' : spanX.toFixed(0) + ' m'),
      fs * 0.9, C.txtDim, 'right');
  }

  /* ------------------------- 11. 高度剖面 ------------------------- */

  function drawProfile(canvas, track, cur) {
    var f = fit(canvas), ctx = f.ctx, W = f.W, H = f.H;
    var fs = clamp(Math.min(H * 0.14, W * 0.035), 7, 11);
    var pad = Math.max(3, Math.min(W, H) * 0.05);
    if (!track || track.length < 2) {
      smallText(ctx, W / 2, H / 2, '无数据', fs * 1.15, '#3d4759', 'center');
      return;
    }
    var minT = track[0].t, maxT = track[track.length - 1].t;
    var maxA = 0, i;
    for (i = 0; i < track.length; i++) maxA = Math.max(maxA, track[i].alt || 0);
    maxA = Math.max(maxA * 1.15, 10);
    var x0 = pad, y0 = pad, w = W - pad * 2, h = H - pad * 2;

    ctx.fillStyle = '#04070c';
    ctx.strokeStyle = '#141c28';
    ctx.beginPath(); ctx.rect(x0, y0, w, h); ctx.fill(); ctx.stroke();

    ctx.strokeStyle = 'rgba(53,200,240,.07)';
    for (var gy = y0; gy <= y0 + h; gy += h / 4) {
      ctx.beginPath(); ctx.moveTo(x0, gy); ctx.lineTo(x0 + w, gy); ctx.stroke();
    }

    ctx.beginPath();
    ctx.moveTo(x0, y0 + h);
    for (i = 0; i < track.length; i++) {
      var px = x0 + (track[i].t - minT) / Math.max(maxT - minT, 0.001) * w;
      var py2 = y0 + h - (track[i].alt || 0) / maxA * h;
      ctx.lineTo(px, py2);
    }
    ctx.lineTo(x0 + w, y0 + h);
    ctx.closePath();
    var g = ctx.createLinearGradient(0, y0, 0, y0 + h);
    g.addColorStop(0, 'rgba(53,200,240,.42)');
    g.addColorStop(1, 'rgba(53,200,240,.03)');
    ctx.fillStyle = g;
    ctx.fill();

    ctx.beginPath();
    for (i = 0; i < track.length; i++) {
      var px2 = x0 + (track[i].t - minT) / Math.max(maxT - minT, 0.001) * w;
      var py3 = y0 + h - (track[i].alt || 0) / maxA * h;
      if (i === 0) ctx.moveTo(px2, py3); else ctx.lineTo(px2, py3);
    }
    ctx.strokeStyle = '#6ee7ff';
    ctx.lineWidth = 1.6;
    ctx.stroke();

    if (cur && cur.t !== null) {
      var cx2 = x0 + clamp((cur.t - minT) / Math.max(maxT - minT, 0.001), 0, 1) * w;
      ctx.strokeStyle = C.amber;
      ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(cx2, y0); ctx.lineTo(cx2, y0 + h); ctx.stroke();
    }
    smallText(ctx, x0 + w - 2, y0 + fs * 0.8, '峰 ' + maxA.toFixed(0) + ' m', fs * 0.88, C.txtDim, 'right');
    smallText(ctx, x0 + 3, y0 + h - fs * 0.6, '0 m', fs * 0.85, C.txtDim, 'left');
    smallText(ctx, x0 + w - 3, y0 + h - fs * 0.6, maxT.toFixed(0) + ' s', fs * 0.85, C.txtDim, 'right');
  }

  global.CockpitGauges = {
    C: C, MONO: MONO, num: num, fmt: fmt, clamp: clamp, fit: fit,
    drawADI: drawADI,
    drawHSI: drawHSI,
    drawIAS: drawIAS,
    drawALT: drawALT,
    drawVSI: drawVSI,
    drawRPM: drawRPM,
    drawTorque: drawTorque,
    drawEngines: drawEngines,
    drawFuel: drawFuel,
    drawTrack: drawTrack,
    drawProfile: drawProfile
  };
})(window);
