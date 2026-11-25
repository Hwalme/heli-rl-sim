/* ==========================================================================
   驾驶舱主逻辑 —— 双数据源（FG 实时 / RL 回放）
   原则：
     - FG 实时：只读 FlightGear 属性服务器（经本地桥接服务），不写入任何属性。
     - RL 回放：逐帧播放 rl_train/results 中的轨迹；轨迹未记录的量一律 N/A，不编造。
   ========================================================================== */
(function () {
  'use strict';

  var G = window.CockpitGauges;
  var KC = window.CockpitControls;
  var $ = function (id) { return document.getElementById(id); };
  var CTL = null;              // 操纵机构实例
  var WRITE_OK = false;        // 服务端是否允许写入 FG（需 --allow-write 启动）
  var lastPush = 0;

  var API = {
    fgState: '/api/fg/state',
    fgStream: '/api/fg/stream',
    fgView: '/api/fg/view',
    fgLaunch: '/api/fg/launch',
    fgLaunchState: '/api/fg/launch/state',
    fgWrite: '/api/fg/write',
    rlManifest: '/api/rl/manifest',
    rlEpisode: '/api/rl/episode'
  };

  // 是否由「静态预览」打开（如 IDE 内置预览面板）：只代理前端文件，没有桥接后端，
  // /api/* 必然 404。此时必须给出真实地址，而不是笼统地报"未连接"。
  var BRIDGE_PORT = '8777';
  var STATIC_PREVIEW = !!(location.port && location.port !== BRIDGE_PORT);
  var COCKPIT_URL = location.protocol + '//' + (location.hostname || '127.0.0.1') + ':' + BRIDGE_PORT + '/cockpit.html';

  /* ---------------- 全局状态 ---------------- */
  var S = {
    mode: 'rl',                 // 'fg' | 'rl'  —— 默认 RL 回放：交接包 §3.7 演示底线；FG LIVE 当前不可靠（5501 为 B 线实验道、bo105 无人必坠）
    fg: null,                   // 最近一帧 FG 状态
    fgError: null,
    fgConnected: false,
    fgTrack: [],                // {x,y,alt,t} 米制相对坐标
    fgOrigin: null,
    rl: { frames: null, meta: null, idx: 0, playing: false, rate: 1, track: null, loaded: false },
    lastDraw: 0,
    hdgBug: null,
    fgViewActive: false,
    fgViewError: null,
    launchPending: false,      // 一键启动中
    launchMsg: '',
    fgPort: 5502               // 由 /api/health 回填实际端口
  };

  var RATES = [0.25, 0.5, 1, 2, 4, 8];

  /* ---------------- 工具 ---------------- */
  function fmt(v, d) { return G.fmt(v, d); }
  function nz(v, d) { return (v === null || v === undefined || !isFinite(v)) ? '–' : Number(v).toFixed(d === undefined ? 1 : d); }

  function geoToXY(lat, lon, o) {
    // 等距投影：x 东向（m），y 北向（m）
    return {
      x: (lon - o.lon) * 111320 * Math.cos(o.lat * Math.PI / 180),
      y: (lat - o.lat) * 110540
    };
  }

  /* ======================================================================
     一、FG 实时源
     ====================================================================== */

  function startFGStream() {
    if (typeof EventSource === 'undefined') { pollFG(); return; }
    var es = new EventSource(API.fgStream);
    var failed = 0, gotMsg = false;

    es.onmessage = function (ev) {
      try {
        var st = JSON.parse(ev.data);
        S.fg = st.data || null;
        S.fgConnected = !!st.connected;
        S.fgError = st.error || null;
        gotMsg = true;
        failed = 0;
        if (st.connected) onFGSample();
      } catch (e) { /* 忽略脏帧 */ }
    };
    es.onerror = function () {
      S.fgConnected = false;
      S.fgError = '桥接服务未运行或连接中断。启动方式：项目根目录运行 python cockpit/bridge_server.py';
      failed += 1;
      // SSE 连续失败（服务未启动 / 503 限流）→ 关闭长连接，降级为轮询
      if (failed >= 3) { es.close(); pollFG(); }
    };
    // 长时间未收到任何帧同样降级为轮询
    setTimeout(function () {
      if (!gotMsg) { es.close(); pollFG(); }
    }, 8000);
  }

  var polling = false;
  function pollFG() {
    if (polling) return;
    polling = true;
    (function loop() {
      if (S.mode === 'fg' || true) {
        fetch(API.fgState).then(function (r) { return r.json(); }).then(function (st) {
          S.fg = st.data || null;
          S.fgConnected = !!st.connected;
          S.fgError = st.error || null;
          if (st.connected) onFGSample();
        }).catch(function () {
          S.fgConnected = false;
          S.fgError = '桥接服务未运行。启动方式：项目根目录运行 python cockpit/bridge_server.py';
        }).then(function () {
          setTimeout(loop, 1000);
        });
      }
    })();
  }

  /* ---------------- 一键启动 FlightGear ---------------- */
  // 用户在顶栏点「FG 未连接」时调用：让桥接服务直接拉起专用 FG 实例 + 抓屏服务，
  // 然后切到 FG 实时模式，由现有轮询逻辑自动翻成已连接。
  function launchFG() {
    if (S.fgConnected) return;
    S.launchPending = true;
    S.launchMsg = '正在向桥接服务请求启动 FlightGear…';
    if (S.mode !== 'fg') switchMode('fg');
    if (typeof updateTop === 'function') updateTop(S.fg);
    if (typeof updateBanner === 'function') updateBanner();
    fetch(API.fgLaunch, { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d || d.status === 'error') {
        S.launchPending = false;
        S.launchMsg = (d && d.msg) ? d.msg : '启动请求失败';
        updateBanner();
        return;
      }
      S.launchMsg = (d && d.msg) ? d.msg : 'FlightGear 启动中…';
      monitorLaunch();
    }).catch(function () {
      S.launchPending = false;
      S.launchMsg = '启动请求失败：桥接服务未响应（请确认 python cockpit/bridge_server.py 已运行）';
      updateBanner();
    });
  }

  // 轮询启动进度；一旦 FG 属性服务器连通即结束。超时则给出排查提示。
  function monitorLaunch() {
    if (S.fgConnected) { S.launchPending = false; S.launchMsg = ''; updateBanner(); return; }
    fetch(API.fgLaunchState).then(function (r) { return r.json(); }).then(function (d) {
      if (d) {
        if (d.fg_connected) { S.launchPending = false; S.launchMsg = ''; updateBanner(); return; }
        if (d.status === 'error') {
          S.launchPending = false; S.launchMsg = d.msg || '启动失败'; updateBanner(); return;
        }
        S.launchMsg = (d.msg || 'FlightGear 启动中…') +
          (d.port ? '（telnet 127.0.0.1:' + d.port + '）' : '');
        // 超过 70 秒仍未连通 → 提示排查（多为 FG 安装/窗口/显卡问题）
        if (typeof d.ts === 'number' && (Date.now() - d.ts * 1000) > 70000) {
          S.launchPending = false;
          S.launchMsg = '启动超时：请确认 FlightGear 2024.1 已安装且未被最小化到任务栏，' +
            '必要时手动运行「启动FlightGear_驾驶舱专用.bat」。';
          updateBanner();
          return;
        }
      }
      updateBanner();
      if (!S.fgConnected) setTimeout(monitorLaunch, 2000);
    }).catch(function () {
      if (!S.fgConnected) setTimeout(monitorLaunch, 2000);
    });
  }

  var fgViewTimer = null;
  function startFGView() {
    if (fgViewTimer) return;
    var img = $('fgView');
    var overlay = $('fgOverlay');
    function tick() {
      if (S.mode !== 'fg') { fgViewTimer = setTimeout(tick, 500); return; }
      var url = API.fgView + '?t=' + Date.now();
      img.onload = function () {
        img.classList.add('live');
        overlay.classList.add('hidden');
        S.fgViewActive = true;
        S.fgViewError = null;
      };
      img.onerror = function () {
        img.classList.remove('live');
        overlay.classList.remove('hidden');
        overlay.textContent = S.fgViewError || '首次加载约 3–5 秒；若持续无画面，请检查驾驶舱专用 FG 实例是否启动且窗口可见';
        S.fgViewActive = false;
      };
      img.src = url;
      fgViewTimer = setTimeout(tick, 200);
    }
    tick();
  }

  function onFGSample() {
    if (!S.fg) return;
    if (S.hdgBug === null && S.fg.heading_deg !== null) S.hdgBug = S.fg.heading_deg;
    var lat = S.fg.lat_deg, lon = S.fg.lon_deg;
    if (lat !== null && lon !== null) {
      if (!S.fgOrigin) S.fgOrigin = { lat: lat, lon: lon };
      var p = geoToXY(lat, lon, S.fgOrigin);
      var t = S.fg.elapsed_s || 0;
      var last = S.fgTrack[S.fgTrack.length - 1];
      if (!last || t - last.t > 0.25 || Math.hypot(p.x - last.x, p.y - last.y) > 0.5) {
        S.fgTrack.push({ x: p.x, y: p.y, alt: S.fg.agl_m || 0, t: t });
        if (S.fgTrack.length > 4000) S.fgTrack.shift();
      }
    }
  }

  function fgState() {
    var d = S.fg || {};
    var engines = [];
    if (d.eng0_n1_pct !== null || d.eng0_n2_pct !== null || d.eng0_run !== null) {
      engines.push({ n1: d.eng0_n1_pct, n2: d.eng0_n2_pct, tot: d.eng0_tot_c, rpm: d.eng0_rpm, run: d.eng0_run });
    }
    if (d.eng1_n1_pct !== null || d.eng1_n2_pct !== null || d.eng1_run !== null) {
      engines.push({ n1: d.eng1_n1_pct, n2: d.eng1_n2_pct, tot: d.eng1_tot_c, rpm: d.eng1_rpm, run: d.eng1_run });
    }
    return {
      mode: 'fg',
      aircraft: d.aircraft || null,
      task: null,
      t: d.elapsed_s,
      roll: d.roll_deg, pitch: d.pitch_deg, heading: d.heading_deg,
      rollRate: d.roll_rate_dps, pitchRate: d.pitch_rate_dps, yawRate: d.yaw_rate_dps,
      ias_kt: d.ias_kt, ias_kmh: d.ias_kmh, gs_kt: d.gs_kt,
      vs_fpm: d.vs_fpm, vs_ms: d.vs_ms,
      alt_ft: d.alt_ft, agl_m: d.agl_m,
      mainRpm: d.main_rpm, mainRpmPct: d.main_rpm_pct, tailRpm: d.tail_rpm,
      torquePct: d.torque_pct, torqueRaw: d.torque_raw,
      torqueNote: '合计 /rotors/gear/total-torque',
      engines: engines,
      fuelGal: d.fuel_gal, grossKg: d.gross_kg, gLoad: d.g_load,
      slip: d.slip, windDir: d.wind_dir_deg, windKt: d.wind_kt,
      coll: d.coll, cyclicLat: d.cyclic_lat, cyclicLon: d.cyclic_lon, pedal: d.pedal,
      rotorBrake: d.rotor_brake,
      crashed: d.crashed, wow: d.wow, frozen: d.frozen,
      fdPitch: d.fd_pitch, fdRoll: d.fd_roll,
      lat: d.lat_deg, lon: d.lon_deg,
      hdgBug: S.hdgBug,
      reward: null,
      trackNote: 'WGS-84 等距投影',
      trackEmpty: '等待位移数据'
    };
  }

  /* ======================================================================
     二、RL 回放源
     ====================================================================== */

  function loadManifest(cb) {
    fetch(API.rlManifest + '?refresh=0')
      .then(function (r) { return r.json(); })
      .then(function (m) { cb(null, m); })
      .catch(function (e) { cb(e); });
  }

  function loadEpisode(file, seed, ep, cb) {
    var url = API.rlEpisode + '?file=' + encodeURIComponent(file) + '&seed=' + seed + '&ep=' + ep + '&max=6000';
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { cb(new Error(d.error)); return; }
      cb(null, d);
    }).catch(cb);
  }

  /** 由 RL 帧构建航迹与预积分航向（优先用轨迹里的 x_m/y_m/yaw_deg，否则积分） */
  function buildRLTrack(frames, dt) {
    var track = [], headings = [], hasRpm = false, hasSpeed = false;
    var usePos = frames[0] && ('x_m' in frames[0]) && ('y_m' in frames[0]);
    var useYaw = frames[0] && ('yaw_deg' in frames[0]);
    var x = 0, y = 0, psi = 0;
    for (var i = 0; i < frames.length; i++) {
      var f = frames[i];
      if (usePos) {
        x = f.x_m || 0; y = f.y_m || 0;
      } else {
        var u = f.vx_mps || 0, v = f.vy_mps || 0;
        var c = Math.cos(psi * Math.PI / 180), s = Math.sin(psi * Math.PI / 180);
        var vN = u * c - v * s;
        var vE = u * s + v * c;
        y += vN * dt; x += vE * dt;
      }
      if (useYaw) {
        psi = (f.yaw_deg === null || f.yaw_deg === undefined) ? psi : f.yaw_deg;
      } else {
        psi += (f.yaw_rate_dps || 0) * dt;
      }
      psi = ((psi % 360) + 360) % 360;
      headings.push(psi);
      if (f.rotor_rpm !== null && f.rotor_rpm !== undefined) hasRpm = true;
      if (f.speed_kts !== null && f.speed_kts !== undefined) hasSpeed = true;
      if (i % 2 === 0 || i === frames.length - 1) {
        track.push({ x: x, y: y, alt: f.agl_m || 0, t: f.t || i * dt });
      }
    }
    return { track: track, headings: headings, hasRpm: hasRpm, hasSpeed: hasSpeed };
  }

  function rlState() {
    var r = S.rl;
    if (!r.loaded || !r.frames) return null;
    var idx = Math.floor(r.idx);
    var f = r.frames.frames[idx];
    if (!f) return null;
    var sp = Math.hypot(f.vx_mps || 0, f.vy_mps || 0);
    // 航向已在加载时预积分，拖动进度条时直接查表
    var psi = r.headings ? (r.headings[idx] || 0) : 0;
    // 若轨迹提供真实空速/旋翼转速，优先使用；AH-1S 主旋翼额定约 324 RPM
    var iasKt = (f.speed_kts !== null && f.speed_kts !== undefined) ? Math.abs(f.speed_kts) : sp * 1.94384;
    var rotorRpm = (f.rotor_rpm !== null && f.rotor_rpm !== undefined) ? f.rotor_rpm : null;
    var rotorPct = rotorRpm !== null ? (rotorRpm / 324.0) * 100.0 : null;

    return {
      mode: 'rl',
      aircraft: r.frames.aircraft || null,
      task: r.frames.task || null,
      t: f.t,
      roll: f.roll_deg, pitch: f.pitch_deg, heading: psi,
      rollRate: f.roll_rate_dps, pitchRate: f.pitch_rate_dps, yawRate: f.yaw_rate_dps,
      ias_kt: iasKt, ias_kmh: iasKt * 1.852, gs_kt: iasKt,
      vs_fpm: (f.vz_mps || 0) * 196.85, vs_ms: f.vz_mps,
      alt_ft: (f.agl_m || 0) / 0.3048, agl_m: f.agl_m,
      mainRpm: rotorRpm, mainRpmPct: rotorPct, tailRpm: null,
      torquePct: null, torqueRaw: null,
      torqueNote: '轨迹未记录扭矩 · 见总距指令',
      rpmNote: rotorRpm !== null ? 'AH-1S 额定 324 RPM' : null,
      engines: [{ n1: null, n2: null, tot: null, rpm: null, run: null }],
      fuelGal: null, grossKg: null, gLoad: null,
      slip: null, windDir: null, windKt: null,
      coll: f.action_coll, cyclicLat: f.action_roll, cyclicLon: f.action_pitch, pedal: f.action_yaw,
      rotorBrake: null,
      crashed: r.frames.crashed, wow: null, frozen: null,
      fdPitch: null, fdRoll: null,
      lat: null, lon: null,
      hdgBug: 0,
      rawAction: {
        coll: f.action_coll === undefined ? null : f.action_coll,
        pitch: f.action_pitch === undefined ? null : f.action_pitch,
        roll: f.action_roll === undefined ? null : f.action_roll,
        yaw: f.action_yaw === undefined ? null : f.action_yaw
      },
      reward: f.reward,
      trackNote: '机体系速度积分（u/v + 偏航率）',
      trackEmpty: '无轨迹数据'
    };
  }

  /* ======================================================================
     三、渲染
     ====================================================================== */

  function drawAll(s) {
    if (!s) return;
    G.drawADI($('cvADI'), s);
    G.drawHSI($('cvHSI'), s);
    G.drawIAS($('cvIAS'), s);
    G.drawALT($('cvALT'), s);
    G.drawVSI($('cvVSI'), s);
    G.drawRPM($('cvRPM'), s, { title: s.mode === 'rl' ? 'ROTOR RPM' : 'ROTOR RPM' });
    G.drawTorque($('cvTQ'), s);
    G.drawEngines($('cvENG'), s);
    G.drawFuel($('cvFUEL'), s);
    G.drawTrack($('cvTrack'), s, S.mode === 'fg' ? S.fgTrack : (S.rl.track || []));
    G.drawProfile($('cvProfile'), S.mode === 'fg' ? S.fgTrack : (S.rl.track || []), s);
    drawControls(s);
    drawAnnunciators(s);
    drawReadouts(s);
  }

  /** 操纵量条：FG 为实际舵面位置（-1..1 / 0..1），RL 为策略动作输出（-1..1） */
  function setBar(id, val, min, max, label) {
    var el = $(id);
    if (!el) return;
    var fill = el.querySelector('.ctl-fill');
    var knob = el.querySelector('.ctl-knob');
    var valEl = el.querySelector('.ctl-val');
    if (val === null || val === undefined || !isFinite(val)) {
      fill.style.width = '0%';
      knob.style.left = '50%';
      knob.style.opacity = '.25';
      valEl.textContent = 'N/A';
      valEl.style.color = '#4a5568';
      return;
    }
    var frac = (G.clamp(val, min, max) - min) / (max - min);
    var pct = frac * 100;
    fill.style.left = Math.min(50, pct) + '%';
    fill.style.width = Math.abs(pct - 50) + '%';
    knob.style.left = pct + '%';
    knob.style.opacity = '1';
    valEl.textContent = label ? label(val) : val.toFixed(3);
    valEl.style.color = '#ffc043';
  }

  function drawControls(s) {
    if (s.mode === 'fg') {
      setBar('ctlColl', s.coll, 0, 1, function (v) { return (v * 100).toFixed(0) + '%'; });
      setBar('ctlLat', s.cyclicLat, -1, 1);
      setBar('ctlLon', s.cyclicLon, -1, 1);
      setBar('ctlPed', s.pedal, -1, 1);
    } else {
      setBar('ctlColl', s.coll, -1, 1);
      setBar('ctlLat', s.cyclicLat, -1, 1);
      setBar('ctlLon', s.cyclicLon, -1, 1);
      setBar('ctlPed', s.pedal, -1, 1);
    }
    $('ctlNote').textContent = s.mode === 'fg'
      ? 'FG 实际舵面位置（总距取 engine[0] 油门通道 / 周期变距取 aileron·elevator / 脚蹬取 rudder）'
      : 'RL 策略动作输出（PPO 归一化动作，−1…+1，非物理量）';
  }

  var ANNUN = [
    { id: 'anRpm', en: 'LOW RPM', cn: '低旋翼转速', cls: 'red' },
    { id: 'anTq', en: 'OVER TQ', cn: '超扭矩', cls: 'amber' },
    { id: 'anEng', en: 'ENG OUT', cn: '发动机停车', cls: 'red' },
    { id: 'anBrake', en: 'ROTOR BRK', cn: '旋翼刹车', cls: 'amber' },
    { id: 'anFuel', en: 'FUEL LOW', cn: '燃油不足', cls: 'amber' },
    { id: 'anFire', en: 'FIRE', cn: '火警', cls: 'red', noSrc: true },
    { id: 'anHyd', en: 'HYD', cn: '液压', cls: 'amber', noSrc: true },
    { id: 'anGen', en: 'GEN', cn: '发电', cls: 'amber', noSrc: true },
    { id: 'anChip', en: 'CHIP', cn: '金属屑', cls: 'amber', noSrc: true },
    { id: 'anWow', en: 'ON GROUND', cn: '接地', cls: 'green' },
    { id: 'anFreeze', en: 'SIM FROZEN', cn: '仿真冻结', cls: 'amber' },
    { id: 'anCrash', en: 'CRASH', cn: '坠毁', cls: 'red' }
  ];

  function buildAnnunciators() {
    var wrap = $('annun');
    wrap.innerHTML = '';
    ANNUN.forEach(function (a) {
      var d = document.createElement('div');
      d.className = 'ann ' + a.cls;
      d.id = a.id;
      d.innerHTML = '<span class="led"></span><span>' + a.en + '</span><span class="cn">' + a.cn + '</span>';
      if (a.noSrc) {
        d.style.opacity = '.45';
        d.title = '该告警在 FG 属性树中无对应数据源，未接入，不做推断';
      }
      wrap.appendChild(d);
    });
  }

  function setAnn(id, on, noSrc) {
    var el = $(id);
    if (!el) return;
    if (noSrc) { el.classList.remove('on'); return; }
    el.classList.toggle('on', !!on);
  }

  function drawAnnunciators(s) {
    setAnn('anRpm', s.mainRpmPct !== null && s.mainRpmPct < 90);
    setAnn('anTq', s.torquePct !== null && s.torquePct > 100);
    var engOut = false;
    (s.engines || []).forEach(function (e) { if (e.run === false) engOut = true; });
    setAnn('anEng', engOut);
    setAnn('anBrake', s.rotorBrake !== null && s.rotorBrake > 0);
    setAnn('anFuel', s.fuelGal !== null && s.fuelGal < 70);
    setAnn('anFire', false, true);
    setAnn('anHyd', false, true);
    setAnn('anGen', false, true);
    setAnn('anChip', false, true);
    setAnn('anWow', s.wow === true);
    setAnn('anFreeze', s.frozen === true);
    if (s.mode === 'rl') {
      // 坠毁是回合结局：回放到最后 3% 才点亮，避免开局就报警
      var r = S.rl;
      var prog = r.frames ? (r.idx / Math.max(1, r.frames.frames.length - 1)) : 0;
      setAnn('anCrash', s.crashed === true && prog > 0.97);
    } else {
      setAnn('anCrash', s.crashed === true);
    }
  }

  function setRO(id, key, val, unit, na) {
    var el = $(id);
    if (!el) return;
    el.classList.toggle('na', !!na);
    el.querySelector('.v').innerHTML = val + (unit ? '<span class="u">' + unit + '</span>' : '');
  }

  function drawReadouts(s) {
    setRO('roRollRate', 'k', nz(s.rollRate, 2), '°/s', s.rollRate === null);
    setRO('roPitchRate', 'k', nz(s.pitchRate, 2), '°/s', s.pitchRate === null);
    setRO('roYawRate', 'k', nz(s.yawRate, 2), '°/s', s.yawRate === null);
    setRO('roG', 'k', nz(s.gLoad, 2), 'g', s.gLoad === null);
    setRO('roTail', 'k', nz(s.tailRpm, 0), 'rpm', s.tailRpm === null);
    if (s.mode === 'fg') {
      setRO('roPos', 'k', (s.lat === null ? '–' : s.lat.toFixed(5) + '<br>' + s.lon.toFixed(5)), '', s.lat === null);
      $('roPos').querySelector('.k').textContent = 'LAT / LON 位置';
    } else {
      setRO('roPos', 'k', nz(s.reward, 3), '', s.reward === null);
      $('roPos').querySelector('.k').textContent = 'REWARD 即时奖励';
    }
    setRO('roAlt', 'k', nz(s.agl_m, 1), 'm AGL', s.agl_m === null);
    setRO('roSpd', 'k', nz(s.ias_kmh, 1), 'km/h', s.ias_kmh === null);
  }

  function updateTop(s) {
    var dot = $('connDot');
    var txt = $('connText');
    var src = document.querySelector('.ck-src');
    if (S.mode === 'fg') {
      if (S.fgConnected) {
        dot.className = 'ck-dot live';
        txt.textContent = 'FG LIVE · FlightGear 实时';
        if (src) { src.classList.remove('clickable'); src.title = ''; }
      } else if (S.launchPending) {
        dot.className = 'ck-dot busy';
        txt.textContent = 'FG 启动中…';
        if (src) { src.classList.remove('clickable'); src.title = 'FlightGear 正在启动，请稍候'; }
      } else {
        dot.className = 'ck-dot';
        txt.textContent = 'FG 未连接 · 点击启动';
        if (src) {
          src.classList.add('clickable');
          src.title = '点击一键启动 FlightGear 驾驶舱专用实例';
        }
      }
    } else {
      dot.className = 'ck-dot replay';
      txt.textContent = 'RL REPLAY · 轨迹回放';
      if (src) { src.classList.remove('clickable'); src.title = ''; }
    }
    $('chipAircraft').innerHTML = '<b>' + (s && s.aircraft ? String(s.aircraft).toUpperCase() : '–') + '</b>';
    $('chipTask').innerHTML = s && s.task ? ('<b>' + s.task + '</b>') : (S.mode === 'fg' ? '实机实操' : '–');
    $('chipTime').innerHTML = '<b>' + nz(s ? s.t : null, 2) + '</b>';
    var d = new Date();
    $('chipClock').innerHTML = '<b>' + d.toTimeString().slice(0, 8) + '</b>';
    var npS = $('npSource'), npR = $('npRate');
    if (npS) npS.textContent = S.mode === 'fg' ? 'FG LIVE' : 'RL REPLAY';
    if (npR) npR.textContent = S.mode === 'fg' ? '10 Hz' : (S.rl.rate + '×');
  }

  var lastBannerSig = '';
  function updateBanner() {
    var b = $('ckBanner');
    // 仅在关键状态变化时重建 DOM，避免每帧重写导致点击被吞
    var sig = [S.mode, S.fgConnected, STATIC_PREVIEW, S.launchPending, S.launchMsg, (S.fgError || '')].join('|');
    if (sig === lastBannerSig) return;
    lastBannerSig = sig;

    if (S.mode === 'fg' && !S.fgConnected && STATIC_PREVIEW) {
      // 静态预览无后端：直接给出可点击的真实地址，避免误判为 FG 本身有问题
      b.className = 'ck-banner err';
      b.innerHTML = '<b>当前是通过静态预览打开，没有后端数据。</b>' +
        '预览面板只加载页面文件，<code>/api/*</code> 无法访问（必然 404）。' +
        '请在浏览器地址栏打开 <a href="' + COCKPIT_URL + '" target="_blank" style="color:#8fd8ff">' +
        COCKPIT_URL + '</a>（桥接服务已在 ' + BRIDGE_PORT + ' 端口运行）。';
      b.classList.remove('hidden');
    } else if (S.mode === 'fg' && !S.fgConnected) {
      var port = S.fgPort || 5502;
      b.className = 'ck-banner err';
      var errMsg = S.launchPending ? '' : (S.fgError ? (S.fgError + '　') : '');
      var prog = S.launchPending ? ('　<b style="color:#ffc043">' + (S.launchMsg || 'FlightGear 启动中…') + '</b>') : '';
      b.innerHTML = '<b>FG 未连接。</b>' + errMsg +
        '<a href="#" id="bnLaunch" class="ck-link-inline">▶ 一键启动 FlightGear</a>' + prog +
        '　未连接时可用「RL 回放」查看已存档轨迹。';
      var lb = $('bnLaunch');
      if (lb) lb.onclick = function (e) { e.preventDefault(); launchFG(); };
      b.classList.remove('hidden');
    } else if (S.mode === 'fg') {
      b.className = 'ck-banner';
      b.innerHTML = '数据源：<b>FlightGear 属性服务器（只读）</b> · 采样 10 Hz · 驾驶舱只显示，不向 FG 写入任何指令。';
      b.classList.remove('hidden');
    } else {
      b.className = 'ck-banner';
      b.innerHTML = '数据源：<b>rl_train/results 轨迹回放</b> · 轨迹未记录发动机/扭矩/燃油等通道，相应仪表显示 N/A（不补数、不编造）。';
      b.classList.remove('hidden');
    }
  }

  /* ---------------- 主循环 ---------------- */
  var lastT = performance.now();
  function loop(now) {
    requestAnimationFrame(loop);
    if (now - S.lastDraw < 33) return;     // ~30 fps 上限，避免高频重绘
    S.lastDraw = now;

    var dtReal = (now - lastT) / 1000;
    lastT = now;

    if (S.mode === 'rl' && S.rl.playing && S.rl.frames) {
      var frames = S.rl.frames.frames;
      var adv = (dtReal * S.rl.rate) / (S.rl.frames.dt || 0.02);
      S.rl.idx += adv;
      if (S.rl.idx >= frames.length - 1) {
        S.rl.idx = frames.length - 1;
        S.rl.playing = false;
        $('rpPlay').textContent = '▶ 播放';
      }
      var pc = frames.length > 1 ? (S.rl.idx / (frames.length - 1)) * 100 : 0;
      $('rpScrub').value = pc;
      $('rpTime').textContent = nz(frames[Math.floor(S.rl.idx)].t, 2) + ' / ' +
        nz(frames[frames.length - 1].t, 2) + ' s';
    }

    var s = S.mode === 'fg' ? (S.fgConnected ? fgState() : null) : rlState();
    if (!s) {
      // 无数据：也要画出空表盘，保持座舱形态
      s = {
        rawAction: {},
        mode: S.mode, aircraft: null, task: null, t: null, roll: null, pitch: null, heading: null,
        rollRate: null, pitchRate: null, yawRate: null, ias_kt: null, ias_kmh: null, gs_kt: null,
        vs_fpm: null, vs_ms: null, alt_ft: null, agl_m: null, mainRpm: null, mainRpmPct: null,
        tailRpm: null, torquePct: null, torqueRaw: null, engines: [], fuelGal: null, grossKg: null,
        gLoad: null, slip: null, windDir: null, windKt: null, coll: null, cyclicLat: null,
        cyclicLon: null, pedal: null, rotorBrake: null, crashed: null, wow: null, frozen: null,
        fdPitch: null, fdRoll: null, lat: null, lon: null, hdgBug: null, reward: null,
        trackEmpty: S.mode === 'fg' ? 'FG 未连接' : '尚未载入轨迹'
      };
    }
    // ---- 操纵机构：物理推进 → 数据联动 / 接管输出 ----
    if (CTL) {
      CTL.step(dtReal);
      if (CTL.follow) syncControls(s);
      var out = CTL.output();
      if (!CTL.follow) {
        // 本地演示或接管：仪表控制位由操纵机构给出
        s.coll = out.collective;
        s.cyclicLat = out.cyclicRoll;
        s.cyclicLon = out.cyclicPitch;
        s.pedal = out.pedals;
        s.rotorBrake = out.brake ? 1 : 0;
        s.estop = out.estop;
      }
      pushControls(out);
    }

    updateTop(s);
    updateBanner();
    drawAll(s);
    if (CTL) {
      KC.drawCyclic($('cvCyclic'), CTL);
      KC.drawCollective($('cvCollective'), CTL);
      KC.drawPedals($('cvPedals'), CTL);
    }
  }

  /* ======================================================================
     四、UI 绑定
     ====================================================================== */

  function switchMode(m) {
    S.mode = m;
    $('btnFg').classList.toggle('on', m === 'fg');
    $('btnRl').classList.toggle('on', m === 'rl');
    $('rpPanel').classList.toggle('hidden', m !== 'rl');
    // 回放条悬浮在面板底部，RL 模式下隐藏面板铭牌避免遮挡
    document.body.classList.toggle('replay-mode', m === 'rl');
    // FG 模式：显示中央 FG 画面；RL 模式：把中央区用作 ADI/HSI 大仪表区
    var img = $('fgView');
    if (img) img.style.display = m === 'fg' ? 'block' : 'none';
    var overlay = $('fgOverlay');
    if (overlay) {
      overlay.style.display = m === 'fg' ? 'grid' : 'none';
      overlay.textContent = '正在连接 FlightGear 画面，首次加载约 3–5 秒…';
    }
    if (m === 'fg') {
      S.fgViewActive = false;
      startFGView();
    } else {
      if (fgViewTimer) { clearTimeout(fgViewTimer); fgViewTimer = null; }
      var img = $('fgView');
      if (img) { img.classList.remove('live'); img.src = ''; }
    }
    if (m === 'rl' && !S.rl.frames) loadRLDefaults();
  }

  function loadRLDefaults() {
    if (S.rl.manifestLoaded) return;
    loadManifest(function (err, m) {
      if (err) {
        $('rpFile').innerHTML = '<option>轨迹服务不可用（桥接服务未启动）</option>';
        return;
      }
      S.rl.manifestLoaded = true;
      var sel = $('rpFile');
      sel.innerHTML = '';
      var usable = (m.files || []).filter(function (f) { return (f.episodes || []).length > 0; });
      usable.forEach(function (f) {
        var meta = f.meta || {};
        var o = document.createElement('option');
        o.value = f.file;
        o.textContent = (meta.task || f.file.replace('_trajectory.json', '')) +
          ' · ' + (meta.aircraft || '?') + ' · ' + f.episodes.length + ' 回合 · ' +
          (meta.verified_timestamp || '');
        o.dataset.task = meta.task || '';
        sel.appendChild(o);
      });
      if (usable.length) { sel.selectedIndex = 0; fillEpisodes(); }
    });
  }

  function fillEpisodes() {
    var file = $('rpFile').value;
    loadManifest(function (err, m) {
      if (err) return;
      var f = (m.files || []).filter(function (x) { return x.file === file; })[0];
      if (!f) return;
      var sel = $('rpEp');
      sel.innerHTML = '';
      (f.episodes || []).forEach(function (e) {
        var o = document.createElement('option');
        o.value = e.seed + '|' + e.episode;
        o.textContent = 'seed ' + e.seed + ' · ep ' + e.episode +
          ' · ' + e.n_steps + ' 步 · ' + (e.crashed ? '坠毁' : (e.success ? '成功' : '未达标'));
        sel.appendChild(o);
      });
      if (sel.options.length) loadSelectedEpisode();
    });
  }

  function loadSelectedEpisode() {
    var file = $('rpFile').value;
    var v = ($('rpEp').value || '').split('|');
    if (!file || v.length !== 2) return;
    $('rpTag').textContent = '载入中…';
    $('rpTag').className = 'ck-tag';
    loadEpisode(file, v[0], v[1], function (err, d) {
      if (err) {
        $('rpTag').textContent = '载入失败：' + err.message;
        $('rpTag').className = 'ck-tag bad';
        return;
      }
      S.rl.frames = d;
      S.rl.idx = 0;
      S.rl.loaded = true;
      var tr = buildRLTrack(d.frames, d.dt || 0.02);
      S.rl.track = tr.track;
      S.rl.headings = tr.headings;
      S.rl.playing = true;
      $('rpPlay').textContent = '❚❚ 暂停';
      $('rpScrub').value = 0;
      $('rpTag').textContent = d.task + ' · ' + d.aircraft + ' · ' +
        (d.crashed ? '坠毁' : (d.success ? '成功' : '未达标')) +
        ' · ' + d.frames.length + ' 帧' + (d.downsampled ? '（已降采样自 ' + d.raw_frames + '）' : '');
      $('rpTag').className = 'ck-tag ' + (d.crashed ? 'bad' : (d.success ? 'ok' : ''));
    });
  }

  function buildRateButtons() {
    var wrap = $('rpRate');
    wrap.innerHTML = '';
    RATES.forEach(function (r) {
      var b = document.createElement('button');
      b.textContent = r + '×';
      b.className = r === 1 ? 'on' : '';
      b.onclick = function () {
        S.rl.rate = r;
        Array.prototype.forEach.call(wrap.children, function (c) { c.classList.remove('on'); });
        b.classList.add('on');
      };
      wrap.appendChild(b);
    });
  }

  /* ------------------------------------------------------------------ *
   * 操纵台：初始化、与数据源联动、写回仿真
   * ------------------------------------------------------------------ */

  function initControls() {
    CTL = new KC.Controls();
    CTL.mountSwitches($('swButtons'), $('swKnobs'));
    KC.bindStick($('cvCyclic'), CTL);
    // 总距杆：向上提 = 增大（y=0 在顶端）
    KC.bindAxis($('cvCollective'), CTL.collective, function (p) { return 1 - p.y; }, CTL);
    // 脚蹬：向右踩 = 正
    KC.bindAxis($('cvPedals'), CTL.pedal, function (p) { return p.x; }, CTL);

    $('btnTakeover').onclick = toggleTakeover;
    window.COCKPIT_CTL = CTL;   // 调试/自动化钩子，与 COCKPIT_STATE 同口径
    // 服务端写入能力探测（只读模式下明确告知）
    fetch('/api/fg/capabilities').then(function (r) { return r.json(); }).then(function (d) {
      WRITE_OK = !!(d && d.write_enabled);
      updateTakeoverUI();
    }).catch(function () { WRITE_OK = false; updateTakeoverUI(); });
  }

  function toggleTakeover() {
    if (!CTL) return;
    if (!CTL.takeover && S.mode === 'rl') {
      CTL._flash('回放模式下不可接管：请切到 FG 实时');
      return;
    }
    var willOn = !CTL.takeover;
    if (willOn) {
      // 接管时顺带请求服务端开启写回（无需重启桥接），让操作真正落到 FlightGear
      fetch(API.fgWrite, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.write_enabled) WRITE_OK = true;
        updateTakeoverUI();
      }).catch(function () { updateTakeoverUI(); })
        .then(applyToggle);
    } else {
      applyToggle();
    }
    function applyToggle() {
      CTL.takeover = !CTL.takeover;
      CTL.follow = !CTL.takeover;
      updateTakeoverUI();
      if (CTL.takeover) {
        // 区分两件事：本地可操纵（始终允许） vs 写回仿真（需服务端 --allow-write）
        CTL._flash(WRITE_OK
          ? '已接管操纵：操作将写入 FlightGear'
          : '本地操纵已开：可操作各机构，但服务端为只读，不会写回 FlightGear');
      } else {
        CTL._flash('已退出操纵：恢复跟随仿真数据');
      }
    }
  }

  function updateTakeoverUI() {
    var btn = $('btnTakeover');
    if (!btn) return;
    var on = CTL && CTL.takeover;
    btn.classList.toggle('on', !!on);
    btn.textContent = '接管操纵：' + (on ? '开' : '关');
    btn.title = WRITE_OK
      ? '本地操纵并写回 FlightGear（服务端已开启写入）'
      : '本地操纵（不回写）：需以 --allow-write 启动服务端才能写回 FlightGear';
    if ($('npNote')) {
      $('npNote').textContent = on
        ? (WRITE_OK ? '⚠ 接管中 · 操作写入 FlightGear' : '⚠ 本地操纵中 · 不回写仿真')
        : (WRITE_OK ? '可接管 · 当前跟随仿真数据' : '跟随仿真数据 · 可切本地操纵');
    }
  }

  /** 把驾驶舱状态喂给操纵机构（跟随模式） */
  function syncControls(s) {
    if (!CTL) return;
    if (s.mode === 'fg') {
      // FG 实际舵面：collective 取 engine[0] 油门通道；周期变距取 aileron/elevator；脚蹬取 rudder
      CTL.setFromData({
        collective: s.coll,
        cyclicPitch: s.cyclicLon,
        cyclicRoll: s.cyclicLat,
        pedal: s.pedal
      });
    } else {
      // RL 回放：动作输出按契约 §1.1 解码（collective=(a+1)/2）
      var a = s.rawAction || {};
      CTL.setFromData({
        collective: a.coll === null || a.coll === undefined ? null : (a.coll + 1) / 2,
        cyclicPitch: a.pitch, cyclicRoll: a.roll, pedal: a.yaw
      });
    }
  }

  /** 接管模式下把操纵量写回 FlightGear（10 Hz 节流） */
  function pushControls(out) {
    if (!CTL || !CTL.takeover || !WRITE_OK) return;
    var now = performance.now();
    if (now - lastPush < 100) return;
    lastPush = now;
    fetch('/api/fg/set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        collective: out.collective,
        aileron: out.cyclicRoll,
        elevator: out.cyclicPitch,
        rudder: out.pedals,
        rotor_brake: out.brake ? 1 : 0
      })
    }).catch(function () { /* 写入失败不打断显示 */ });
  }

  function init() {
    buildAnnunciators();
    buildRateButtons();
    initControls();

    $('btnFg').onclick = function () { switchMode('fg'); };
    $('btnRl').onclick = function () { switchMode('rl'); };

    // 顶栏「FG 未连接」整块可点击：一键拉起驾驶舱专用 FlightGear 实例
    var srcEl = document.querySelector('.ck-src');
    if (srcEl) {
      srcEl.setAttribute('role', 'button');
      srcEl.tabIndex = 0;
      srcEl.addEventListener('click', function () {
        if (!S.fgConnected && !S.launchPending) launchFG();
      });
      srcEl.addEventListener('keydown', function (e) {
        if ((e.key === 'Enter' || e.key === ' ') && !S.fgConnected && !S.launchPending) {
          e.preventDefault(); launchFG();
        }
      });
    }
    // 回填桥接实际监听的 FG 端口，使横幅提示与一键启动对齐
    fetch('/api/health').then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.fg_port) S.fgPort = d.fg_port;
    }).catch(function () {});

    initDraggablePips();
    initKeysModal();
    // 默认进入 RL 回放：轨迹已存档、稳定可用；FG LIVE 作为可切换源
    switchMode('rl');

    $('rpFile').onchange = fillEpisodes;
    $('rpEp').onchange = loadSelectedEpisode;
    $('rpReload').onclick = function () { S.rl.manifestLoaded = false; loadRLDefaults(); };
    $('rpPlay').onclick = function () {
      if (!S.rl.frames) return;
      S.rl.playing = !S.rl.playing;
      if (S.rl.playing && S.rl.idx >= S.rl.frames.frames.length - 1) S.rl.idx = 0;
      $('rpPlay').textContent = S.rl.playing ? '❚❚ 暂停' : '▶ 播放';
    };
    $('rpStepB').onclick = function () {
      if (!S.rl.frames) return;
      S.rl.playing = false;
      $('rpPlay').textContent = '▶ 播放';
      S.rl.idx = Math.max(0, S.rl.idx - 1);
      syncScrub();
    };
    $('rpStepF').onclick = function () {
      if (!S.rl.frames) return;
      S.rl.playing = false;
      $('rpPlay').textContent = '▶ 播放';
      S.rl.idx = Math.min(S.rl.frames.frames.length - 1, S.rl.idx + 1);
      syncScrub();
    };
    $('rpScrub').oninput = function () {
      if (!S.rl.frames) return;
      var n = S.rl.frames.frames.length;
      S.rl.idx = Math.round(($('rpScrub').value / 100) * (n - 1));
      syncScrub();
    };

    startFGStream();
    requestAnimationFrame(loop);
  }

  function syncScrub() {
    var frames = S.rl.frames.frames;
    $('rpTime').textContent = nz(frames[Math.floor(S.rl.idx)].t, 2) + ' / ' +
      nz(frames[frames.length - 1].t, 2) + ' s';
  }

  /* ---------------- 画中画拖动 ---------------- */
  function initDraggablePips() {
    ['pipADI', 'pipHSI'].forEach(function (id) {
      var el = $(id); if (!el) return;
      var parent = el.parentElement, dragging = false, ox, oy, sx, sy;
      el.addEventListener('pointerdown', function (e) {
        dragging = true;
        el.setPointerCapture(e.pointerId);
        el.classList.add('dragging');
        ox = e.clientX; oy = e.clientY;
        sx = el.offsetLeft; sy = el.offsetTop;
        e.preventDefault();
      });
      el.addEventListener('pointermove', function (e) {
        if (!dragging) return;
        var dx = e.clientX - ox, dy = e.clientY - oy;
        var nx = sx + dx, ny = sy + dy;
        var maxX = parent.clientWidth - el.clientWidth;
        var maxY = parent.clientHeight - el.clientHeight;
        el.style.left = Math.max(0, Math.min(maxX, nx)) + 'px';
        el.style.top = Math.max(0, Math.min(maxY, ny)) + 'px';
        el.style.right = 'auto';
        e.preventDefault();
      });
      el.addEventListener('pointerup', function () { dragging = false; el.classList.remove('dragging'); });
      el.addEventListener('pointercancel', function () { dragging = false; el.classList.remove('dragging'); });
      el.style.touchAction = 'none';
    });
  }

  /* ---------------- 快捷键映射表 ---------------- */
  var KEYMAP = [
    { dev: '总距杆 COLLECTIVE', action: '上提/下放改变总距（升力）', key: '鼠标/触摸拖动 或 PageUp/PageDown', prop: '/sim/controls/engine/throttle', note: 'FG 中 throttle 同时驱动总距；接管时写回 throttle 通道' },
    { dev: '周期变距杆 CYCLIC', action: '前后推/拉 = 俯仰；左右压 = 滚转', key: '鼠标/触摸拖动', prop: '/sim/controls/flight/aileron, /sim/controls/flight/elevator', note: '摇杆松手自动回中（弹簧）' },
    { dev: '脚蹬 PEDALS', action: '左/右踩控制偏航（尾桨）', key: '鼠标/触摸拖动 或 Z/X', prop: '/sim/controls/flight/rudder', note: '脚踏联动：左进右退' },
    { dev: 'STARTER 按钮', action: '接通启动马达（瞬时）', key: '点击 或 Shift+S', prop: '/sim/model/start-switch', note: '仅示意，真启动需按机型程序' },
    { dev: 'ROTOR BRK 按钮', action: '旋翼刹车 开/关', key: '点击', prop: '/sim/controls/rotor/brake', note: '地面停车用' },
    { dev: 'STAB AUG 按钮', action: '增稳系统 开/关', key: '点击', prop: '—', note: '本地开关，是否写回 FG 取决于接管状态' },
    { dev: 'EMER STOP 按钮', action: '急停 ESD：总距保持，姿态归零', key: '点击', prop: '—', note: '本地安全开关，不会直接写 FG' },
    { dev: 'MAG SWITCH 旋钮', action: '磁电机 OFF/R/L/BOTH/START', key: '点击/滚轮/方向键', prop: '/engines/engine/magnetos', note: '多档选择' },
    { dev: 'FUEL VALVE 旋钮', action: '燃油阀 OFF/ON', key: '点击/滚轮', prop: '/sim/fuel/fuel-selector', note: '两档开关' },
    { dev: 'LAG MODEL 旋钮', action: '执行器延迟模型 L0/L1', key: '点击/滚轮', prop: '—', note: '纯本地训练演示参数' },
    { dev: '接管操纵', action: '把驾驶舱操作写回 FlightGear', key: '顶栏「接管操纵」', prop: '/api/fg/set', note: '需桥接以 --allow-write 启动且连 FG 5502' }
  ];

  function buildKeymap() {
    var host = $('keysMap');
    if (!host) return;
    var html = '<table class="kb-table"><thead><tr><th>驾驶舱器具</th><th>操作含义</th><th>FG 默认键 / 我们的交互</th><th>FG 属性 / 接口</th><th>备注</th></tr></thead><tbody>';
    KEYMAP.forEach(function (k) {
      html += '<tr><td class="dev">' + k.dev + '</td><td>' + k.action + '</td><td class="key">' + k.key + '</td><td class="prop">' + k.prop + '</td><td class="note">' + k.note + '</td></tr>';
    });
    html += '</tbody></table>';
    host.innerHTML = html;
  }

  function getInputLog() {
    try { return JSON.parse(localStorage.getItem('cockpit_input_log_v1') || '[]'); } catch (e) { return []; }
  }

  function renderAnalysis() {
    var host = $('keysAnalysis');
    if (!host) return;
    var log = getInputLog();
    var now = Date.now();
    var recent = log.filter(function (e) { return now - e.t < 30000; });
    if (!log.length) { host.innerHTML = '<div class="anal-empty">暂无手动操作记录。拖动摇杆/总距/脚蹬或点击按钮、旋钮后数据会出现在这里。</div>'; return; }

    var counts = {}, recentCounts = {}, ranges = {};
    log.forEach(function (e) {
      var id = e.type + ':' + e.id;
      counts[id] = (counts[id] || 0) + 1;
      if (now - e.t < 30000) recentCounts[id] = (recentCounts[id] || 0) + 1;
      if (e.type === 'axis' || e.type === 'stick') {
        var v = Array.isArray(e.value) ? e.value[0] : e.value;
        if (!ranges[id]) ranges[id] = { min: v, max: v, sum: 0, n: 0 };
        var r = ranges[id];
        r.min = Math.min(r.min, v); r.max = Math.max(r.max, v);
        r.sum += v; r.n += 1;
      }
    });

    var total = log.length;
    var active = Object.keys(counts).length;
    var recentTotal = recent.length;
    var avgRate = recentTotal / 30;

    var html = '<div class="anal-grid">' +
      '<div class="anal-card"><div class="k">总操作次数</div><div class="v">' + total + '<span class="u">次</span></div></div>' +
      '<div class="anal-card"><div class="k">30 秒内</div><div class="v">' + recentTotal + '<span class="u">次</span></div></div>' +
      '<div class="anal-card"><div class="k">平均频率</div><div class="v">' + avgRate.toFixed(1) + '<span class="u">次/秒</span></div></div>' +
      '<div class="anal-card"><div class="k">活跃控件</div><div class="v">' + active + '<span class="u">个</span></div></div>' +
      '</div>';

    html += '<h4 style="font-family:var(--ck-mono);font-size:var(--fz-text);color:var(--ck-ink2);margin:12px 0 6px;">最近 30 秒各控件活动占比</h4>';
    var ids = Object.keys(recentCounts).sort(function (a, b) { return recentCounts[b] - recentCounts[a]; });
    ids.forEach(function (id) {
      var pct = (recentCounts[id] / Math.max(1, recentTotal)) * 100;
      html += '<div class="anal-bar-wrap"><span>' + id + '</span><div class="anal-bar"><i style="width:' + pct.toFixed(1) + '%"></i></div><span style="width:40px;text-align:right;font-family:var(--ck-mono);font-size:var(--fz-label);color:#8fa3bd;">' + pct.toFixed(0) + '%</span></div>';
    });

    html += '<h4 style="font-family:var(--ck-mono);font-size:var(--fz-text);color:var(--ck-ink2);margin:12px 0 6px;">操纵量范围 &amp; 均值（全部历史）</h4>';
    Object.keys(ranges).forEach(function (id) {
      var r = ranges[id];
      html += '<div class="anal-bar-wrap"><span>' + id + '</span><div style="flex:1;font-family:var(--ck-mono);font-size:var(--fz-label);color:#8fa3bd;">min ' + r.min.toFixed(2) + ' · avg ' + (r.sum / r.n).toFixed(2) + ' · max ' + r.max.toFixed(2) + '</div></div>';
    });

    html += '<div style="margin-top:14px;display:flex;gap:10px;">' +
      '<button class="ck-chip" id="btnExportLog" style="cursor:pointer;">导出操作日志 (JSONL)</button>' +
      '<button class="ck-chip" id="btnClearLog" style="cursor:pointer;color:#ff9e9e;">清空记录</button>' +
      '</div>';

    host.innerHTML = html;
    var btnE = $('btnExportLog');
    if (btnE) btnE.onclick = function () {
      var blob = new Blob(log.map(function (e) { return JSON.stringify(e) + '\n'; }), { type: 'application/jsonlines' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'cockpit_inputs_' + new Date().toISOString().slice(0, 19).replace(/:/g, '-') + '.jsonl';
      a.click();
    };
    var btnC = $('btnClearLog');
    if (btnC) btnC.onclick = function () {
      if (confirm('确定清空本机所有操作记录？')) { localStorage.removeItem('cockpit_input_log_v1'); renderAnalysis(); }
    };
  }

  function toggleKeys(show) {
    var modal = $('keysModal');
    if (!modal) return;
    if (show === undefined) show = modal.classList.contains('hidden');
    modal.classList.toggle('hidden', !show);
    if (show) { buildKeymap(); renderAnalysis(); }
  }

  function initKeysModal() {
    var modal = $('keysModal');
    if (!modal) return;
    $('btnKeys').onclick = function () { toggleKeys(true); };
    $('keysClose').onclick = function () { toggleKeys(false); };
    modal.querySelector('.ck-modal-backdrop').onclick = function () { toggleKeys(false); };
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !modal.classList.contains('hidden')) toggleKeys(false);
    });
    var tabs = modal.querySelectorAll('.ck-modal-tabs button');
    tabs.forEach(function (t) {
      t.onclick = function () {
        tabs.forEach(function (x) { x.classList.remove('on'); });
        t.classList.add('on');
        var name = t.dataset.tab;
        $('keysMap').classList.toggle('hidden', name !== 'map');
        $('keysAnalysis').classList.toggle('hidden', name !== 'analysis');
        if (name === 'analysis') renderAnalysis();
      };
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 暴露调试句柄（仅用于排查，不暴露敏感信息）
  window.COCKPIT_STATE = S;
})();
