/* ============================================================
   训练证据链管理平台 · 前端逻辑（原生 JS，零依赖，离线可用）
   ============================================================ */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  overview: null,
  evidence: [],
  evidenceLoaded: false,
  recFilter: "all",
  recSearch: "",
};

/* ---------------- 工具 ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function shortHash(h, n = 10) {
  return h ? h.slice(0, n) + "…" : "-";
}

function fmtNum(v, digits = 1) {
  if (v === null || v === undefined) return "--";
  return Number(v).toLocaleString("zh-CN",
    { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function relTime(iso) {
  if (!iso) return "-";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(s)) return iso;
  if (s < 10) return "刚刚";
  if (s < 60) return `${Math.floor(s)} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function copyText(text, ev) {
  const done = () => {
    const t = ev?.target;
    if (t) { const old = t.textContent; t.textContent = "已复制 ✓"; setTimeout(() => t.textContent = old, 900); }
  };
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text; document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
  ta.remove();
}

const TYPE_ZH = {
  milestone: "进度节点", training_run: "训练运行", evaluation: "评估",
  verification: "指标验证", incident: "事故/失败", analysis: "分析报告",
  data_collection: "数据采集", note: "人工记录",
};

/* ---------------- 图表（SVG 折线） ---------------- */

function lineChart(container, points, opts = {}) {
  const W = 600, H = 220, padL = 40, padR = 14, padT = 14, padB = 26;
  if (!points || points.length === 0) {
    container.innerHTML = `<div class="empty">暂无曲线数据</div>`;
    return;
  }
  const xs = points.map((p) => p.steps);
  const xMax = Math.max(...xs, 1), xMin = 0;
  const rawMax = Math.max(...points.map((p) => Math.max(p.success_rate ?? 0, p.crash_rate ?? 0)));
  const yMax = rawMax > 1.0001 ? 100 : 1;
  const norm = (v) => (v === null || v === undefined) ? null : v / yMax;
  const X = (v) => padL + (v - xMin) / (xMax - xMin || 1) * (W - padL - padR);
  const Y = (v) => padT + (1 - v) * (H - padT - padB);
  const path = (key, cls) => {
    let d = "", started = false;
    for (const p of points) {
      const nv = norm(p[key]);
      if (nv === null) { started = false; continue; }
      d += `${started ? "L" : "M"}${X(p.steps).toFixed(1)},${Y(nv).toFixed(1)} `;
      started = true;
    }
    return d ? `<path d="${d}" fill="none" stroke="${cls}" stroke-width="2"
      stroke-linejoin="round" filter="drop-shadow(0 0 4px ${cls}66)"/>` : "";
  };
  const dots = points.map((p) => {
    const nv = norm(p.success_rate);
    if (nv === null) return "";
    return `<circle cx="${X(p.steps).toFixed(1)}" cy="${Y(nv).toFixed(1)}" r="3"
      fill="#0a1120" stroke="#34d399" stroke-width="1.6"/>`;
  }).join("");
  const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = yMax * f;
    const label = yMax === 1 ? `${Math.round(v * 100)}%` : `${Math.round(v)}%`;
    return `<line x1="${padL}" y1="${Y(f)}" x2="${W - padR}" y2="${Y(f)}"
      stroke="#1c2b45" stroke-dasharray="3 5"/>
      <text x="${padL - 7}" y="${Y(f) + 4}" text-anchor="end" font-size="10"
      fill="#5b6f8d" font-family="Consolas">${label}</text>`;
  }).join("");
  const gridX = [0, 0.5, 1].map((f) => {
    const v = xMin + f * (xMax - xMin);
    return `<text x="${X(v)}" y="${H - 8}" text-anchor="middle" font-size="10"
      fill="#5b6f8d" font-family="Consolas">${fmtNum(v, 0)}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridY}${gridX}
    ${path("crash_rate", "#f87171")}
    ${path("success_rate", "#34d399")}
    ${dots}
  </svg>`;
}

/* ---------------- Tab 切换 ---------------- */

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.id === `tab-${name}`));
  if (name === "chain" || name === "records") loadEvidence().catch(console.error);
  if (name === "live") renderLiveTab();
}

/* ---------------- 看板 ---------------- */

function kpiCard(m) {
  const cls = m.status === "pass" ? "pass" : m.status === "fail" ? "fail" : "pending";
  let valHtml, barW = 0;
  if (m.measured === null || m.measured === undefined) {
    valHtml = `待验证`;
  } else {
    const disp = m.display === "percent" ? `${fmtNum(m.measured)}<small>%</small>`
      : `${fmtNum(m.measured)}<small>${esc(m.unit)}</small>`;
    valHtml = disp;
    barW = m.dir === ">=" ? Math.min(100, m.measured / m.threshold * 100)
      : (m.measured > 0 ? Math.min(100, m.threshold / m.measured * 100) : 100);
  }
  const dirTxt = m.dir === ">=" ? "≥" : "≤";
  const evLink = m.evidence_id
    ? `<span class="link" onclick="showEvidence('${esc(m.evidence_id)}')">证据 ${esc(m.evidence_id)} ›</span>` : "";
  return `<div class="kpi ${cls}">
    <div class="top"><span class="name">${esc(m.name)}</span><span class="kid">${esc(m.id)}</span></div>
    <div class="val">${valHtml}</div>
    <div class="req">要求 ${dirTxt}${fmtNum(m.threshold)}${m.display === "percent" ? "%" : esc(m.unit)}</div>
    <div class="desc">${esc(m.desc || "")}</div>
    <div class="bar"><i style="width:${barW}%"></i></div>
    ${evLink}
  </div>`;
}

function renderDashboard() {
  const o = state.overview;
  if (!o) return;

  // KPI
  $("#kpis").innerHTML = o.metrics.map(kpiCard).join("");

  // 工期进度
  const pct = o.project.progress_pct;
  $("#projPct").textContent = pct === null || pct === undefined ? "--" : fmtNum(pct) + "%";
  $("#projBar").style.width = (pct || 0) + "%";
  $("#t0Note").textContent = o.project.t0 ? `（T0 = ${o.project.t0}）` : "";

  // 时间线
  $("#timeline").innerHTML = o.milestones.map((m) => {
    const cls = m.status;
    const mark = m.status === "done" ? "✓" : m.status === "current" ? "●" : "○";
    return `<div class="tnode ${cls}">
      <div class="ndot">${mark}</div>
      <div class="nname">${esc(m.name)}</div>
      <div class="ndate">${esc(m.date)}</div>
      <span class="ntag ${m.status}">${esc(m.label || m.id)}${m.status === "done" ? " · 完成" : m.status === "current" ? " · 进行中" : ""}</span>
    </div>`;
  }).join("");

  // 实时训练迷你卡
  renderLiveMini();

  // 曲线
  lineChart($("#curveChart"), o.success_curve || []);

  // 最新证据
  const lv = $("#latestEvi");
  if (!o.latest_evidence.length) {
    lv.innerHTML = `<div class="empty">暂无证据</div>`;
  } else {
    lv.innerHTML = o.latest_evidence.map(eviRow).join("");
  }

  // 顶部胶囊
  const cp = $("#chainPill");
  if (o.chain_ok === true) {
    cp.className = "pill ok";
    cp.innerHTML = `<span class="dot"></span>哈希链完整 · ${o.counts.total} 条证据`;
  } else if (o.chain_ok === false) {
    cp.className = "pill bad";
    cp.innerHTML = `<span class="dot"></span>哈希链异常！`;
  }
  const lp = $("#livePill");
  if (o.live?.status === "running") {
    lp.className = "pill live";
    lp.innerHTML = `<span class="dot"></span>训练进行中`;
  } else {
    lp.className = "pill";
    lp.innerHTML = `<span class="dot"></span>训练空闲`;
  }
  if (o.server_time) {
    const d = new Date(o.server_time);
    $("#clock").textContent = isNaN(d) ? o.server_time
      : d.toTimeString().slice(0, 8);
  }
}

function eviRow(r) {
  return `<div class="evi-item" onclick="showEvidence('${esc(r.id)}')">
    <span class="evi-seq">#${r.seq}</span>
    <span class="tag ${esc(r.type)}">${esc(r.type_zh || TYPE_ZH[r.type] || r.type)}</span>
    <div class="evi-main">
      <div class="evi-title">${esc(r.title)}</div>
      <div class="evi-meta">${esc(r.actor || "")} · ${esc(fmtTime(r.time))} · 产物×${r.artifact_count}</div>
      <div class="evi-hash" title="点击复制完整哈希"
        onclick="event.stopPropagation();copyText('${esc(r.hash || "")}',event)">
        ${esc(shortHash(r.hash))}</div>
    </div>
  </div>`;
}

function renderLiveMini() {
  const o = state.overview;
  const box = $("#liveMini");
  const live = o.live?.run;
  if (o.live?.status === "running" && live) {
    const total = live.total_steps || live.config?.steps || 0;
    const steps = live.steps || 0;
    const p = total ? Math.min(100, steps / total * 100) : 0;
    const m = live.metrics || {};
    box.innerHTML = `
      <div class="live-head">
        <span class="pill live"><span class="dot"></span>${esc(stageZh(live.stage))}</span>
        <span class="live-title">${esc(live.title || live.run_id)}</span>
      </div>
      <div class="muted mono">${esc(live.run_id)} · 更新于 ${esc(relTime(live.updated_at))}</div>
      <div class="pbar"><i style="width:${p}%"></i></div>
      <div class="row spread"><span class="muted mono">步数 ${fmtNum(steps)}${total ? " / " + fmtNum(total) : ""}</span>
      <span class="mono" style="color:var(--cyan2)">${fmtNum(p)}%</span></div>
      <div class="live-stats">
        <div class="lstat"><div class="lv">${m.success_rate === undefined || m.success_rate === null ? "--" : fmtNum(m.success_rate * (m.success_rate <= 1 ? 100 : 1)) + "%"}</div><div class="lk">成功率</div></div>
        <div class="lstat"><div class="lv">${m.steps_per_second ? fmtNum(m.steps_per_second, 0) : "--"}</div><div class="lk">steps/s</div></div>
        <div class="lstat"><div class="lv">${m.mean_alt_err_m === undefined || m.mean_alt_err_m === null ? "--" : fmtNum(m.mean_alt_err_m) + "m"}</div><div class="lk">高度误差</div></div>
        <div class="lstat"><div class="lv">${m.crash_rate === undefined || m.crash_rate === null ? "--" : fmtNum((m.crash_rate || 0) * 100) + "%"}</div><div class="lk">坠毁率</div></div>
      </div>`;
  } else {
    const lastEval = state.overview.latest_evidence?.find((e) => e.type === "evaluation");
    box.innerHTML = `
      <div class="live-head">
        <span class="pill"><span class="dot"></span>空闲</span>
        <span class="muted">当前没有正在进行的训练</span>
      </div>
      ${lastEval ? `<div class="muted" style="margin-top:6px">最近一次评估：
        <b style="color:var(--ok)">${esc(lastEval.title)}</b>
        （${esc(relTime(lastEval.time))}）
        <a href="#" onclick="showEvidence('${esc(lastEval.id)}');return false" style="color:var(--cyan)">查看证据</a></div>`
      : ""}
      <div class="muted section-gap">启动训练：
        <code class="inline">python train_ppo_evidence.py --steps 400000</code></div>`;
  }
}

function stageZh(s) {
  return { started: "已启动", training: "训练中", evaluating: "评估中",
    finished: "已完成", failed: "失败" }[s] || s || "";
}

/* ---------------- 证据链页 ---------------- */

async function runVerify() {
  const btn = $("#verifyBtn");
  btn.disabled = true; btn.textContent = "校验中…";
  try {
    const v = await api("/api/verify");
    renderVerifyBanner(v);
    // 同步顶栏
    state.overview.chain_ok = v.ok;
    renderDashboard();
  } catch (e) {
    $("#verifyBanner").innerHTML = `<div class="chain-banner bad">
      <span class="big">校验失败</span><span class="muted">${esc(e.message)}</span></div>`;
  }
  btn.disabled = false; btn.textContent = "⟳ 校验整条链";
}

function renderVerifyBanner(v) {
  const art = v.artifact_stats || {};
  const cls = v.ok ? "ok" : "bad";
  const head = v.ok
    ? `<span class="big" style="color:var(--ok)">✓ 链完整性校验通过</span>
       <span class="muted">${v.total} 条证据 · 产物校验 ${art.checked || 0} 个
       （缺失 ${art.missing || 0} · 篡改 ${art.mismatch || 0}）</span>
       <span class="muted mono">链头 ${esc(shortHash(v.head_hash, 16))}</span>`
    : `<span class="big" style="color:var(--bad)">✕ 链完整性异常</span>
       <span class="muted">在 #${v.broken_at} 附近发现问题</span>`;
  const probs = (v.problems || []).map((p) => `<div class="muted" style="color:#fca5a5">· ${esc(p)}</div>`).join("");
  $("#verifyBanner").innerHTML = `<div class="chain-banner ${cls}">${head}${probs}
    <span class="muted mono" style="margin-left:auto">校验于 ${esc(fmtTime(v.verified_at))}</span></div>`;
}

function renderChain() {
  const list = state.evidence;
  if (!list.length) {
    $("#chainList").innerHTML = `<div class="empty">证据链为空</div>`;
    return;
  }
  $("#chainList").innerHTML = list.slice().reverse().map((r) => `
    <div class="chain-block">
      <div class="cb-head">
        <span class="evi-seq" style="width:auto">#${r.seq}</span>
        <span class="tag ${esc(r.type)}">${esc(r.type_zh)}</span>
        <b class="evi-title" style="cursor:pointer" onclick="showEvidence('${esc(r.id)}')">${esc(r.title)}</b>
        <span class="muted mono">${esc(r.id)}</span>
        <span class="muted" style="margin-left:auto">${esc(fmtTime(r.time))}</span>
      </div>
      <div class="chain-hash" style="margin:3px 0 0 44px">
        hash ${esc(r.hash?.slice(0, 24))}… ← prev ${esc(r.prev_hash?.slice(0, 16))}…
        ${r.prev_hash === "0000000000000000000000000000000000000000000000000000000000000000" ?
          '<span style="color:var(--warn)">（创世块）</span>' : ""}
      </div>
    </div>`).join("");
}

/* ---------------- 记录页 ---------------- */

async function loadEvidence() {
  const data = await api("/api/evidence");
  state.evidence = data.records || [];
  state.evidenceLoaded = true;
  renderChain();
  renderRecords();
}

function renderRecords() {
  const types = ["all", ...Object.keys(TYPE_ZH)];
  const counts = {};
  state.evidence.forEach((r) => counts[r.type] = (counts[r.type] || 0) + 1);
  $("#recFilters").innerHTML = types.map((t) => {
    const n = t === "all" ? state.evidence.length : (counts[t] || 0);
    return `<button class="fbtn ${state.recFilter === t ? "active" : ""}"
      onclick="setFilter('${t}')">${t === "all" ? "全部" : esc(TYPE_ZH[t])} <span class="mono">${n}</span></button>`;
  }).join("") + `<span style="flex:1"></span>`;

  const kw = state.recSearch.trim().toLowerCase();
  const rows = state.evidence.filter((r) =>
    (state.recFilter === "all" || r.type === state.recFilter) &&
    (!kw || (r.title + r.id + (r.actor || "")).toLowerCase().includes(kw)));

  $("#recBody").innerHTML = rows.map((r) => `
    <tr class="click" onclick="showEvidence('${esc(r.id)}')">
      <td class="mono muted">${r.seq}</td>
      <td><span class="tag ${esc(r.type)}">${esc(r.type_zh)}</span></td>
      <td><b>${esc(r.title)}</b></td>
      <td class="mono muted">${esc(r.actor || "-")}</td>
      <td class="mono muted">${esc(fmtTime(r.time))}</td>
      <td class="mono">${r.artifact_count || "-"}</td>
      <td><span class="hash-txt" title="点击复制" onclick="event.stopPropagation();copyText('${esc(r.hash)}',event)">${esc(shortHash(r.hash, 12))}</span></td>
    </tr>`).join("");
  $("#recEmpty").style.display = rows.length ? "none" : "block";
}

function setFilter(t) { state.recFilter = t; renderRecords(); }

/* ---------------- 指标验证页 ---------------- */

function renderMetricsTab() {
  const o = state.overview;
  if (!o) return;
  $("#metricBody").innerHTML = o.metrics.map((m) => {
    const cls = m.status === "pass" ? "pass" : m.status === "fail" ? "fail" : "pending";
    const dirTxt = m.dir === ">=" ? "≥" : "≤";
    const unit = m.display === "percent" ? "%" : esc(m.unit);
    const measured = m.measured === null || m.measured === undefined
      ? `<span class="muted">待验证</span>`
      : `<b class="mono" style="font-size:16px">${fmtNum(m.measured)}${unit}</b>`;
    const status = m.status === "pass"
      ? `<span class="pill ok"><span class="dot"></span>达标</span>`
      : m.status === "fail"
        ? `<span class="pill bad"><span class="dot"></span>未达标</span>`
        : `<span class="pill"><span class="dot"></span>待验证</span>`;
    const evCell = m.evidence_id
      ? `<a href="#" onclick="showEvidence('${esc(m.evidence_id)}');return false" style="color:var(--cyan)" class="mono">${esc(m.evidence_id)}</a>`
      : `<span class="muted">—</span>`;
    const scope = [m.scope, m.basis].filter(Boolean).map(esc).join(" · ");
    return `<tr class="mrow-${cls}">
      <td><b>${esc(m.name)}</b> <span class="kid mono muted">${esc(m.id)}</span><br>
          <span class="muted" style="font-size:11px">${esc(m.desc || "")}</span></td>
      <td class="mono">${dirTxt}${fmtNum(m.threshold)}${unit}</td>
      <td>${measured}</td>
      <td>${status}</td>
      <td>${scope ? `<span class="scope-note">${scope}</span>` : `<span class="muted">—</span>`}</td>
      <td>${evCell}</td>
    </tr>`;
  }).join("");

  // 里程碑产出物
  const plan = [
    { key: "T1", name: "动力学和运动控制模型搭建 + 数据管理软件设计", items: [
      { t: "FastHeloEnv 训练环境（简化动力学, 50Hz, 修好地面效应/推力标定）", done: true },
      { t: "PPO 训练管线（SB3, 8 并行, CPU）+ 启发式基准", done: true },
      { t: "证据链数据管理系统（本平台）", done: true },
      { t: "Bo-105 全量动力学模型 / 系统辨识", done: false },
    ]},
    { key: "T2", name: "软件在环仿真环境搭建, 全流程研发能力", items: [
      { t: "无头快速仿真 + RL 全流程（已通）", done: true },
      { t: "FlightGear 软件在环可视化回路", done: false },
      { t: "座舱三维资产库 + 机械臂操纵映射训练（arm 模式已冒烟）", done: false },
    ]},
    { key: "T3", name: "合同验收", items: [
      { t: "六项技术指标全部达标验证", done: false },
      { t: "验收材料与证据链打包交付", done: false },
    ]},
  ];
  $("#milestoneOut").innerHTML = plan.map((p) => `
    <div style="margin-bottom:14px">
      <div class="row" style="margin-bottom:6px">
        <span class="tag milestone">${esc(p.key)}</span>
        <b>${esc(p.name)}</b>
      </div>
      ${p.items.map((i) => `<div class="row" style="padding:3px 0 3px 14px;font-size:13px">
        <span style="color:${i.done ? "var(--ok)" : "var(--txt3)"}">${i.done ? "✓" : "○"}</span>
        <span style="color:${i.done ? "var(--txt)" : "var(--txt2)"}">${esc(i.t)}</span>
      </div>`).join("")}
    </div>`).join("");
}

/* ---------------- 训练实况页 ---------------- */

function renderLiveTab() {
  const o = state.overview;
  if (!o) return;
  const big = $("#liveBig");
  const live = o.live?.run;
  if (o.live?.status === "running" && live) {
    const total = live.total_steps || live.config?.steps || 0;
    const p = total ? Math.min(100, (live.steps || 0) / total * 100) : 0;
    const m = live.metrics || {};
    const cfg = live.config || {};
    big.innerHTML = `
      <div class="live-head">
        <span class="pill live"><span class="dot"></span>${esc(stageZh(live.stage))}</span>
        <span class="live-title">${esc(live.title || live.run_id)}</span>
      </div>
      <div class="muted mono">${esc(live.run_id)} · 启动 ${esc(fmtTime(live.started_at))} · 更新 ${esc(relTime(live.updated_at))}</div>
      <div style="margin:8px 0">
        ${cfg.steps ? `<span class="chip">目标 ${fmtNum(cfg.steps)} 步</span>` : ""}
        ${cfg.envs ? `<span class="chip">${cfg.envs} 并行环境</span>` : ""}
        ${cfg.mode ? `<span class="chip">${esc(cfg.mode)} 模式</span>` : ""}
        ${cfg.algorithm ? `<span class="chip">${esc(cfg.algorithm)}</span>` : ""}
      </div>
      <div class="pbar"><i style="width:${p}%"></i></div>
      <div class="row spread"><span class="muted mono">${fmtNum(live.steps || 0)} / ${fmtNum(total)} 步</span>
        <span class="mono" style="color:var(--cyan2)">${fmtNum(p)}%</span></div>
      <div class="live-stats" style="grid-template-columns:repeat(4,1fr)">
        <div class="lstat"><div class="lv">${pct(m.success_rate)}</div><div class="lk">成功率</div></div>
        <div class="lstat"><div class="lv">${m.steps_per_second ? fmtNum(m.steps_per_second, 0) : "--"}</div><div class="lk">steps/s</div></div>
        <div class="lstat"><div class="lv">${m.mean_alt_err_m ?? "--"}</div><div class="lk">高度误差 m</div></div>
        <div class="lstat"><div class="lv">${pct(m.crash_rate)}</div><div class="lk">坠毁率</div></div>
      </div>
      <div class="muted section-gap">实时数据文件：evidence/live/current_run.json（训练脚本每 5 万步更新）</div>`;
    lineChart($("#liveChart"), live.history || []);
  } else {
    big.innerHTML = `
      <div class="live-head">
        <span class="pill"><span class="dot"></span>空闲</span>
        <span class="muted">当前没有正在进行的训练</span>
      </div>
      <div class="muted section-gap">要开始一次可追溯的训练，运行：</div>
      <pre class="payload" style="margin-top:6px">cd 
python train_ppo_evidence.py --steps 400000</pre>
      <div class="muted section-gap">训练启动后：实时状态写入 evidence/live/，本页与看板自动刷新；
      结束后自动追加"训练运行 + 最终评估"两条证据并计算模型文件 SHA-256。</div>`;
    const latest = o.success_curve || [];
    lineChart($("#liveChart"), latest.length ? latest : []);
  }

  // 历史运行表：训练运行 + 评估记录合并
  const runs = state.evidence
    .filter((r) => ["training_run", "evaluation"].includes(r.type))
    .sort((a, b) => b.seq - a.seq);
  $("#runsBody").innerHTML = runs.map((r) => {
    const p = r.payload || {};
    const sr = p.success_rate;
    const cr = p.crash_rate;
    return `<tr class="click" onclick="showEvidence('${esc(r.id)}')">
      <td class="mono muted">${esc(fmtTime(r.time))}</td>
      <td><span class="tag ${esc(r.type)}">${esc(r.type_zh)}</span></td>
      <td class="mono">${p.steps ? fmtNum(p.steps, 0) : "-"}</td>
      <td class="mono">${p.steps_per_second ? fmtNum(p.steps_per_second, 0) : "-"}</td>
      <td class="mono" style="color:${sr === undefined ? "inherit" : sr >= 0.85 ? "var(--ok)" : "var(--warn)"}">${sr === undefined ? "-" : pct(sr)}</td>
      <td class="mono">${cr === undefined ? "-" : pct(cr)}</td>
      <td class="mono">${p.mean_alt_err_m ?? "-"}${p.mean_alt_err_m ? " m" : ""}</td>
      <td class="mono muted">${esc(r.id)}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="8"><div class="empty">暂无训练记录</div></td></tr>`;
}

function pct(v) {
  if (v === null || v === undefined) return "--";
  return fmtNum(v <= 1 ? v * 100 : v) + "%";
}

/* ---------------- 模态框 ---------------- */

function openModal(html) {
  $("#modalContent").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function closeModal() { $("#modal").classList.add("hidden"); }

async function showEvidence(id) {
  try {
    const r = await api(`/api/evidence/${id}`);
    const arts = (r.artifacts || []).map((a) => `
      <div class="art-item">
        <span class="tag note">${esc(a.name)}</span>
        <span class="mono muted">${esc(a.path)}</span>
        ${a.sha256
          ? `<span class="hash-txt" title="点击复制完整 SHA-256"
              onclick="copyText('${esc(a.sha256)}',event)"
              style="margin-left:auto">sha256 ${esc(a.sha256.slice(0, 20))}…</span>`
          : `<span class="muted" style="color:var(--bad)">记录时文件不存在</span>`}
      </div>`).join("") || `<div class="muted">无产物</div>`;
    openModal(`
      <h2>#${r.seq} ${esc(r.title)}</h2>
      <div class="row" style="margin:4px 0 8px">
        <span class="tag ${esc(r.type)}">${esc(TYPE_ZH[r.type] || r.type)}</span>
        <span class="mono muted">${esc(r.id)}</span>
        <span class="muted">发起者 ${esc(r.actor || "-")}</span>
        <span class="muted">${esc(fmtTime(r.time))}</span>
      </div>
      <dl class="kv">
        <dt>本条哈希</dt><dd><span class="hash-txt" onclick="copyText('${esc(r.hash)}',event)">${esc(r.hash)}</span></dd>
        <dt>前置哈希</dt><dd><span class="hash-txt" onclick="copyText('${esc(r.prev_hash)}',event)">${esc(r.prev_hash)}</span></dd>
      </dl>
      <h3 style="margin:10px 0 6px;font-size:13px;color:var(--txt2)">记录内容 (payload)</h3>
      <pre class="payload">${esc(JSON.stringify(r.payload, null, 2))}</pre>
      <h3 style="margin:12px 0 4px;font-size:13px;color:var(--txt2)">产物固证（SHA-256）</h3>
      ${arts}`);
  } catch (e) {
    openModal(`<h2>加载失败</h2><div class="muted">${esc(e.message)}</div>`);
  }
}

function showAddForm() {
  openModal(`
    <h2>新增记录</h2>
    <div class="muted" style="margin:4px 0 14px">记录将立即追加进哈希证据链并展示在平台各处。</div>
    <div class="form-grid">
      <div><label>记录类型</label>
        <select id="fType">${Object.entries(TYPE_ZH).map(([k, v]) =>
          `<option value="${k}">${v}</option>`).join("")}</select></div>
      <div><label>发起者（可留空）</label><input id="fActor" placeholder="如：质检组 / 会议"></div>
      <div class="form-full"><label>标题 *</label><input id="fTitle" placeholder="一句话说明本条记录"></div>
      <div class="form-full"><label>说明内容</label><textarea id="fPayload" rows="3"
        placeholder='自由文本；或 JSON，如 {"metric_id":"K3","measured":0.96,"scope":"首轮语义理解评测"}'></textarea></div>
      <div class="form-full form-msg" id="fMsg"></div>
      <div class="form-full row" style="justify-content:flex-end">
        <button class="btn ghost" onclick="closeModal()">取消</button>
        <button class="btn" id="fSubmit">上链保存</button>
      </div>
    </div>`);
  $("#fSubmit").onclick = submitRecord;
}

async function submitRecord() {
  const msg = $("#fMsg");
  const title = $("#fTitle").value.trim();
  let payloadRaw = $("#fPayload").value.trim();
  let payload = {};
  if (payloadRaw) {
    try {
      payload = JSON.parse(payloadRaw);
      if (typeof payload === "string") payload = { note: payload };
    } catch (e) {
      payload = { note: payloadRaw };  // 非 JSON 一律作为文本说明
    }
  }
  if (!title) { msg.className = "form-msg bad"; msg.textContent = "标题不能为空"; return; }
  try {
    await api("/api/evidence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: $("#fType").value, title, payload,
        actor: $("#fActor").value.trim() || undefined }),
    });
    msg.className = "form-msg ok";
    msg.textContent = "✓ 已上链";
    await loadEvidence();
    setTimeout(() => { closeModal(); refreshAll(); }, 600);
  } catch (e) {
    msg.className = "form-msg bad";
    msg.textContent = "失败：" + e.message;
  }
}

/* ---------------- 数据加载与轮询 ---------------- */

async function refreshAll() {
  try {
    const o = await api("/api/overview");
    state.overview = o;
    renderDashboard();
    renderMetricsTab();
    if (!$("#tab-live").classList.contains("active")) return;
    renderLiveTab();
  } catch (e) {
    $("#chainPill").className = "pill bad";
    $("#chainPill").innerHTML = `<span class="dot"></span>后端离线`;
  }
}

async function pollLoop() {
  await refreshAll();
  setInterval(async () => {
    await refreshAll();
    if (state.evidenceLoaded &&
        (document.querySelector("#tab-chain")?.classList.contains("active") ||
         document.querySelector("#tab-records")?.classList.contains("active"))) {
      loadEvidence().catch(() => {});
    }
  }, 5000);
}

/* ---------------- 启动 ---------------- */

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("#verifyBtn").addEventListener("click", runVerify);
  $("#recSearch").addEventListener("input", (e) => {
    state.recSearch = e.target.value; renderRecords();
  });
  $("#addRecBtn").addEventListener("click", showAddForm);
  $("#modal").addEventListener("click", (e) => {
    if (e.target.id === "modal") closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
  pollLoop();
});
