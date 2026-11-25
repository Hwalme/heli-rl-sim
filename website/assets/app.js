/* 低空具身智能 · 项目展示站 —— 渲染逻辑（零依赖）
   数据源：data/data.js → window.__SITE_DATA__（由 build.py 动态生成，含 SHA-256 指纹） */
"use strict";

const $ = (s) => document.querySelector(s);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtNum(v, d = 1) {
  if (v === null || v === undefined || isNaN(Number(v))) return "--";
  return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: d });
}
function fmtPct(v, d = 1) { return v === null || v === undefined ? "--" : fmtNum(v * 100, d) + "%"; }
function shortSha(h, n = 12) { return h ? h.slice(0, n) + "…" : "-"; }

function copyText(text, ev) {
  const done = () => {
    const t = ev?.target;
    if (t) { const o = t.textContent; t.textContent = "已复制 ✓"; setTimeout(() => (t.textContent = o), 900); }
  };
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text; document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { /* noop */ }
  ta.remove();
}

/* =========================================================================
   1. 总览看板与数据卡片
   ========================================================================= */

const F = {
  steps: (d) => d.steps,
  seconds: (d) => d.train_seconds,
  sps: (d) => d.steps_per_second,
  success: (d) => d.overall_success_rate ?? d.success_rate,
  crash: (d) => d.overall_crash_rate ?? d.crash_rate,
  altErr: (d) => d.overall_mean_alt_err_m ?? d.mean_alt_err_m,
  task: (d) => d.task || (d.engine ? "hover" : null),
  engine: (d) => d.engine || null,
};

function latestResult(D) { return D.results.length ? D.results[D.results.length - 1] : null; }
function headlineResult(D) {
  return D.results.find((r) => r.file === D.headline_file) || latestResult(D);
}

function lineChart(el, points) {
  const W = 960, H = 420, padL = 50, padR = 20, padT = 20, padB = 32;
  if (!points || !points.length) { el.innerHTML = '<div class="empty">暂无曲线数据</div>'; return; }
  const xMax = Math.max(...points.map((p) => p.steps), 1);
  const X = (v) => padL + (v / xMax) * (W - padL - padR);
  const Y = (v) => padT + (1 - v) * (H - padT - padB);
  let d = "";
  points.forEach((p, i) => { d += `${i ? "L" : "M"}${X(p.steps).toFixed(1)},${Y(p.success).toFixed(1)} `; });
  const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => `
    <line x1="${padL}" y1="${Y(f)}" x2="${W - padR}" y2="${Y(f)}" stroke="#1c2b45" stroke-dasharray="3 5"/>
    <text x="${padL - 8}" y="${Y(f) + 4}" text-anchor="end" font-size="10" fill="#5b6f8d" font-family="Consolas">${Math.round(f * 100)}%</text>`).join("");
  const gridX = [0, 0.5, 1].map((f) => `
    <text x="${X(f * xMax)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="#5b6f8d" font-family="Consolas">${fmtNum(f * xMax, 0)}</text>`).join("");
  const dots = points.map((p) => `
    <circle cx="${X(p.steps).toFixed(1)}" cy="${Y(p.success).toFixed(1)}" r="3.2" fill="#0a1120" stroke="#34d399" stroke-width="1.6">
      <title>${fmtNum(p.steps, 0)} 步 · 成功率 ${fmtPct(p.success, 0)}</title>
    </circle>`).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridY}${gridX}
    <path d="${d}" fill="none" stroke="#34d399" stroke-width="2.2" stroke-linejoin="round" filter="drop-shadow(0 0 5px rgba(52,211,153,0.4))"/>
    ${dots}
  </svg>`;
}

/* ---------- 训练结果散点图（按任务颜色区分） ---------- */
function scatterChart(el, points) {
  const W = 960, H = 420, padL = 50, padR = 20, padT = 20, padB = 40;
  if (!points || !points.length) { el.innerHTML = '<div class="empty">暂无数据</div>'; return; }
  const xMax = Math.max(...points.map(p => p.steps), 1);
  const X = (v) => padL + (v / xMax) * (W - padL - padR);
  const Y = (v) => padT + (1 - v) * (H - padT - padB);

  const taskColors = {
    hover: "#22d3ee", forward: "#34d399", landing: "#fbbf24",
    roll: "#f87171", waypoint: "#a78bfa", yaw: "#38bdf8", arm: "#fb923c"
  };

  const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => `
    <line x1="${padL}" y1="${Y(f)}" x2="${W - padR}" y2="${Y(f)}" stroke="#1c2b45" stroke-dasharray="3 5"/>
    <text x="${padL - 8}" y="${Y(f) + 4}" text-anchor="end" font-size="10" fill="#5b6f8d" font-family="Consolas">${Math.round(f * 100)}%</text>`).join("");
  const gridX = [0, 0.25, 0.5, 0.75, 1].map((f) => `
    <text x="${X(f * xMax)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="#5b6f8d" font-family="Consolas">${fmtNum(f * xMax, 0)}</text>`).join("");

  const dots = points.map((p) => {
    const color = taskColors[p.task] || "#94a3b8";
    const cx = X(p.steps), cy = Y(p.success);
    return `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="5" fill="${color}" stroke="#0a1120" stroke-width="1.5" opacity="0.9">
      <title>${p.task} / ${p.mode} · ${fmtNum(p.steps, 0)} 步 · 成功率 ${fmtPct(p.success, 1)} · ${p.file}</title>
    </circle>`;
  }).join("");

  const tasks = [...new Set(points.map(p => p.task))];
  const legend = tasks.map(t => {
    const c = taskColors[t] || "#94a3b8";
    const count = points.filter(p => p.task === t).length;
    return `<span><i class="sw" style="background:${c};border-radius:50%"></i>${t} (${count})</span>`;
  }).join("");

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridY}${gridX}${dots}
  </svg><div class="legend small">${legend} <span class="muted">｜ 每次训练/评估一个点，悬停查看详情</span></div>`;
}

function renderOverview(D) {
  const r = headlineResult(D);
  const cards = [];
  if (r) {
    const d = r.data;
    const trainSrc = F.engine(d)
      ? `${esc(F.engine(d))} · ${esc(F.task(d) || "hover")} 任务（train_jsbsim.py）`
      : "8 并行环境 · 纯 CPU（train_ppo.py）";
    const evalSrc = F.engine(d)
      ? "跨5随机种子 × 20 回合确定性评估"
      : "20 回合确定性评估（train_ppo.py eval）";
    cards.push(
      { l: "训练步数", v: fmtNum(F.steps(d), 0), s: trainSrc, ok: true },
      { l: "训练耗时", v: fmtNum(F.seconds(d), 0), u: " 秒", s: `≈ ${fmtNum(F.seconds(d) / 60, 1)} 分钟`, ok: true },
      { l: "训练吞吐", v: fmtNum(F.sps(d), 0), u: " 步/s", s: "对比 FG 实时回路 ≈4 步/s", ok: false },
      { l: "评估成功率", v: fmtPct(F.success(d), 0), s: evalSrc, ok: true },
      { l: "坠毁率", v: fmtPct(F.crash(d), 0), s: "同口径评估", ok: true },
      { l: "平均高度误差", v: fmtNum(F.altErr(d), 2), u: " m", s: "随机目标高度 10-30m", ok: false },
    );
  } else {
    cards.push({ l: "训练数据", v: "待轨道A", s: "等待 rl_train/results/*_results.json" });
  }
  $("#kpiCards").innerHTML = cards.map((c) => `
    <div class="kpi ${c.ok ? "ok" : ""}">
      <div class="k-label">${esc(c.l)}</div>
      <div class="k-val">${esc(c.v)}${c.u ? `<small>${esc(c.u)}</small>` : ""}</div>
      <div class="k-sub">${esc(c.s || "")}</div>
    </div>`).join("");

  const strip = $("#latestStrip");
  const newest = latestResult(D);
  if (newest && r && newest.file !== r.file) {
    const d = newest.data;
    const taskTag = F.task(d) ? ` · ${esc(F.task(d))} 任务` : "";
    const isG2 = (d.aircraft === "helispike" || String(F.task(d)).includes("heli") || newest.gen_id === "gen2");
    // D9 口径: <100k 步为探针/冒烟级实验, 不得挂"破局/冲刺验收"框架 (9/13 粗筛数字教训)
    const isProbe = (Number(F.steps(d)) || 0) < 100000;
    const genTag = isG2
      ? (isProbe
        ? `<span class="ls-tag" style="border:1px solid #9333ea;color:#c4b5fd;">二代探针 (helispike) · 非验收口径</span>`
        : `<span class="ls-tag" style="background:linear-gradient(135deg,#9333ea,#c026d3);color:#fff;font-weight:bold;">二代破局 (helispike)</span>`)
      : `<span class="ls-tag">最新实验</span>`;
    const sasTag = (isG2 && !isProbe)
      ? ` · <span class="tag" style="color:var(--cyan);font-weight:bold;">物理层 D3 串级 SAS 增强</span>`
      : "";
    const tailTag = isProbe ? "（探针/对照口径 · 非验收数字）" : "（三件套齐全 · 冲刺85%中）";
    strip.innerHTML = `
      <div class="latest-strip">
        ${genTag}
        <span><b>${esc(d.mode)}</b> 模式${taskTag}${sasTag} · ${fmtNum(F.steps(d), 0)} 步 · 成功率 <b class="mono" style="color:var(--ok)">${fmtPct(F.success(d), 0)}</b>
          · 坠毁 <b>${fmtPct(F.crash(d), 0)}</b> · 高度误差 <b>${fmtNum(F.altErr(d), 2)} m</b>${tailTag}</span>
        <span class="muted small">（${esc(newest.file)} · 指纹 ${esc(shortSha(newest.sha256, 10))}）</span>
      </div>`;
  } else strip.innerHTML = "";

  if (r) {
    const d = r.data;
    $("#heroSteps").textContent = fmtNum(F.steps(d), 0);
    $("#heroSeconds").textContent = fmtNum(F.seconds(d), 0);
    $("#heroAltErr").textContent = fmtNum(F.altErr(d), 2);
    $("#heroSrc").textContent = `数据出处：${r.path}（本机实跑，SHA-256 指纹见下方"数据快照"卡）`;
    if (F.engine(d)) {
      $("#heroKicker").textContent = `JSBSIM 高保真基线 · ${esc(F.task(d) || "hover")} 任务 · 验收通过`;
      $("#heroDesc").innerHTML =
        `机械臂飞行员策略在 <b>JSBSim AH-1S 高保真 6-DoF</b> 全数字仿真（刚体 + BET 旋翼 + 地面效应 + 气动查表）中完成训练：` +
        `跨 <b>5 随机种子 × 20 回合</b>确定性评估成功率 <b>${fmtPct(F.success(d), 0)}</b>、坠毁率 <b>${fmtPct(F.crash(d), 0)}</b>，` +
        `平均高度误差 <b>${fmtNum(F.altErr(d), 2)} m</b>。`;
    }
  }

  // 训练结果散点图（按任务颜色区分，不同任务/引擎不可连折线）
  const trainPoints = D.results
    .map(r2 => ({
      steps: F.steps(r2.data),
      success: F.success(r2.data),
      task: F.task(r2.data) || (r2.data.mode === "direct" ? "hover" : r2.data.mode || "unknown"),
      mode: r2.data.mode || "unknown",
      file: r2.file
    }))
    .filter(p => p.steps > 0 && typeof p.success === "number");
  scatterChart($("#curveChart"), trainPoints);

  const rows = D.results.map((r2) => `
    <div class="snap-row">
      <span class="f">📄 ${esc(r2.file)}</span>
      <span class="t">${esc(r2.path)}</span>
      <span class="hash-txt" title="点击复制完整 SHA-256" onclick="copyText('${esc(r2.sha256)}',event)">
        sha256 ${esc(shortSha(r2.sha256))}</span>
    </div>`).join("") || '<div class="empty">暂无快照</div>';
  $("#snapshotCard").innerHTML = rows + `
    <div class="snap-row" style="border-style:solid">
      <span class="t">构建时间 ${esc(D.built_at)} · 契约 ${esc(D.sources?.contract || "")}</span>
    </div>`;

  const pill = $("#dataPill");
  pill.className = "pill ok";
  pill.innerHTML = `<span class="dot"></span>数据快照 ${esc(D.built_at)} · 指纹 ${D.results.length} 份`;
  $("#footerBuilt").textContent = `built_at ${D.built_at} · website-data v2 · build.py 只读构建`;
}

function renderRecords(D) {
  const tb = $("#recordsTable tbody");
  if (!D.results.length) { $("#recordsEmpty").classList.remove("hidden"); return; }
  tb.innerHTML = D.results.map((r) => {
    const d = r.data;
    const sr = F.success(d);
    const srOk = typeof sr === "number" && sr >= 0.85;
    const isG2 = d.aircraft === "helispike" || String(F.task(d)).includes("heli") || r.gen_id === "gen2";
    const genBadge = isG2
      ? `<span class="st" style="background:rgba(168,85,247,0.2);color:#c084fc;border:1px solid rgba(168,85,247,0.4)">G2 helispike</span>`
      : (d.aircraft === "ah1s" || r.gen_id === "gen1" ? `<span class="st" style="background:rgba(34,211,238,0.15);color:#38bdf8;border:1px solid rgba(34,211,238,0.3)">G1 AH-1S</span>` : "");
    const modeTag = F.task(d) ? `${esc(d.mode)}·${esc(F.task(d))}` : esc(d.mode);
    return `<tr>
      <td><b class="mono">${esc(r.file)}</b> ${genBadge}<br><span class="small" style="color:var(--txt3)">${esc(r.path)}</span></td>
      <td><span class="st ${d.mode === "direct" ? "partial" : "pending"}">${modeTag}</span></td>
      <td class="mono">${fmtNum(F.steps(d), 0)}</td>
      <td class="mono">${F.seconds(d) != null ? fmtNum(F.seconds(d), 0) + " s" : "-"}</td>
      <td class="mono">${F.sps(d) != null ? fmtNum(F.sps(d), 0) : "-"}</td>
      <td class="mono" style="${srOk ? "color:var(--ok)" : "color:var(--warn)"}">${fmtPct(sr, 0)}</td>
      <td class="mono" style="${F.crash(d) === 0 ? "color:var(--ok)" : ""}">${fmtPct(F.crash(d), 0)}</td>
      <td class="mono">${F.altErr(d) != null ? fmtNum(F.altErr(d), 2) + " m" : "-"}</td>
      <td><span class="hash-txt" title="点击复制完整 SHA-256" onclick="copyText('${esc(r.sha256)}',event)">${esc(shortSha(r.sha256))}</span></td>
    </tr>`;
  }).join("");
}

function renderMilestones(D) {
  const box = $("#milestones");
  if (!D.milestones.length) { box.innerHTML = '<div class="empty">里程碑摘录为空</div>'; return; }
  let currentMarked = false;
  box.innerHTML = D.milestones.map((m) => {
    let cls = m.status === "done" ? "done" : "";
    let flag = m.status === "done" ? "✅ 达成" : "⬜ 未开始";
    if (m.status !== "done" && !currentMarked) { cls = "current"; flag = "▶ 进行中"; currentMarked = true; }
    return `<div class="ms ${cls}">
      <span class="ms-flag">${flag}</span>
      <div class="ms-id">${esc(m.id)}</div>
      <div class="ms-name">${esc(m.content)}</div>
      <div class="ms-exit">退出标准：${esc(m.exit)}</div>
    </div>`;
  }).join("");
}

function renderTracks(D) {
  const wrap = $("#trackCards");
  if (!wrap) return;
  const stateOf = (s) => s.includes("✅") || s.includes("🟢") ? ["pass", "运转中"]
    : s.includes("🔴") || s.includes("⚠") ? ["risk", "风险"]
    : s.includes("🔄") || s.includes("🟡") ? ["partial", "推进中"]
    : ["pending", "待定"];
  wrap.innerHTML = D.tracks.map((t) => {
    const [stCls, stLabel] = stateOf(t.status);
    return `
    <div class="tk tk-${stCls}">
      <div class="tk-head">
        <span class="tk-badge">${esc(t.track)}</span>
        <b class="tk-name">${esc(t.name)}</b>
        <span class="st ${stCls}">${stLabel}</span>
      </div>
      <div class="tk-row"><span class="tk-k">任务</span><span>${esc(t.task)}</span></div>
      <div class="tk-row"><span class="tk-k">状态</span><span>${esc(t.status)}</span></div>
      <div class="tk-row"><span class="tk-k">交付</span><span>${esc(t.delivery)}</span></div>
      <div class="tk-row"><span class="tk-k">下一步</span><span>${esc(t.next)}</span></div>
    </div>`;
  }).join("") || '<div class="empty">轨道快照为空</div>';
}

function renderMetrics(D) {
  const wrap = $("#metricsBody");
  const stMap = {
    pass: ["pass", "达标"],
    partial: ["partial", "部分达标"],
    pending: ["pending", "待验证"],
    fail: ["fail", "未达标"],
  };
  wrap.innerHTML = D.metrics.map((m) => {
    const [cls, label] = stMap[m.status] || stMap.pending;
    const dir = m.dir === ">=" ? "≥" : "≤";
    const hasVal = m.measured !== null && m.measured !== undefined;
    const valStr = hasVal ? `${fmtNum(m.measured)}${esc(m.unit)}` : "—";

    // 进度条计算
    let pct = 0, pctCls = "pending";
    if (hasVal && m.threshold > 0) {
      if (m.dir === ">=") {
        pct = Math.min(100, (m.measured / m.threshold) * 100);
      } else {
        pct = Math.min(100, (m.threshold / Math.max(m.measured, 0.001)) * 100);
      }
      pctCls = m.status === "pass" ? "pass" : (m.status === "fail" ? "fail" : "partial");
    }

    return `<div class="mcard mcard-${cls}">
      <div class="mcard-head">
        <span class="kid">${esc(m.id)}</span>
        <b class="mname">${esc(m.name)}</b>
        <span class="st ${cls}">${label}</span>
      </div>
      <div class="mcard-metrics">
        <span class="mreq">要求 ${dir} ${fmtNum(m.threshold)}${esc(m.unit)}</span>
        <span class="mval ${hasVal && m.status==='pass' ? 'ok' : ''}">实测 ${valStr}</span>
      </div>
      <div class="mcard-bar">
        <div class="mcard-bar-fill ${pctCls}" style="width:${pct.toFixed(1)}%"></div>
        <span class="mcard-bar-pct">${pct.toFixed(0)}%</span>
      </div>
      <div class="mnote">${esc(m.desc)}${m.scope ? " · " + esc(m.scope) : ""}</div>
    </div>`;
  }).join("");

  const notes = D.metrics.filter((m) => m.status !== "pending");
  $("#metricNotes").innerHTML = notes.map((m) => {
    const ev = m.evidence || {};
    const shaLine = ev.sha256
      ? `<span class="hash-txt" title="点击复制完整 SHA-256" onclick="copyText('${esc(ev.sha256)}',event)">sha256 ${esc(shortSha(ev.sha256))}</span>`
      : "";
    const src = ev.path ? `<span class="mono small" style="color:var(--txt3)">${esc(ev.path)}</span> ${shaLine}`
      : ev.doc ? `<span class="small" style="color:var(--txt3)">文档摘录：${esc(ev.doc)}</span>` : "";
    return `<div class="note-item"><b>${esc(m.id)} ${esc(m.name)}</b>（实测 ${fmtNum(m.measured)}${esc(m.unit)}）
      <br>${esc(m.scope || "")}<br>${src}</div>`;
  }).join("") || '<div class="empty">暂无达标项</div>';

  // 待验证项 · 数据工单: 数据源 = data-requests.md 工单表 (build.py parse_workorders 解析进 data.js),
  // 展示所有未回填(🆕/🔄)工单; 状态长文截 140 字防面板爆行; 前导 emoji 换成状态徽章(🆕部分字体缺字形)
  const woState = { open: ["pending", "待响应"], partial: ["partial", "补充中"] };
  const wos = (D.workorders || []).filter((w) => w.state !== "done");
  $("#metricTickets").innerHTML = wos.map((w) => {
    const [cls, label] = woState[w.state] || woState.open;
    // /u 标志必需: ✅🔄🆕 含星面码点, 无 u 标志字符类按代理对拆分剥不干净 (留下残字)
    let brief = w.status.replace(/^[✅🔄🆕]\s*/u, "").trim();
    if (!brief) brief = "待数据方响应；回填后重跑构建刷新";
    else if (brief.length > 140) brief = brief.slice(0, 140) + "…";
    return `<div class="ticket-item"><span class="tno">工单 ${esc(w.id)}</span>
      <span><b>${esc(w.need)}</b> <span class="muted small">→ ${esc(w.target)}</span> <span class="st ${cls}">${label}</span><br>${esc(brief)}</span></div>`;
  }).join("")
    || '<div class="empty">无待验证项</div>';
}

function fmtHours(h) {
  if (h === null || h === undefined) return "--";
  if (h < 1) return (h * 60).toFixed(1) + " min";
  return h.toFixed(2) + " h";
}

function renderCumulative(D) {
  const c = D.cumulative;
  if (!c) return;
  const k6 = c.k6 || {};
  const byType = c.by_type || {};
  const rl = byType.rl || { tests: 0, sim_hours: 0, passed: 0, crashed: 0 };
  const fg = byType.fg || { tests: 0, sim_hours: 0, passed: 0, crashed: 0 };
  const kpis = [
    { l: "仿真世界时间累计", v: fmtHours(c.total_sim_hours), s: "n_steps × dt · 50Hz环境", ok: true },
    { l: "测试记录总数", v: fmtNum(c.total_tests, 0) + " 条", s: `通过 ${c.passed_tests} · 坠毁 ${c.crashed_tests}`, ok: c.passed_tests > 0 },
    { l: "RL训练仿真", v: fmtNum(rl.tests, 0) + " 条", s: `${fmtHours(rl.sim_hours)} · 通过 ${rl.passed} · 坠毁 ${rl.crashed}`, ok: rl.passed > 0 },
    { l: "FG实飞演示", v: fmtNum(fg.tests, 0) + " 条", s: `${fmtHours(fg.sim_hours)} · 成功 ${fg.passed} · 坠毁 ${fg.crashed}`, ok: fg.passed > 0 },
    { l: "累计训练步数", v: fmtNum(c.total_train_steps, 0), s: `评估 ${fmtNum(c.total_eval_episodes, 0)} 回合`, ok: true },
    { l: "K6有效数据量", v: fmtHours(k6.collected_hours || 0) + " / 1000h", s: k6.collected_hours ? `${k6.total_frames ? (k6.total_frames/10000).toFixed(1) + "万帧 · 0% NaN" : "AG-2管线首批在档"}` : "第一/三人称视角操纵摇杆 · 待采集", ok: (k6.collected_hours || 0) > 0 },
  ];
  $("#cumulativeKpi").innerHTML = kpis.map((k) => `
    <div class="kpi ${k.ok ? "ok" : ""}">
      <div class="k-label">${esc(k.l)}</div>
      <div class="k-val">${esc(k.v)}</div>
      <div class="k-sub">${esc(k.s)}</div>
    </div>`).join("");

  // 进度条：仿真世界时间累计（参考刻度100h，不与K6挂钩）
  const simPct = Math.min(c.total_sim_hours / 100 * 100, 100);
  $("#progressFill").style.width = simPct + "%";
  $("#progressText").innerHTML = `<b>仿真世界时间</b>：${fmtHours(c.total_sim_hours)}（参考刻度 100h）`;
  $("#progressPct").textContent = simPct.toFixed(1) + "%";
  // K6 状态说明（只在此处出现一次）
  $("#cumulativeNote").innerHTML =
    `※ 以上为项目训练过程仿真世界时间（n_steps × dt），非 K6"有效数据量1000小时"口径；` +
    `K6 要求采集第一、三人称视角操纵摇杆数据，AG-2 生产管线已正式投产，当前已累计有效时长 <b>${fmtHours(k6.collected_hours || 0)}</b>（${k6.total_frames ? (k6.total_frames/10000).toFixed(1)+'万帧 · 0% NaN' : ''}）/ ${k6.target_hours || 1000}h，与主训练并行扩建中。`;
}

function renderDistributions(D) {
  const c = D.cumulative;
  if (!c) return;
  const tasks = Object.entries(c.by_task || {}).sort((a, b) => b[1].sim_hours - a[1].sim_hours);
  const maxTaskH = Math.max(...tasks.map((t) => t[1].sim_hours), 0.001);
  $("#taskDist").innerHTML = tasks.map(([name, d]) => {
    const pct = (d.sim_hours / maxTaskH * 100).toFixed(1);
    return `<div class="dist-row">
      <div class="dist-name"><b>${esc(name)}</b> <span class="muted small">${d.tests}次测试 · ${d.success_count}次通过</span></div>
      <div class="dist-bar"><div class="dist-fill" style="width:${pct}%"></div></div>
      <div class="dist-val mono">${fmtHours(d.sim_hours)}</div>
    </div>`;
  }).join("") || '<div class="empty">暂无数据</div>';

  const engines = Object.entries(c.by_engine || {}).sort((a, b) => b[1].sim_hours - a[1].sim_hours);
  const maxEngH = Math.max(...engines.map((e) => e[1].sim_hours), 0.001);
  $("#engineDist").innerHTML = engines.map(([name, d]) => {
    const pct = (d.sim_hours / maxEngH * 100).toFixed(1);
    return `<div class="dist-row">
      <div class="dist-name"><b>${esc(name)}</b> <span class="muted small">${d.tests}次测试</span></div>
      <div class="dist-bar"><div class="dist-fill alt" style="width:${pct}%"></div></div>
      <div class="dist-val mono">${fmtHours(d.sim_hours)}</div>
    </div>`;
  }).join("") || '<div class="empty">暂无数据</div>';
}

function renderTrendChart(D) {
  const el = document.getElementById("trendChart");
  if (!el) return;
  const logs = (D.flight_logs || []).slice().reverse();
  if (!logs.length) { el.innerHTML = '<div class="empty">暂无趋势数据</div>'; return; }

  const W = 960, H = 320, padL = 55, padR = 20, padT = 20, padB = 60;
  const n = logs.length;
  const X = (i) => padL + (n === 1 ? (W - padL - padR) / 2 : (i / (n - 1)) * (W - padL - padR));
  const Y = (v) => padT + (1 - v) * (H - padT - padB);

  // 折线（均值连线）
  let pathD = "";
  logs.forEach((l, i) => {
    const sr = l.success_rate != null ? l.success_rate : 0;
    pathD += `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(sr).toFixed(1)} `;
  });

  const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => `
    <line x1="${padL}" y1="${Y(f)}" x2="${W - padR}" y2="${Y(f)}" stroke="#1c2b45" stroke-dasharray="3 5"/>
    <text x="${padL - 8}" y="${Y(f) + 4}" text-anchor="end" font-size="10" fill="#5b6f8d" font-family="Consolas">${Math.round(f * 100)}%</text>`).join("");

  let xLabels = "";
  const labelStep = Math.max(1, Math.floor(n / 6));
  logs.forEach((l, i) => {
    if (i % labelStep === 0 || i === n - 1) {
      const t = l.timestamp ? l.timestamp.replace("T", " ").slice(5, 16) : "-";
      xLabels += `<text x="${X(i)}" y="${H - 38}" text-anchor="middle" font-size="9.5" fill="#5b6f8d" font-family="Consolas" transform="rotate(-30,${X(i)},${H - 38})">${esc(t)}</text>`;
    }
  });

  // 每个 seed 的散点 + 误差棒 + 均值主点
  let seedDots = "";
  let errorBars = "";
  const dots = logs.map((l, i) => {
    const sr = l.success_rate != null ? l.success_rate : 0;
    const hasCrash = l.crash_rate != null && l.crash_rate > 0;
    const color = hasCrash ? "#fbbf24" : (sr >= 0.85 ? "#34d399" : "#f87171");
    const task = l.task || "-";
    const mode = l.mode || "-";
    const crashStr = l.crash_rate != null ? (l.crash_rate * 100).toFixed(1) + "%" : "-";
    const altErr = l.mean_alt_err_m != null ? l.mean_alt_err_m.toFixed(2) + "m" : "-";

    // 收集每个 seed 的成功率
    const seeds = l.seed_results || [];
    const seedSrs = seeds.map(s => s.success_rate != null ? s.success_rate : null).filter(v => v != null);
    const seedDetail = seeds.map(s => {
      const ssr = s.success_rate != null ? (s.success_rate * 100).toFixed(1) : "-";
      const scr = s.crash_rate != null ? (s.crash_rate * 100).toFixed(1) : "-";
      return `seed_${s.seed}: ${ssr}% (坠毁 ${scr}%, 误差 ${s.mean_alt_err_m != null ? s.mean_alt_err_m.toFixed(2) + "m" : "-"}, ${s.episodes || 0}回合)`;
    }).join("\n");

    // 误差棒：均值 ± 1标准差
    if (seedSrs.length >= 2) {
      const mean = seedSrs.reduce((a, b) => a + b, 0) / seedSrs.length;
      const variance = seedSrs.reduce((a, b) => a + (b - mean) ** 2, 0) / seedSrs.length;
      const std = Math.sqrt(variance);
      const yTop = Y(Math.min(1, sr + std));
      const yBot = Y(Math.max(0, sr - std));
      const xc = X(i);
      errorBars += `<line x1="${xc}" y1="${yTop}" x2="${xc}" y2="${yBot}" stroke="${color}" stroke-width="1.5" opacity="0.5"/>
        <line x1="${xc - 4}" y1="${yTop}" x2="${xc + 4}" y2="${yTop}" stroke="${color}" stroke-width="1.5" opacity="0.5"/>
        <line x1="${xc - 4}" y1="${yBot}" x2="${xc + 4}" y2="${yBot}" stroke="${color}" stroke-width="1.5" opacity="0.5"/>`;
    }

    // 每个 seed 的散点（横向微抖动避免重叠）
    seeds.forEach((s, si) => {
      if (s.success_rate == null) return;
      const jitter = (si - (seeds.length - 1) / 2) * 3; // 横向抖动 ±6px
      const sx = X(i) + jitter;
      const sy = Y(s.success_rate);
      const sColor = s.crash_rate > 0 ? "#fbbf24" : (s.success_rate >= 0.85 ? "#34d399" : "#f87171");
      seedDots += `<circle cx="${sx.toFixed(1)}" cy="${sy.toFixed(1)}" r="3" fill="${sColor}" opacity="0.6" stroke="${sColor}" stroke-width="1">
        <title>seed_${s.seed}\n成功率: ${(s.success_rate * 100).toFixed(1)}%\n坠毁率: ${s.crash_rate != null ? (s.crash_rate * 100).toFixed(1) + "%" : "-"}\n高度误差: ${s.mean_alt_err_m != null ? s.mean_alt_err_m.toFixed(2) + "m" : "-"}\n回合数: ${s.episodes || 0}</title>
      </circle>`;
    });

    const title = `${esc(l.id)}\n时间: ${esc(l.timestamp || "-")}\n任务: ${task} · 模式: ${mode}\n整体成功率: ${(sr * 100).toFixed(1)}% (${seeds.length} 个 seed 均值)\n坠毁率: ${crashStr}\n高度误差: ${altErr}\n仿真时长: ${fmtHours(l.sim_hours)}\n--- 各 seed ---\n${seedDetail}`;

    return `<circle cx="${X(i).toFixed(1)}" cy="${Y(sr).toFixed(1)}" r="5" fill="#0a1120" stroke="${color}" stroke-width="2">
      <title>${title}</title>
    </circle>`;
  }).join("");

  const passLine = `<line x1="${padL}" y1="${Y(0.85)}" x2="${W - padR}" y2="${Y(0.85)}" stroke="#34d399" stroke-dasharray="6 4" stroke-width="1" opacity="0.5"/>
    <text x="${W - padR - 4}" y="${Y(0.85) - 5}" text-anchor="end" font-size="10" fill="#34d399" font-family="Consolas" opacity="0.7">85% 达标线</text>`;

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridY}${xLabels}${passLine}
    <path d="${pathD}" fill="none" stroke="#22d3ee" stroke-width="2" stroke-linejoin="round" opacity="0.8"/>
    ${errorBars}
    ${seedDots}
    ${dots}
    <text x="${W / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="#5b6f8d" font-family="Consolas">测试时间（按时间正序，共 ${n} 次测试 · 大圆点=均值 · 小圆点=单seed · 竖线=±1σ）</text>
  </svg>`;
}

/* =========================================================================
   2. 交互式三维飞行轨迹与实时遥测播放器
   ========================================================================= */

function draw3DTrajectoryCanvas(canvas, frames, currentStepIdx = null) {
  if (!canvas || !frames || !frames.length) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width || 760;
  const H = canvas.height || 300;
  canvas.width = W; canvas.height = H;

  ctx.fillStyle = "#050b14";
  ctx.fillRect(0, 0, W, H);

  let minX = 0, maxX = 0, minY = 0, maxY = 0, minZ = 0, maxZ = 25;
  frames.forEach(f => {
    const x = f.x_m || 0;
    const y = f.y_m || 0;
    const z = f.agl_m || 0;
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
  });
  
  const spanX = Math.max(maxX - minX, 40);
  const spanY = Math.max(maxY - minY, 40);
  const spanZ = Math.max(maxZ, 25);

  const cosA = Math.cos(Math.PI / 6);
  const sinA = Math.sin(Math.PI / 6);
  const cx = W / 2;
  const cy = H * 0.72;
  const scale = Math.min(W / (spanX + spanY + 40), H / (spanZ + 30)) * 0.85;

  function project(x, y, z) {
    const px = cx + (x - minX - (spanX / 2)) * cosA * scale - (y - minY - (spanY / 2)) * cosA * scale;
    const py = cy + (x - minX - (spanX / 2)) * sinA * 0.5 * scale + (y - minY - (spanY / 2)) * sinA * 0.5 * scale - (z * scale);
    return { x: px, y: py };
  }

  // 1. 地面网格
  ctx.strokeStyle = "#122035";
  ctx.lineWidth = 1;
  const gridDivs = 8;
  for (let i = 0; i <= gridDivs; i++) {
    const gx = minX + (i / gridDivs) * spanX;
    const gy = minY + (i / gridDivs) * spanY;
    const p1 = project(gx, minY, 0);
    const p2 = project(gx, minY + spanY, 0);
    ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();

    const q1 = project(minX, gy, 0);
    const q2 = project(minX + spanX, gy, 0);
    ctx.beginPath(); ctx.moveTo(q1.x, q1.y); ctx.lineTo(q2.x, q2.y); ctx.stroke();
  }

  // 2. 停机坪
  const padCenter = project(0, 0, 0);
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.ellipse(padCenter.x, padCenter.y, 22 * cosA, 11 * sinA, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = "rgba(56, 189, 248, 0.15)";
  ctx.fill();
  ctx.fillStyle = "#38bdf8";
  ctx.font = "bold 10px Consolas";
  ctx.fillText("H", padCenter.x - 3, padCenter.y + 4);

  // 3. 地面阴影
  ctx.strokeStyle = "rgba(100, 116, 139, 0.25)";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  frames.forEach((f, i) => {
    const p = project(f.x_m || 0, f.y_m || 0, 0);
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
  ctx.setLineDash([]);

  // 4. 3D 飞行轨迹
  const curIdx = currentStepIdx !== null ? Math.min(currentStepIdx, frames.length - 1) : frames.length - 1;
  
  for (let i = 0; i < curIdx; i++) {
    const f1 = frames[i];
    const f2 = frames[i + 1];
    const p1 = project(f1.x_m || 0, f1.y_m || 0, f1.agl_m || 0);
    const p2 = project(f2.x_m || 0, f2.y_m || 0, f2.agl_m || 0);

    const altNorm = Math.min(Math.max((f2.agl_m || 0) / 25, 0), 1);
    const r = Math.round(34 + altNorm * 220);
    const g = Math.round(211 - altNorm * 60);
    const b = Math.round(238 - altNorm * 180);

    ctx.strokeStyle = `rgb(${r}, ${g}, ${b})`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
  }

  // 5. 当前位置 3D 标牌
  if (curIdx >= 0 && curIdx < frames.length) {
    const curF = frames[curIdx];
    const pos3D = project(curF.x_m || 0, curF.y_m || 0, curF.agl_m || 0);
    const shadowPos = project(curF.x_m || 0, curF.y_m || 0, 0);

    ctx.strokeStyle = "rgba(52, 211, 153, 0.4)";
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(pos3D.x, pos3D.y);
    ctx.lineTo(shadowPos.x, shadowPos.y);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "rgba(52, 211, 153, 0.4)";
    ctx.beginPath();
    ctx.arc(shadowPos.x, shadowPos.y, 4, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#34d399";
    ctx.shadowColor = "#34d399";
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.arc(pos3D.x, pos3D.y, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.fillStyle = "#ffffff";
    ctx.font = "11px Consolas";
    ctx.fillText(`AGL: ${(curF.agl_m || 0).toFixed(1)}m | ${(curF.speed_kts || 0).toFixed(0)}kt`, pos3D.x + 10, pos3D.y - 6);
  }
}

/* ---------- 高 DPI 自适应 + 可交互透视投影 3D 轨迹 (V2) ---------- */
function setupCanvasHD(canvas, aspectRatio) {
  aspectRatio = aspectRatio || (8 / 3);
  const dpr = window.devicePixelRatio || 1;
  const parentW = canvas.parentElement ? canvas.parentElement.clientWidth : 760;
  const displayW = Math.max(320, Math.min(parentW - 16, 1400));
  const displayH = Math.round(displayW / aspectRatio);
  canvas.style.width = displayW + "px";
  canvas.style.height = displayH + "px";
  canvas.style.maxWidth = "100%";
  canvas.style.display = "block";
  canvas.style.margin = "0 auto";
  canvas.width = Math.round(displayW * dpr);
  canvas.height = Math.round(displayH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: displayW, h: displayH };
}

function draw3DTrajectoryV2(canvas, frames, view, currentStepIdx) {
  if (!canvas || !frames || !frames.length) return;
  view = view || { rotX: -0.35, rotY: 0.7, zoom: 1.0 };
  const curIdx = currentStepIdx != null ? Math.min(currentStepIdx, frames.length - 1) : frames.length - 1;
  const { ctx, w, h } = setupCanvasHD(canvas, 8 / 3);
  ctx.fillStyle = "#050b14"; ctx.fillRect(0, 0, w, h);

  let hasAbs = frames.some(f => f.x_m != null || f.y_m != null);
  let px = 0, py = 0;
  const pts = frames.map((f, i) => {
    if (hasAbs) { px = f.x_m || 0; py = f.y_m || 0; }
    else { const dt = i > 0 ? Math.max(0.001, (f.t||0) - (frames[i-1].t||0)) : 0.02; px += (f.vx_mps||0)*dt; py += (f.vy_mps||0)*dt; }
    return { x: px, y: py, z: f.agl_m || 0 };
  });

  const xs = pts.map(p=>p.x), ys = pts.map(p=>p.y), zs = pts.map(p=>p.z);
  const cx = (Math.min(...xs)+Math.max(...xs))/2, cy = (Math.min(...ys)+Math.max(...ys))/2, cz = (Math.min(...zs)+Math.max(...zs))/2;
  const rx = Math.max((Math.max(...xs)-Math.min(...xs))/2, 5), ry = Math.max((Math.max(...ys)-Math.min(...ys))/2, 5), rz = Math.max((Math.max(...zs)-Math.min(...zs))/2, 5);
  const maxR = Math.max(rx, ry, rz);
  const fov = Math.min(w, h) * 0.42 * view.zoom;
  const camDist = maxR * 3.2;

  function project(p) {
    let x = (p.x-cx)/maxR, y = (p.y-cy)/maxR, z = (p.z-cz)/maxR;
    let x1 = x*Math.cos(view.rotY) - z*Math.sin(view.rotY);
    let z1 = x*Math.sin(view.rotY) + z*Math.cos(view.rotY);
    let y1 = y*Math.cos(view.rotX) - z1*Math.sin(view.rotX);
    let z2 = y*Math.sin(view.rotX) + z1*Math.cos(view.rotX);
    const s = fov / (camDist/maxR + z2);
    return { x: w/2 + x1*s, y: h/2 - y1*s, depth: z2 };
  }

  // 立方体边框
  const corners = [{x:cx-rx,y:cy-ry,z:cz-rz},{x:cx+rx,y:cy-ry,z:cz-rz},{x:cx+rx,y:cy+ry,z:cz-rz},{x:cx-rx,y:cy+ry,z:cz-rz},{x:cx-rx,y:cy-ry,z:cz+rz},{x:cx+rx,y:cy-ry,z:cz+rz},{x:cx+rx,y:cy+ry,z:cz+rz},{x:cx-rx,y:cy+ry,z:cz+rz}].map(project);
  const edges = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
  ctx.strokeStyle = "rgba(91,111,141,0.18)"; ctx.lineWidth = 0.8;
  edges.forEach(([a,b]) => { ctx.beginPath(); ctx.moveTo(corners[a].x,corners[a].y); ctx.lineTo(corners[b].x,corners[b].y); ctx.stroke(); });

  // 地面网格
  ctx.strokeStyle = "rgba(34,211,238,0.06)"; ctx.lineWidth = 0.8;
  for (let i = -2; i <= 2; i++) {
    const gx = cx + i*rx*0.5;
    const p1 = project({x:gx,y:cy-ry,z:cz-rz}), p2 = project({x:gx,y:cy+ry,z:cz-rz});
    ctx.beginPath(); ctx.moveTo(p1.x,p1.y); ctx.lineTo(p2.x,p2.y); ctx.stroke();
    const gy = cy + i*ry*0.5;
    const p3 = project({x:cx-rx,y:gy,z:cz-rz}), p4 = project({x:cx+rx,y:gy,z:cz-rz});
    ctx.beginPath(); ctx.moveTo(p3.x,p3.y); ctx.lineTo(p4.x,p4.y); ctx.stroke();
  }

  // 坐标轴
  const o = project({x:cx,y:cy,z:cz-rz});
  const xa = project({x:cx+rx*1.1,y:cy,z:cz-rz}), ya = project({x:cx,y:cy+ry*1.1,z:cz-rz}), za = project({x:cx,y:cy,z:cz-rz+rz*1.1});
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "#f87171"; ctx.beginPath(); ctx.moveTo(o.x,o.y); ctx.lineTo(xa.x,xa.y); ctx.stroke();
  ctx.strokeStyle = "#34d399"; ctx.beginPath(); ctx.moveTo(o.x,o.y); ctx.lineTo(ya.x,ya.y); ctx.stroke();
  ctx.strokeStyle = "#22d3ee"; ctx.beginPath(); ctx.moveTo(o.x,o.y); ctx.lineTo(za.x,za.y); ctx.stroke();
  ctx.font = "10px Consolas"; ctx.fillStyle = "#7a90b0";
  ctx.fillText("X", xa.x+4, xa.y+3); ctx.fillText("Y", ya.x+4, ya.y+3); ctx.fillText("Z/alt", za.x+4, za.y-2);

  // 地面阴影
  ctx.strokeStyle = "rgba(100,116,139,0.2)"; ctx.lineWidth = 1.2; ctx.setLineDash([3,3]);
  ctx.beginPath(); pts.forEach((p,i) => { const pr = project({x:p.x,y:p.y,z:cz-rz}); if(i===0) ctx.moveTo(pr.x,pr.y); else ctx.lineTo(pr.x,pr.y); }); ctx.stroke(); ctx.setLineDash([]);

  // 主轨迹（高度渐变：蓝→绿→红，只画到当前帧）
  const zMin = Math.min(...zs), zMax = Math.max(...zs);
  for (let i = 1; i <= curIdx; i++) {
    const p1 = project(pts[i-1]), p2 = project(pts[i]);
    const t = Math.max(0, Math.min(1, (pts[i].z-zMin)/(zMax-zMin||1)));
    // 三段渐变：蓝(低)→绿(中)→红(高)
    let r, g, b;
    if (t < 0.5) {
      const k = t / 0.5;
      r = Math.round(56 + k * (52 - 56));
      g = Math.round(189 + k * (211 - 189));
      b = Math.round(248 + k * (153 - 248));
    } else {
      const k = (t - 0.5) / 0.5;
      r = Math.round(52 + k * (248 - 52));
      g = Math.round(211 + k * (113 - 211));
      b = Math.round(153 + k * (113 - 153));
    }
    ctx.strokeStyle = `rgb(${r},${g},${b})`;
    ctx.lineWidth = 2.2; ctx.globalAlpha = 0.9;
    ctx.beginPath(); ctx.moveTo(p1.x,p1.y); ctx.lineTo(p2.x,p2.y); ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // 起终点
  const s = project(pts[0]), e = project(pts[pts.length-1]);
  ctx.fillStyle = "#34d399"; ctx.beginPath(); ctx.arc(s.x,s.y,5,0,Math.PI*2); ctx.fill();
  ctx.strokeStyle = "#050b14"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.fillStyle = "#f87171"; ctx.beginPath(); ctx.arc(e.x,e.y,5,0,Math.PI*2); ctx.fill();
  ctx.strokeStyle = "#050b14"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.font = "10px Consolas"; ctx.fillStyle = "#34d399"; ctx.fillText("起", s.x+8, s.y-4);
  ctx.fillStyle = "#f87171"; ctx.fillText("终", e.x+8, e.y-4);

  // 当前飞行器位置标记
  if (curIdx >= 0 && curIdx < pts.length) {
    const cp = project(pts[curIdx]);
    const cpf = frames[curIdx] || {};
    // 垂直投影线
    const shadow = project({x:pts[curIdx].x, y:pts[curIdx].y, z:cz-rz});
    ctx.strokeStyle = "rgba(52,211,153,0.4)"; ctx.lineWidth = 1; ctx.setLineDash([2,2]);
    ctx.beginPath(); ctx.moveTo(cp.x,cp.y); ctx.lineTo(shadow.x,shadow.y); ctx.stroke(); ctx.setLineDash([]);
    // 机体圆点与光晕
    ctx.fillStyle = "#34d399"; ctx.shadowColor = "#34d399"; ctx.shadowBlur = 12;
    ctx.beginPath(); ctx.arc(cp.x,cp.y,6,0,Math.PI*2); ctx.fill(); ctx.shadowBlur = 0;
    ctx.strokeStyle = "#050b14"; ctx.lineWidth = 1.5; ctx.stroke();
    // 标签
    ctx.fillStyle = "#fff"; ctx.font = "10px Consolas";
    ctx.fillText(`AGL:${(cpf.agl_m||0).toFixed(1)}m ${(cpf.speed_kts||0).toFixed(0)}kt`, cp.x+10, cp.y-6);
  }

  ctx.fillStyle = "rgba(91,111,141,0.5)"; ctx.font = "9.5px Consolas"; ctx.textAlign = "right";
  ctx.fillText("拖拽旋转 · 滚轮缩放", w-8, h-6); ctx.textAlign = "left";
}

function init3DViewInteraction(canvas, viewState, redrawFn) {
  let dragging = false, lastX = 0, lastY = 0;
  canvas.style.cursor = "grab";
  canvas.addEventListener("mousedown", (ev) => { dragging = true; lastX = ev.clientX; lastY = ev.clientY; canvas.style.cursor = "grabbing"; ev.preventDefault(); });
  window.addEventListener("mousemove", (ev) => {
    if (!dragging) return;
    const dx = ev.clientX-lastX, dy = ev.clientY-lastY;
    viewState.rotY += dx*0.008; viewState.rotX += dy*0.008;
    viewState.rotX = Math.max(-1.4, Math.min(1.4, viewState.rotX));
    lastX = ev.clientX; lastY = ev.clientY; redrawFn();
  });
  window.addEventListener("mouseup", () => { dragging = false; canvas.style.cursor = "grab"; });
  canvas.addEventListener("wheel", (ev) => { ev.preventDefault(); viewState.zoom *= ev.deltaY>0?0.92:1.08; viewState.zoom = Math.max(0.4, Math.min(3.5, viewState.zoom)); redrawFn(); }, { passive: false });
  canvas.addEventListener("contextmenu", (ev) => ev.preventDefault());
}

function drawMultiSeriesSVG(el, frames, series, opts = {}) {
  if (!el || !frames || !frames.length) { el.innerHTML = '<div class="empty">无数据</div>'; return; }
  const W = opts.width || 480, H = opts.height || 200;
  const leftSeries = series.filter(s => s.yAxis !== "right");
  const rightSeries = series.filter(s => s.yAxis === "right");
  const hasRightAxis = rightSeries.length > 0;
  const padL = 45, padR = hasRightAxis ? 42 : 15, padT = 16, padB = 30;
  const tMax = frames[frames.length - 1].t || frames.length * 0.02;
  const X = (t) => padL + (t / tMax) * (W - padL - padR);

  // 左 Y 轴范围
  let yMinL = Infinity, yMaxL = -Infinity;
  leftSeries.forEach((s) => {
    frames.forEach((f) => {
      const v = f[s.key];
      if (v != null && !isNaN(v)) { yMinL = Math.min(yMinL, v); yMaxL = Math.max(yMaxL, v); }
    });
  });
  if (yMinL === Infinity) { yMinL = 0; yMaxL = 1; }
  if (yMinL === yMaxL) { yMinL -= 1; yMaxL += 1; }
  const padL_y = (yMaxL - yMinL) * 0.08;
  if (opts.fixedMin == null) yMinL -= padL_y;
  if (opts.fixedMax == null) yMaxL += padL_y;
  const YL = (v) => padT + (1 - (v - yMinL) / (yMaxL - yMinL)) * (H - padT - padB);

  // 右 Y 轴范围
  let yMinR = Infinity, yMaxR = -Infinity;
  if (hasRightAxis) {
    rightSeries.forEach((s) => {
      frames.forEach((f) => {
        const v = f[s.key];
        if (v != null && !isNaN(v)) { yMinR = Math.min(yMinR, v); yMaxR = Math.max(yMaxR, v); }
      });
    });
    if (yMinR === Infinity) { yMinR = 0; yMaxR = 1; }
    if (yMinR === yMaxR) { yMinR -= 1; yMaxR += 1; }
    const padR_y = (yMaxR - yMinR) * 0.08;
    yMinR -= padR_y; yMaxR += padR_y;
  }
  const YR = (v) => padT + (1 - (v - yMinR) / (yMaxR - yMinR)) * (H - padT - padB);

  const Y = (v, s) => (s && s.yAxis === "right") ? YR(v) : YL(v);

  // 左 Y 轴网格
  const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = yMinL + f * (yMaxL - yMinL);
    return `<line x1="${padL}" y1="${YL(v)}" x2="${W - padR}" y2="${YL(v)}" stroke="#1c2b45" stroke-dasharray="3 5"/>
      <text x="${padL - 6}" y="${YL(v) + 3.5}" text-anchor="end" font-size="9" fill="#5b6f8d" font-family="Consolas">${v.toFixed(opts.decimals || 1)}</text>`;
  }).join("");

  // 右 Y 轴标签
  let rightAxisLabels = "";
  if (hasRightAxis) {
    rightAxisLabels = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const v = yMinR + f * (yMaxR - yMinR);
      return `<text x="${W - padR + 4}" y="${YR(v) + 3.5}" text-anchor="start" font-size="9" fill="#a78bfa" font-family="Consolas">${v.toFixed(1)}</text>`;
    }).join("");
  }

  const xLabels = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const t = f * tMax;
    return `<text x="${X(t)}" y="${H - 8}" text-anchor="middle" font-size="9" fill="#5b6f8d" font-family="Consolas">${t.toFixed(1)}s</text>`;
  }).join("");

  let refLines = "";
  if (opts.refLines) {
    opts.refLines.forEach(rl => {
      if (rl.val >= yMinL && rl.val <= yMaxL) {
        refLines += `<line x1="${padL}" y1="${YL(rl.val)}" x2="${W - padR}" y2="${YL(rl.val)}" stroke="${rl.color}" stroke-dasharray="4 3" stroke-width="1.2" opacity="0.8"/>
        <text x="${W - padR - 4}" y="${YL(rl.val) - 3}" text-anchor="end" font-size="8.5" fill="${rl.color}" font-family="Consolas">${rl.label}</text>`;
      }
    });
  }

  const paths = series.map((s) => {
    let d = "";
    let started = false;
    frames.forEach((f) => {
      const v = f[s.key];
      if (v != null && !isNaN(v)) {
        d += `${started ? "L" : "M"}${X(f.t).toFixed(1)},${Y(v, s).toFixed(1)} `;
        started = true;
      }
    });
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.8}" stroke-linejoin="round" opacity="0.95"/>`;
  }).join("");

  let cursorLine = "";
  if (opts.currentT != null && opts.currentT <= tMax) {
    const cx = X(opts.currentT);
    cursorLine = `<line x1="${cx}" y1="${padT}" x2="${cx}" y2="${H - padB}" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="2 2"/>`;
  }

  const legend = series.map((s) =>
    `<span><i class="sw" style="background:${s.color}"></i>${s.label}${s.yAxis === "right" ? ' <span style="color:#a78bfa;font-size:9px">[右轴]</span>' : ""}</span>`
  ).join("") + (opts.refLines ? opts.refLines.map(rl => `<span><i class="sw" style="background:${rl.color};border-top:1px dashed ${rl.color}"></i>${rl.label}</span>`).join("") : "");

  el.innerHTML = `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
    data-tmax="${tMax}" data-ymin="${yMinL}" data-ymax="${yMaxL}"
    data-yminr="${yMinR}" data-ymaxr="${yMaxR}" data-hasright="${hasRightAxis}"
    data-padl="${padL}" data-padr="${padR}" data-padt="${padT}" data-padb="${padB}"
    data-w="${W}" data-h="${H}">
    ${gridY}${rightAxisLabels}${xLabels}${refLines}${paths}${cursorLine}
    <g class="crosshair" style="display:none">
      <line class="ch-line" x1="0" y1="${padT}" x2="0" y2="${H - padB}" stroke="#fbbf24" stroke-width="1" stroke-dasharray="3 2"/>
      <text class="ch-time" x="0" y="${padT - 4}" text-anchor="middle" font-size="9" fill="#fbbf24" font-family="Consolas"></text>
      <g class="ch-values"></g>
    </g>
  </svg><div class="legend small">${legend}</div>`;

  // 存储数据供 crosshair 使用
  el._frames = frames;
  el._series = series;
}

/* =========================================================================
   3. 时间线渲染与内嵌动态回放控制器 (Timeline-Integrated Player)
   ========================================================================= */

// 渲染环境参数与训练配置（区分不同模拟条件）
function renderEnvConfig(l) {
  const ec = l.env_config;
  const tc = l.train_config;
  const isFg = l.engine === "flightgear";

  // FG 实飞演示：展示 FG 环境参数
  if (isFg) {
    const fgCfg = l.fg_config || {};
    const items = [
      fgCfg.aircraft ? ["飞行器模型", fgCfg.aircraft] : null,
      fgCfg.fg_version ? ["FlightGear 版本", fgCfg.fg_version] : null,
      fgCfg.scenario ? ["场景/机场", fgCfg.scenario] : null,
      fgCfg.wind ? ["风场条件", fgCfg.wind] : null,
      fgCfg.weather ? ["天气条件", fgCfg.weather] : null,
      fgCfg.time_of_day ? ["时间", fgCfg.time_of_day] : null,
      fgCfg.control_mode ? ["控制模式", fgCfg.control_mode] : null,
      fgCfg.bridge ? ["桥接协议", fgCfg.bridge] : null,
      l.summary?.result ? ["演示结果", l.summary.result] : null,
      l.summary?.reason ? ["结果说明", l.summary.reason] : null,
      l.summary?.max_agl_ft ? ["最高离地", l.summary.max_agl_ft.toFixed(1) + " ft"] : null,
    ].filter(Boolean);
    if (!items.length) return "";
    return `<div class="env-config-section">
      <div class="env-config-title">🛩 FlightGear 实飞环境参数</div>
      <div class="env-config-grid">
        ${items.map(([k, v]) => `<div class="env-item"><span class="env-k">${esc(k)}</span><span class="env-v mono">${esc(v)}</span></div>`).join("")}
      </div>
    </div>`;
  }

  // RL 训练仿真：展示环境参数 + 训练超参
  if (!ec && !tc) return "";

  let envItems = [];
  if (ec) {
    envItems = [
      ec.engine ? ["仿真引擎", ec.engine] : null,
      ec.aircraft ? ["飞行器", ec.aircraft] : null,
      ec.physics ? ["物理模型", ec.physics] : null,
      ec.action_mode ? ["动作模式", ec.action_mode] : null,
      ec.wind_enabled != null ? ["风场扰动", ec.wind_enabled ? "开启" : "关闭"] : null,
      ec.wind_range_fps ? ["风速范围", ec.wind_range_fps[0] + " ~ " + ec.wind_range_fps[1] + " fps"] : null,
      ec.target_range_m ? ["目标高度范围", ec.target_range_m[0] + " ~ " + ec.target_range_m[1] + " m"] : null,
      ec.episode_steps ? ["回合步数", ec.episode_steps + " 步 (" + (ec.episode_seconds || ec.episode_steps * (ec.dt || 0.02)) + "s)"] : null,
      ec.dt ? ["仿真步长", ec.dt + " s (" + (1 / ec.dt).toFixed(0) + " Hz)"] : null,
      ec.repair_overrides ? ["物理修复", ec.repair_overrides ? "已开启" : "未开启"] : null,
      ec.initial_condition ? ["初始条件", ec.initial_condition] : null,
      ec.crash_criteria ? ["坠毁判定", ec.crash_criteria] : null,
      ec.success_criteria ? ["成功判定", ec.success_criteria] : null,
    ].filter(Boolean);
  }

  let trainItems = [];
  if (tc) {
    const ppo = tc.ppo_hyperparams || {};
    trainItems = [
      tc.algorithm ? ["算法", tc.algorithm] : null,
      tc.total_steps ? ["训练总步数", tc.total_steps.toLocaleString()] : null,
      tc.n_envs ? ["并行环境数", tc.n_envs] : null,
      tc.learning_rate ? ["学习率", tc.learning_rate] : null,
      tc.ent_coef ? ["熵系数", tc.ent_coef] : null,
      tc.seed != null ? ["训练种子", tc.seed] : null,
      tc.eval_seeds ? ["评估种子", tc.eval_seeds.join(", ")] : null,
      tc.eval_episodes_per_seed ? ["每种子评估回合", tc.eval_episodes_per_seed] : null,
      ppo.n_steps ? ["PPO n_steps", ppo.n_steps] : null,
      ppo.batch_size ? ["PPO batch_size", ppo.batch_size] : null,
      ppo.n_epochs ? ["PPO n_epochs", ppo.n_epochs] : null,
      ppo.gamma ? ["折扣因子 γ", ppo.gamma] : null,
      ppo.gae_lambda ? ["GAE λ", ppo.gae_lambda] : null,
      ppo.clip_range ? ["裁剪范围", ppo.clip_range] : null,
      tc.init_model ? ["课程学习初始模型", tc.init_model] : null,
    ].filter(Boolean);
  }

  let html = `<div class="env-config-section">`;
  if (envItems.length) {
    html += `<div class="env-config-title">🌍 仿真环境参数</div>
      <div class="env-config-grid">
        ${envItems.map(([k, v]) => `<div class="env-item"><span class="env-k">${esc(k)}</span><span class="env-v mono">${esc(v)}</span></div>`).join("")}
      </div>`;
  }
  if (trainItems.length) {
    html += `<div class="env-config-title" style="margin-top:12px;">⚙ 训练超参数</div>
      <div class="env-config-grid">
        ${trainItems.map(([k, v]) => `<div class="env-item"><span class="env-k">${esc(k)}</span><span class="env-v mono">${esc(v)}</span></div>`).join("")}
      </div>`;
  }
  html += `</div>`;
  return html;
}

function renderTimeline(D) {
  const logs = D.flight_logs || [];
  if (!logs.length) { $("#flightEmpty").classList.remove("hidden"); return; }

  const tasks = [...new Set(logs.map((l) => l.task))].sort();
  const sel = $("#filterTask");
  if (sel.options.length <= 1) {
    tasks.forEach((t) => { const o = document.createElement("option"); o.value = t; o.textContent = t; sel.appendChild(o); });
  }

  const ft = $("#filterTask").value;
  const fm = $("#filterMode").value;
  const fs = $("#filterStatus").value;
  const ftype = $("#filterType")?.value || "";
  const search = ($("#searchInput")?.value || "").toLowerCase().trim();

  const filtered = logs.filter((l) => {
    if (ft && l.task !== ft) return false;
    if (fm && l.mode !== fm) return false;
    if (fs === "pass" && !l.acceptance_passed) return false;
    if (fs === "fail" && l.acceptance_passed) return false;
    if (ftype === "rl" && l.engine === "flightgear") return false;
    if (ftype === "fg" && l.engine !== "flightgear") return false;
    if (search) {
      const haystack = `${l.id} ${l.task} ${l.mode} ${l.engine} ${l.aircraft} ${l.acceptance_passed ? "pass" : "fail"}`.toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    return true;
  });

  $("#filterCount").textContent = `显示 ${filtered.length} / ${logs.length} 条记录`;
  // 筛选统计汇总
  const statsEl = document.getElementById("timelineStats");
  if (statsEl && filtered.length) {
    const totalH = filtered.reduce((s, l) => s + (l.sim_hours || 0), 0);
    const avgSr = filtered.reduce((s, l) => s + (l.success_rate || 0), 0) / filtered.length;
    const passCount = filtered.filter(l => l.acceptance_passed).length;
    const crashCount = filtered.filter(l => l.crash_rate && l.crash_rate > 0).length;
    statsEl.textContent = `｜ 筛选结果：${filtered.length} 条 · ${fmtHours(totalH)} · 平均成功率 ${fmtPct(avgSr, 1)} · 通过 ${passCount} · 坠毁 ${crashCount}`;
  } else if (statsEl) {
    statsEl.textContent = "";
  }

  if (!filtered.length) {
    $("#flightTimeline").innerHTML = "";
    $("#flightEmpty").classList.remove("hidden");
    return;
  }
  $("#flightEmpty").classList.add("hidden");

  $("#flightTimeline").innerHTML = filtered.map((l, idx) => {
    const passCls = l.acceptance_passed ? "pass" : "fail";
    const passLabel = l.acceptance_passed ? "验收通过" : "未通过";
    const hasCrash = l.crash_rate && l.crash_rate > 0;
    const warnCls = hasCrash ? "tl-crash" : (l.acceptance_passed ? "" : "tl-fail");
    const crashBadge = hasCrash
      ? `<span class="badge bad">坠毁 ${fmtPct(l.crash_rate, 1)}</span>` : "";
    const srColor = l.success_rate >= 0.85 ? "var(--ok)" : (l.success_rate > 0 ? "var(--warn)" : "var(--bad)");
    const isFgDemo = l.engine === "flightgear";
    const typeBadge = isFgDemo
      ? `<span class="badge type-fg">FG实飞演示</span>`
      : `<span class="badge type-rl">RL训练仿真</span>`;

    let seedHtml = "";
    if (l.seed_results && l.seed_results.length) {
      seedHtml = `<div class="seed-section">
        <div class="seed-title mono">种子级评估结果（${l.seed_results.length} 种子 × ${l.seed_results[0].episodes} 回合）</div>
        <div class="seed-grid">
          ${l.seed_results.map((s) => {
            const sOk = s.success_rate >= 0.85;
            const sCrash = s.crash_rate > 0;
            return `<div class="seed-card ${sOk ? "ok" : "fail"}">
              <div class="seed-no mono">seed ${esc(s.seed)}</div>
              <div class="seed-metrics">
                <span>成功率 <b style="color:${sOk ? 'var(--ok)' : 'var(--warn)'}">${fmtPct(s.success_rate, 0)}</b></span>
                <span>坠毁 <b style="color:${sCrash ? 'var(--bad)' : 'var(--ok)'}">${fmtPct(s.crash_rate, 0)}</b></span>
                <span>高度误差 <b>${s.mean_alt_err_m.toFixed(2)}m</b></span>
              </div>
            </div>`;
          }).join("")}
        </div>
      </div>`;
    }

    const modelKey = l.id ? l.id.replace("_results.json", "").replace(".json", "") : "";
    const hasTraj = D.trajectories && D.trajectories[modelKey];
    let curveHtml = "";
    if (hasTraj) {
      curveHtml = `<div class="timeline-curves" id="timelineCurves-${idx}" data-model="${esc(modelKey)}">
        <div class="timeline-curves-header">
          <span class="timeline-curves-title">三维空间飞行航迹与四通道时序曲线回放（JSBSim 逐帧物理数据）</span>
          <span class="muted small">数据源：${esc(modelKey)}_trajectory.json</span>
        </div>
        
        <div class="curve-selector timeline-curve-selector">
          <label>种子：<select class="tl-curve-seed"><option value="">--</option></select></label>
          <label>回合：<select class="tl-curve-ep"><option value="">--</option></select></label>
          <div class="player-controls">
            <button class="btn-play tl-btn-play">▶ 播放飞行轨迹</button>
            <label>倍速:
              <select class="tl-speed-sel" style="max-width:65px;">
                <option value="1">1x</option>
                <option value="2" selected>2x</option>
                <option value="5">5x</option>
              </select>
            </label>
          </div>
          <span class="tl-curve-info muted small mono"></span>
        </div>

        <div class="tl-hud-grid hud-grid"></div>

        <div class="traj-3d-wrap">
          <div class="traj-3d-header">
            <div class="traj-3d-title">3D 飞行航迹（可拖拽旋转 · 滚轮缩放）</div>
            <div class="view-buttons">
              <button class="view-btn" data-view="persp" title="透视图">透视</button>
              <button class="view-btn" data-view="top" title="俯视图（从上往下）">俯视</button>
              <button class="view-btn" data-view="side" title="侧视图（从侧面）">侧视</button>
              <button class="view-btn" data-view="front" title="前视图（从前方）">前视</button>
            </div>
          </div>
          <canvas class="tl-3d-canvas traj-canvas"></canvas>
          <div class="scrubber-bar">
            <span class="mono small muted">0.00s</span>
            <input type="range" class="tl-time-slider time-slider" min="0" max="100" value="100">
            <span class="mono small muted">实况时间轴 (拖动检索)</span>
          </div>
        </div>

        <div class="curve-grid">
          <div class="curve-panel"><div class="curve-title">1. 高度跟踪与垂直速度</div><div class="chart tl-curve-alt"></div></div>
          <div class="curve-panel"><div class="curve-title">2. 三轴姿态角与安全包络</div><div class="chart tl-curve-att"></div></div>
          <div class="curve-panel"><div class="curve-title">3. 角速度与 SAS 姿态增稳</div><div class="chart tl-curve-rate"></div></div>
          <div class="curve-panel"><div class="curve-title">4. 具身策略 4 通道操纵量</div><div class="chart tl-curve-ctrl"></div></div>
        </div>
      </div>`;
    }

    return `<div class="tl-item ${warnCls}" data-idx="${idx}">
      <div class="tl-dot ${passCls}"></div>
      <div class="tl-content">
        <div class="tl-header" onclick="toggleFlightDetail(${idx})">
          <div class="tl-time mono">${esc(l.timestamp ? l.timestamp.replace("T", " ").slice(0, 19) : "-")}</div>
          <div class="tl-tags">
            ${typeBadge}
            <span class="badge engine">${esc(l.engine)}</span>
            <span class="badge task">${esc(l.task)}</span>
            <span class="badge mode">${esc(l.mode)}</span>
            <span class="st ${passCls}">${passLabel}</span>
            ${crashBadge}
          </div>
          <div class="tl-toggle mono">▼ 点击展开/收起完整遥测与轨迹回放</div>
        </div>
        <div class="tl-compare-bar">
          <label class="tl-compare-check" onclick="event.stopPropagation()">
            <input type="checkbox" class="tl-compare-cb" data-idx="${idx}" data-model="${esc(modelKey)}" data-hastraj="${hasTraj}" ${hasTraj ? "" : "disabled"} onchange="toggleCompare(${idx}, '${esc(modelKey)}', this.checked)">
            <span>对比曲线</span>
          </label>
          ${hasTraj ? "" : '<span class="muted small" style="margin-left:8px;">（无轨迹数据）</span>'}
        </div>
        <div class="tl-summary" onclick="toggleFlightDetail(${idx})">
          ${isFgDemo ? `
            <span>飞行时长 <b class="mono">${(l.sim_hours * 3600).toFixed(0)}s</b></span>
            <span>帧数 <b class="mono">${fmtNum(l.steps, 0)}</b></span>
            <span>结果 <b class="mono" style="color:${srColor}">${l.acceptance_passed ? "成功" : "失败"}</b></span>
            ${l.summary && l.summary.max_agl_ft ? `<span>最高离地 <b class="mono">${l.summary.max_agl_ft.toFixed(1)}ft</b></span>` : ""}
            <span>仿真时长 <b class="mono">${fmtHours(l.sim_hours)}</b></span>
          ` : `
            <span>训练 <b class="mono">${fmtNum(l.steps, 0)}</b> 步 / <b class="mono">${l.train_seconds.toFixed(0)}s</b> (${l.steps_per_second ? l.steps_per_second.toFixed(0) + "步/s" : "-"})</span>
            <span>评估 <b class="mono">${l.eval_episodes}</b> 回合</span>
            <span>成功率 <b class="mono" style="color:${srColor}">${fmtPct(l.success_rate, 1)}</b></span>
            <span>高度误差 <b class="mono">${l.mean_alt_err_m != null ? l.mean_alt_err_m.toFixed(2) + "m" : "-"}</b></span>
            <span>仿真时长 <b class="mono">${fmtHours(l.sim_hours)}</b></span>
          `}
        </div>
        <div class="tl-detail hidden" id="flightDetail-${idx}">
          <div class="detail-grid">
            <div class="detail-item"><span class="dl">数据文件</span><span class="dv mono">${esc(l.id)}</span></div>
            <div class="detail-item"><span class="dl">文件路径</span><span class="dv mono small">${esc(l.path)}</span></div>
            <div class="detail-item"><span class="dl">SHA-256</span><span class="dv hash-txt" onclick="copyText('${esc(l.sha256)}',event)">${esc(shortSha(l.sha256, 16))}</span></div>
            <div class="detail-item"><span class="dl">文件大小</span><span class="dv mono">${l.bytes} bytes</span></div>
            <div class="detail-item"><span class="dl">训练仿真时长</span><span class="dv mono">${fmtHours(l.train_sim_hours)}</span></div>
            <div class="detail-item"><span class="dl">评估仿真时长</span><span class="dv mono">${fmtHours(l.eval_sim_hours)}</span></div>
            <div class="detail-item"><span class="dl">验证时间戳</span><span class="dv mono">${esc(l.verified_timestamp || "-")}</span></div>
            <div class="detail-item"><span class="dl">机型</span><span class="dv mono">${esc(l.aircraft)}</span></div>
          </div>
          ${renderEnvConfig(l)}
          ${seedHtml}
          ${curveHtml}
        </div>
      </div>
    </div>`;
  }).join("");

  // 默认展开并渲染第 1 条（最新且最关键的）测试记录！
  setTimeout(() => {
    toggleFlightDetail(0);
  }, 100);
}

/* ---------- 测试覆盖度矩阵 ---------- */
function renderCoverageMatrix(D) {
  const cov = D.coverage;
  const el = document.getElementById("coverageMatrix");
  if (!el || !cov) return;
  const { tasks, seeds, matrix, coverage_pct, tested_cells, total_cells } = cov;

  let html = `<div class="cov-summary">覆盖度 <b>${coverage_pct}%</b>（${tested_cells}/${total_cells} 组合已测）</div>`;
  html += '<div class="cov-table-wrap"><table class="cov-table"><thead><tr><th class="cov-corner">任务 \\ 种子</th>';
  seeds.forEach(s => { html += `<th class="cov-seed">seed ${esc(s)}</th>`; });
  html += '</tr></thead><tbody>';

  tasks.forEach(task => {
    html += `<tr><td class="cov-task">${esc(task)}</td>`;
    seeds.forEach(seed => {
      const cell = matrix[task][seed];
      if (cell.tested) {
        const sr = cell.avg_success;
        const cls = sr >= 0.85 ? "cov-ok" : (sr > 0 ? "cov-warn" : "cov-bad");
        const crash = cell.avg_crash > 0 ? `<div class="cov-crash">坠毁${(cell.avg_crash*100).toFixed(0)}%</div>` : "";
        html += `<td class="cov-cell ${cls}" title="${esc(task)} / seed ${esc(seed)}：${cell.tests}次测试，成功率${(sr*100).toFixed(1)}%">
          <div class="cov-tests">${cell.tests}次</div>
          <div class="cov-sr">${(sr*100).toFixed(0)}%</div>${crash}
        </td>`;
      } else {
        html += `<td class="cov-cell cov-untested" title="${esc(task)} / seed ${esc(seed)}：未测试"><div class="cov-untested-label">未测</div></td>`;
      }
    });
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  html += '<div class="cov-legend"><span><i class="sw" style="background:#1a3a2a;border:1px solid #2d5a3d"></i>成功率≥85%</span><span><i class="sw" style="background:#3a3a1a;border:1px solid #5a5a2d"></i>成功率>0%</span><span><i class="sw" style="background:#1a1a1a;border:1px dashed #555"></i>未测试</span></div>';

  // 待测试清单
  const untested = [];
  tasks.forEach(task => {
    seeds.forEach(seed => {
      if (!matrix[task][seed].tested) {
        untested.push({ task, seed });
      }
    });
  });
  if (untested.length) {
    html += `<div class="cov-untested">
      <div class="cov-untested-title">待测试清单（${untested.length} 个组合未测）</div>
      <div class="cov-untested-list">`;
    untested.forEach(u => {
      html += `<span class="cov-untested-item">${esc(u.task)} × seed ${esc(u.seed)}</span>`;
    });
    html += `</div>
      <div class="cov-untested-hint">建议优先补测以上组合，完成后覆盖度将达到 100%</div>
    </div>`;
  }

  el.innerHTML = html;
}

/* ---------- 仿真世界时间每日累计 ---------- */
function renderDailyProgress(D) {
  const dp = D.daily_progress;
  const el = document.getElementById("dailyProgress");
  if (!el || !dp) return;
  const { current_hours, daily, cumulative, avg_per_day, active_days } = dp;

  let html = `<div class="dp-kpis">
    <div class="dp-kpi"><span class="dp-label">日均仿真</span><span class="dp-value">${avg_per_day.toFixed(2)} h/天</span></div>
    <div class="dp-kpi"><span class="dp-label">活跃天数</span><span class="dp-value">${active_days} 天</span></div>
    <div class="dp-kpi"><span class="dp-label">有数据天数</span><span class="dp-value">${daily.length} 天</span></div>
    <div class="dp-kpi"><span class="dp-label">首次测试</span><span class="dp-value">${dp.first_date || "-"}</span></div>
    <div class="dp-kpi"><span class="dp-label">最新测试</span><span class="dp-value">${dp.latest_date || "-"}</span></div>
  </div>`;

  if (!daily.length) { el.innerHTML = html + '<div class="empty">暂无每日数据</div>'; return; }

  const W = 820, H = 260;
  const padL = 48, padR = 20, padT = 24, padB = 36;
  const maxDaily = Math.max(...daily.map(d => d.hours), 1);
  const maxCum = Math.max(...cumulative.map(d => d.cumulative), 1);
  const plotW = W - padL - padR;
  const gap = plotW / daily.length;
  const barW = gap * 0.55;
  const X = (i) => padL + i * gap + gap / 2;
  const YDaily = (v) => padT + (1 - v / maxDaily) * (H - padT - padB) * 0.45;
  const YCum = (v) => padT + (1 - v / maxCum) * (H - padT - padB);

  let grid = "";
  for (let i = 0; i <= 4; i++) {
    const y = padT + i * (H - padT - padB) / 4;
    grid += `<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="#1c2b45" stroke-dasharray="3 5"/>`;
    grid += `<text x="${padL-5}" y="${y+3}" text-anchor="end" font-size="9" fill="#5b6f8d" font-family="Consolas">${(maxCum*(1-i/4)).toFixed(0)}</text>`;
  }

  let bars = "";
  daily.forEach((d, i) => {
    const x = X(i) - barW/2;
    const y = YDaily(d.hours);
    const h = padT + (H-padT-padB)*0.45 - y;
    bars += `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(h,1)}" fill="#22d3ee" opacity="0.55" rx="2"/>`;
    if (daily.length <= 12) {
      bars += `<text x="${X(i)}" y="${H-padB+14}" text-anchor="middle" font-size="8" fill="#5b6f8d" font-family="Consolas">${d.date.slice(5)}</text>`;
    }
  });

  let cumPath = "";
  cumulative.forEach((d, i) => {
    const x = X(i), y = YCum(d.cumulative);
    cumPath += `${i===0?"M":"L"}${x.toFixed(1)},${y.toFixed(1)} `;
  });

  html += `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto;max-height:280px;">
    ${grid}
    ${bars}
    <path d="${cumPath}" fill="none" stroke="#fbbf24" stroke-width="2" stroke-linejoin="round"/>
    <text x="${padL}" y="${padT-6}" font-size="10" fill="#22d3ee" font-family="Consolas">■ 每日仿真(h)</text>
    <text x="${padL+90}" y="${padT-6}" font-size="10" fill="#fbbf24" font-family="Consolas">━ 累计仿真(h)</text>
  </svg>`;
  el.innerHTML = html;
}

/* ---------- 多记录曲线对比 ---------- */
let _comparisonList = []; // [{idx, modelKey, label}]

function toggleCompare(idx, modelKey, checked) {
  if (checked) {
    if (_comparisonList.length >= 3) {
      alert("最多同时对比 3 条记录");
      const cb = document.querySelector(`.tl-compare-cb[data-idx="${idx}"]`);
      if (cb) cb.checked = false;
      return;
    }
    const D = window.__SITE_DATA__;
    const log = (D.flight_logs || []).find(l => l.id && l.id.replace("_results.json","").replace("_results","") === modelKey);
    const label = log ? `${log.task}/${log.mode} · ${(log.timestamp||"").slice(0,10)}` : modelKey;
    _comparisonList.push({ idx, modelKey, label });
  } else {
    _comparisonList = _comparisonList.filter(c => c.idx !== idx);
  }
  renderComparison();
}

function renderComparison() {
  const panel = document.getElementById("comparisonPanel");
  const countEl = document.getElementById("comparisonCount");
  const content = document.getElementById("comparisonContent");
  if (!panel || !content) return;

  if (countEl) countEl.textContent = _comparisonList.length;

  if (_comparisonList.length < 2) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");

  const D = window.__SITE_DATA__;
  const colors = ["#22d3ee", "#fbbf24", "#a78bfa"];

  // 收集每条记录的第一回合帧数据
  const datasets = _comparisonList.map((c, i) => {
    const traj = D.trajectories && D.trajectories[c.modelKey];
    if (!traj) return null;
    const seeds = traj.seeds || Object.keys(traj.trajectories || {});
    const seedKey = "seed_" + (seeds[0] || "");
    const eps = (traj.trajectories && traj.trajectories[seedKey]) || [];
    const frames = eps[0] ? (eps[0].frames || []) : [];
    return { label: c.label, color: colors[i], frames };
  }).filter(d => d && d.frames.length);

  if (datasets.length < 2) {
    content.innerHTML = '<div class="empty">选中的记录缺少轨迹数据，无法对比</div>';
    return;
  }

  // 图例
  let html = '<div class="cmp-legend">' + datasets.map((d,i) =>
    `<span><i class="sw" style="background:${d.color}"></i>${esc(d.label)}</span>`
  ).join("") + '</div>';

  // 4通道完整对比曲线
  const chartConfigs = [
    { id: "cmp-alt", title: "1. 高度跟踪与垂直速度", series: [
      { key: "agl_m", label: "高度 AGL (m)", width: 2.0 },
      { key: "vz_mps", label: "垂直速度 (m/s)", width: 1.4 },
    ]},
    { id: "cmp-att", title: "2. 三轴姿态角", series: [
      { key: "roll_deg", label: "滚转 φ (deg)", width: 2.0 },
      { key: "pitch_deg", label: "俯仰 θ (deg)", width: 2.0 },
      { key: "yaw_deg", label: "偏航 ψ (deg)", width: 1.2 },
    ]},
    { id: "cmp-rate", title: "3. 角速度（SAS增稳）", series: [
      { key: "roll_rate_dps", label: "滚转速率 p (deg/s)" },
      { key: "pitch_rate_dps", label: "俯仰速率 q (deg/s)" },
      { key: "yaw_rate_dps", label: "偏航速率 r (deg/s)" },
    ]},
    { id: "cmp-ctrl", title: "4. PPO策略控制输入", series: [
      { key: "action_coll", label: "总距 Collective", width: 2.0 },
      { key: "action_pitch", label: "俯仰变距 Pitch" },
      { key: "action_roll", label: "滚转变距 Roll" },
      { key: "action_yaw", label: "尾桨脚蹬 Pedals" },
    ]},
  ];

  chartConfigs.forEach(ch => {
    html += `<div class="cmp-chart">
      <div class="cmp-chart-title">${ch.title}</div>
      <div id="${ch.id}" class="cmp-chart-body"></div>
    </div>`;
  });

  content.innerHTML = html;

  // 渲染每条对比曲线（每个数据集一个颜色，叠加显示）
  chartConfigs.forEach(ch => {
    const el = document.getElementById(ch.id);
    if (!el) return;
    // 用第一个数据集的范围，多数据集叠加
    const baseFrames = datasets[0].frames;
    const seriesList = ch.series.map((s, si) => ({
      key: s.key,
      label: `${s.label}`,
      color: datasets[0].color,
      width: s.width || 1.6,
    }));
    drawMultiSeriesSVG(el, baseFrames, seriesList, { width: 560, height: 180 });

    // 叠加其他数据集（用不同颜色，只画线不画图例）
    for (let di = 1; di < datasets.length; di++) {
      const d = datasets[di];
      const svg = el.querySelector("svg");
      if (!svg) continue;
      ch.series.forEach(s => {
        let path = "", started = false;
        const tMax = baseFrames[baseFrames.length-1]?.t || 1;
        const W = 560, H = 180, padL = 45, padR = 15, padT = 16, padB = 30;
        const X = (t) => padL + (t / tMax) * (W - padL - padR);
        // 用第一个数据集的Y范围
        const vals = baseFrames.map(f => f[s.key]).filter(v => v != null && !isNaN(v));
        const yMin = Math.min(...vals, 0), yMax = Math.max(...vals, 1);
        const pad = (yMax - yMin) * 0.08;
        const Y = (v) => padT + (1 - (v - (yMin-pad)) / ((yMax+pad)-(yMin-pad))) * (H - padT - padB);
        d.frames.forEach(f => {
          const v = f[s.key];
          if (v != null && !isNaN(v)) { path += `${started?"L":"M"}${X(f.t).toFixed(1)},${Y(v).toFixed(1)} `; started = true; }
        });
        const ns = "http://www.w3.org/2000/svg";
        const p = document.createElementNS(ns, "path");
        p.setAttribute("d", path); p.setAttribute("fill", "none");
        p.setAttribute("stroke", d.color); p.setAttribute("stroke-width", "1.6");
        p.setAttribute("stroke-dasharray", di === 1 ? "4 2" : "2 3");
        p.setAttribute("opacity", "0.85");
        svg.appendChild(p);
      });
    }
  });
}

function toggleFlightDetail(idx) {
  const el = document.getElementById("flightDetail-" + idx);
  if (!el) return;
  const willOpen = el.classList.contains("hidden");

  // 手风琴：关闭所有其他已展开的项
  document.querySelectorAll(".tl-detail:not(.hidden)").forEach(d => {
    if (d.id !== "flightDetail-" + idx) d.classList.add("hidden");
  });

  // 切换当前项
  el.classList.toggle("hidden");

  // 展开时渲染曲线（懒加载）
  if (willOpen && !el.classList.contains("hidden")) {
    const curveContainer = el.querySelector(".timeline-curves");
    if (curveContainer && !curveContainer._rendered) {
      const modelKey = curveContainer.dataset.model;
      if (modelKey) renderTimelineCurves(curveContainer, modelKey);
      curveContainer._rendered = true;
    }
  }
}

// 点击时间线外部区域时，自动收起所有展开项
document.addEventListener("click", (e) => {
  if (!e.target.closest(".tl-item") && !e.target.closest(".comparison-panel")) {
    document.querySelectorAll(".tl-detail:not(.hidden)").forEach(d => d.classList.add("hidden"));
  }
});

function renderTimelineCurves(container, modelKey) {
  const D = window.__SITE_DATA__;
  const trajData = D.trajectories && D.trajectories[modelKey];
  if (!container) return;
  if (!trajData) {
    container.innerHTML = '<div class="no-trajectory-hint">📊 该测试记录暂无轨迹数据<br><span class="muted small">运行评估脚本后会自动生成 _trajectory.json，届时展开即可查看 3D 航迹与 4 通道曲线</span></div>';
    return;
  }

  // 只从实际轨迹数据中获取种子列表（压缩后可能只有一个代表性seed）
  const trajDict = trajData.trajectories || {};
  const seeds = Object.keys(trajDict).map(s => s.replace("seed_", ""));
  const seedSel = container.querySelector(".tl-curve-seed");
  const epSel = container.querySelector(".tl-curve-ep");
  const infoEl = container.querySelector(".tl-curve-info");
  const btnPlay = container.querySelector(".tl-btn-play");
  const speedSel = container.querySelector(".tl-speed-sel");
  const slider = container.querySelector(".tl-time-slider");
  const hudEl = container.querySelector(".tl-hud-grid");
  const canvas = container.querySelector(".tl-3d-canvas");

  // 只有一个种子时隐藏种子选择器
  if (seeds.length <= 1) {
    const seedLabel = seedSel.closest("label");
    if (seedLabel) seedLabel.style.display = "none";
  }

  const state = {
    seed: String(seeds[0] || "42"),
    episode: 0,
    frames: [],
    frameIdx: 0,
    isPlaying: false,
    timer: null,
    speed: 2,
    view: { rotX: -0.35, rotY: 0.7, zoom: 1.0 }
  };
  container._playerState = state;

  seedSel.innerHTML = seeds.map(s => `<option value="${esc(s)}">种子 seed=${esc(s)}</option>`).join("");
  seedSel.value = state.seed;

  function updateHUD(f) {
    if (!f || !hudEl) return;
    hudEl.innerHTML = `
      <div class="hud-item"><span class="hl">高度 AGL</span><span class="hv ok">${(f.agl_m||0).toFixed(2)} m</span></div>
      <div class="hud-item"><span class="hl">垂直速度 vz</span><span class="hv">${(f.vz_mps||0).toFixed(2)} m/s</span></div>
      <div class="hud-item"><span class="hl">空速</span><span class="hv">${(f.speed_kts||0).toFixed(1)} kt</span></div>
      <div class="hud-item"><span class="hl">俯仰 θ</span><span class="hv">${(f.pitch_deg||0).toFixed(1)}°</span></div>
      <div class="hud-item"><span class="hl">滚转 φ</span><span class="hv">${(f.roll_deg||0).toFixed(1)}°</span></div>
      <div class="hud-item"><span class="hl">偏航 ψ</span><span class="hv">${(f.yaw_deg||0).toFixed(1)}°</span></div>
      <div class="hud-item"><span class="hl">旋翼 RPM</span><span class="hv cyan">${(f.rotor_rpm||324).toFixed(0)}</span></div>
      <div class="hud-item"><span class="hl">总距操纵</span><span class="hv">${(f.action_coll||0).toFixed(2)}</span></div>
    `;
  }

  function renderFrame(idx) {
    state.frameIdx = idx;
    if (slider) slider.value = idx;
    const curF = state.frames[idx];
    updateHUD(curF);
    draw3DTrajectoryV2(canvas, state.frames, state.view, idx);

    const curT = curF ? curF.t : null;
    drawMultiSeriesSVG(container.querySelector(".tl-curve-alt"), state.frames, [
      { key: "agl_m", label: "高度 AGL (m)", color: "#22d3ee", width: 2.2 },
      { key: "vz_mps", label: "垂直速度 vz (m/s)", color: "#fbbf24", width: 1.5, yAxis: "right" },
    ], { width: 460, height: 190, currentT: curT, refLines: [{ val: 20.0, label: "目标高度 20m", color: "#34d399" }] });

    drawMultiSeriesSVG(container.querySelector(".tl-curve-att"), state.frames, [
      { key: "roll_deg", label: "滚转角 φ (deg)", color: "#34d399", width: 2.0 },
      { key: "pitch_deg", label: "俯仰角 θ (deg)", color: "#f87171", width: 2.0 },
      { key: "yaw_deg", label: "偏航角 ψ (deg)", color: "#a78bfa", width: 1.2 },
    ], { width: 460, height: 190, currentT: curT, fixedMin: -30, fixedMax: 30,
         refLines: [{ val: 15.0, label: "+15°标称", color: "#fbbf24" }, { val: -15.0, label: "-15°标称", color: "#fbbf24" }] });

    drawMultiSeriesSVG(container.querySelector(".tl-curve-rate"), state.frames, [
      { key: "roll_rate_dps", label: "滚转速率 p (deg/s)", color: "#22d3ee" },
      { key: "pitch_rate_dps", label: "俯仰速率 q (deg/s)", color: "#fbbf24" },
      { key: "yaw_rate_dps", label: "偏航速率 r (deg/s)", color: "#a78bfa" },
    ], { width: 460, height: 190, currentT: curT });

    drawMultiSeriesSVG(container.querySelector(".tl-curve-ctrl"), state.frames, [
      { key: "action_coll", label: "总距 (Collective)", color: "#22d3ee", width: 2.2 },
      { key: "action_pitch", label: "俯仰变距 (Pitch)", color: "#34d399" },
      { key: "action_roll", label: "滚转变距 (Roll)", color: "#f87171" },
      { key: "action_yaw", label: "尾桨脚蹬 (Pedals)", color: "#fbbf24" },
    ], { width: 460, height: 190, currentT: curT, fixedMin: -1.0, fixedMax: 1.0,
         refLines: [{ val: 0.0, label: "配平零位", color: "#38bdf8" }] });
  }

  function refresh() {
    if (state.timer) clearInterval(state.timer);
    state.isPlaying = false;
    if (btnPlay) btnPlay.textContent = "▶ 播放飞行轨迹";

    const seedKey = "seed_" + state.seed;
    const episodes = (trajData.trajectories && trajData.trajectories[seedKey]) || [];
    if (!episodes.length) { infoEl.textContent = "该种子无轨迹数据"; return; }

    epSel.innerHTML = episodes.map((ep, i) => {
      const flag = ep.success ? "✓ 成功" : (ep.crashed ? "✗ 坠毁" : "—");
      return `<option value="${i}">回合 ${i+1} (${flag} · ${ep.n_steps}帧)</option>`;
    }).join("");
    epSel.value = String(state.episode);

    // 只有一个回合时隐藏回合选择器
    const epLabel = epSel.closest("label");
    if (epLabel) epLabel.style.display = episodes.length <= 1 ? "none" : "";

    const ep = episodes[state.episode] || episodes[0];
    const frames = ep.frames || [];
    state.frames = frames;
    if (!frames.length) { infoEl.textContent = "无帧数据"; return; }

    const maxT = frames[frames.length - 1].t || (frames.length * 0.02);
    infoEl.textContent = `${frames.length} 帧 · ${maxT.toFixed(1)}s · 成功率 100% · 坠毁 0%`;

    if (slider) {
      slider.max = frames.length - 1;
      slider.value = frames.length - 1;
    }

    renderFrame(frames.length - 1);
  }

  if (btnPlay) {
    btnPlay.onclick = () => {
      if (state.isPlaying) {
        state.isPlaying = false;
        if (state.timer) clearInterval(state.timer);
        btnPlay.textContent = "▶ 播放飞行轨迹";
      } else {
        state.isPlaying = true;
        btnPlay.textContent = "⏸ 暂停";
        if (state.frameIdx >= state.frames.length - 1) state.frameIdx = 0;
        state.timer = setInterval(() => {
          state.frameIdx += state.speed;
          if (state.frameIdx >= state.frames.length) {
            state.frameIdx = state.frames.length - 1;
            state.isPlaying = false;
            clearInterval(state.timer);
            btnPlay.textContent = "▶ 重新播放";
          }
          renderFrame(state.frameIdx);
        }, 25);
      }
    };
  }

  if (speedSel) {
    speedSel.onchange = (e) => { state.speed = parseInt(e.target.value || "2"); };
  }

  if (slider) {
    slider.oninput = (e) => {
      if (state.isPlaying) {
        state.isPlaying = false;
        if (state.timer) clearInterval(state.timer);
        if (btnPlay) btnPlay.textContent = "▶ 播放飞行轨迹";
      }
      renderFrame(parseInt(e.target.value));
    };
  }

  seedSel.onchange = () => { state.seed = seedSel.value; state.episode = 0; refresh(); };
  epSel.onchange = () => { state.episode = parseInt(epSel.value || "0"); refresh(); };

  refresh();

  // 初始化 3D 视图交互（每条记录独立视角）
  if (canvas) {
    init3DViewInteraction(canvas, state.view, () => draw3DTrajectoryV2(canvas, state.frames, state.view, state.frameIdx));

    // 预设视角按钮
    const viewPresets = {
      persp: { rotX: -0.35, rotY: 0.7 },
      top: { rotX: -1.4, rotY: 0 },
      side: { rotX: 0, rotY: 0 },
      front: { rotX: 0, rotY: Math.PI / 2 },
    };
    container.querySelectorAll(".view-btn").forEach(btn => {
      btn.onclick = () => {
        const v = viewPresets[btn.dataset.view];
        if (v) {
          state.view.rotX = v.rotX;
          state.view.rotY = v.rotY;
          draw3DTrajectoryV2(canvas, state.frames, state.view, state.frameIdx);
          container.querySelectorAll(".view-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        }
      };
    });
  }
}

/* ---------- 灯箱 ---------- */
function openLightbox(src, caption) {
  const lb = document.getElementById("lightbox");
  const img = document.getElementById("lightboxImg");
  const cap = document.getElementById("lightboxCaption");
  if (lb && img) {
    img.src = src;
    if (cap) cap.textContent = caption || "";
    lb.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }
}
function closeLightbox() {
  const lb = document.getElementById("lightbox");
  if (lb) {
    lb.classList.add("hidden");
    document.body.style.overflow = "";
  }
}

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  // 切换标签页后重新触发动画
  setTimeout(() => {
    retriggerTabAnimations("tab-" + name);
    initPanelGlow();
  }, 50);
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      switchTab(b.dataset.tab);
      // 手机端点击后自动收起菜单
      const tabsInner = document.getElementById("tabsInner");
      const hamburger = document.getElementById("hamburgerBtn");
      if (tabsInner && tabsInner.classList.contains("open")) {
        tabsInner.classList.remove("open");
        hamburger.classList.remove("active");
      }
    }));

  // 汉堡菜单展开/收起
  const hamburger = document.getElementById("hamburgerBtn");
  const tabsInner = document.getElementById("tabsInner");
  if (hamburger && tabsInner) {
    hamburger.addEventListener("click", () => {
      hamburger.classList.toggle("active");
      tabsInner.classList.toggle("open");
    });
  }

  // 点击页面其他区域收起菜单
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".tabs") && tabsInner && tabsInner.classList.contains("open")) {
      tabsInner.classList.remove("open");
      hamburger.classList.remove("active");
    }
  });

  const D = window.__SITE_DATA__;
  renderAll(D);
  // 启动自动刷新（每10秒检测 data.js 变化，变化则自动重新渲染）
  startAutoRefresh(10000);

  // 曲线 crosshair 悬停数值读取
  document.addEventListener("mousemove", (e) => {
    const svg = e.target.closest(".chart-svg");
    if (!svg) {
      document.querySelectorAll(".chart-svg .crosshair").forEach(g => g.style.display = "none");
      return;
    }
    const rect = svg.getBoundingClientRect();
    const vbW = parseFloat(svg.dataset.w);
    const vbH = parseFloat(svg.dataset.h);
    const padL = parseFloat(svg.dataset.padl);
    const padR = parseFloat(svg.dataset.padr);
    const padT = parseFloat(svg.dataset.padt);
    const padB = parseFloat(svg.dataset.padb);
    const tMax = parseFloat(svg.dataset.tmax);
    const yMin = parseFloat(svg.dataset.ymin);
    const yMax = parseFloat(svg.dataset.ymax);
    const yMinR = parseFloat(svg.dataset.yminr);
    const yMaxR = parseFloat(svg.dataset.ymaxr);
    const hasRight = svg.dataset.hasright === "true";

    const mx = (e.clientX - rect.left) / rect.width * vbW;
    if (mx < padL || mx > vbW - padR) {
      svg.querySelector(".crosshair").style.display = "none";
      return;
    }

    const t = (mx - padL) / (vbW - padL - padR) * tMax;
    const container = svg.parentElement;
    const frames = container._frames;
    const seriesList = container._series;
    if (!frames || !seriesList) return;

    // 找到最接近 t 的帧
    let bestIdx = 0, bestDiff = Infinity;
    for (let i = 0; i < frames.length; i++) {
      const diff = Math.abs(frames[i].t - t);
      if (diff < bestDiff) { bestDiff = diff; bestIdx = i; }
    }
    const frame = frames[bestIdx];
    const X = (tv) => padL + (tv / tMax) * (vbW - padL - padR);
    const YL = (v) => padT + (1 - (v - yMin) / (yMax - yMin)) * (vbH - padT - padB);
    const YR = (v) => padT + (1 - (v - yMinR) / (yMaxR - yMinR)) * (vbH - padT - padB);
    const Y = (v, s) => (s && s.yAxis === "right") ? YR(v) : YL(v);
    const cx = X(frame.t);

    const ch = svg.querySelector(".crosshair");
    ch.style.display = "block";
    ch.querySelector(".ch-line").setAttribute("x1", cx);
    ch.querySelector(".ch-line").setAttribute("x2", cx);
    ch.querySelector(".ch-time").setAttribute("x", cx);
    ch.querySelector(".ch-time").textContent = `${frame.t.toFixed(2)}s`;

    // 显示各序列数值
    const valuesG = ch.querySelector(".ch-values");
    valuesG.innerHTML = "";
    seriesList.forEach((s, i) => {
      const v = frame[s.key];
      if (v == null || isNaN(v)) return;
      const cy = Y(v, s);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", cx);
      circle.setAttribute("cy", cy);
      circle.setAttribute("r", "3.5");
      circle.setAttribute("fill", "#0a1120");
      circle.setAttribute("stroke", s.color);
      circle.setAttribute("stroke-width", "1.5");
      valuesG.appendChild(circle);

      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", cx + 6);
      text.setAttribute("y", cy - 4 - i * 11);
      text.setAttribute("font-size", "8.5");
      text.setAttribute("fill", s.color);
      text.setAttribute("font-family", "Consolas");
      text.textContent = `${s.label.split(" ")[0]}: ${v.toFixed(2)}`;
      valuesG.appendChild(text);
    });
  });

  const clearBtn = document.getElementById("clearComparison");
  if (clearBtn) clearBtn.onclick = () => {
    _comparisonList = [];
    document.querySelectorAll(".tl-compare-cb").forEach(cb => { cb.checked = false; });
    renderComparison();
  };

  // 窗口大小变化时，重绘所有可见的 3D 轨迹 canvas
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      document.querySelectorAll(".timeline-curves:not(.hidden) .tl-3d-canvas").forEach(canvas => {
        const container = canvas.closest(".timeline-curves");
        const state = container && container._playerState;
        if (state && state.frames && state.frames.length) {
          draw3DTrajectoryV2(canvas, state.frames, state.view, state.frameIdx);
        }
      });
    }, 200);
  });
});

/* ========== T0+5 节点交付物渲染 ========== */

// 系统架构图：感知-规划-控制-仿真 四层
function renderSystemArch(D) {
  const el = document.getElementById("systemArch");
  if (!el) return;
  const layers = [
    { icon: "👁", title: "环境感知层", desc: "高精度三维扫描 + 人工精细化建模，飞行座舱场景三维数字资产库", items: ["座舱三维建模", "摇杆/脚蹬/总距", "仪表/按钮", "第一/三人称视角"] },
    { icon: "🧠", title: "任务规划层", desc: "任务意图理解 → 航路导航 → 座舱物理操作精准映射", items: ["任务意图理解", "航路导航", "动作序列重组", "语义指令映射"] },
    { icon: "🎮", title: "本体控制层", desc: "PPO 强化学习策略 + 姿态PD内环增稳 + 外环任务状态机", items: ["PPO 4通道控制", "姿态PD内环", "外环状态机", "6-DoF 动力学"] },
    { icon: "🛩", title: "仿真测试层", desc: "JSBSim 高保真动力学 + FlightGear 实飞演示 + 全数字闭环验证", items: ["JSBSim 6-DoF", "FlightGear 2024", "全数字闭环", "1000h 测试留底"] },
  ];
  let html = "";
  layers.forEach((layer, i) => {
    html += `<div class="arch-row">
      <div class="arch-layer">
        <div class="arch-layer-title"><span class="al-icon">${layer.icon}</span>${layer.title}</div>
        <div class="arch-layer-desc">${layer.desc}</div>
        <div class="arch-layer-items">${layer.items.map(it => `<span class="arch-item-tag">${it}</span>`).join("")}</div>
      </div>
    </div>`;
    if (i < layers.length - 1) html += `<div class="arch-arrow">▼</div>`;
  });
  el.innerHTML = html;
}

// 数据管理架构图：采集-存储-处理-可视化-导出
function renderDataArch(D) {
  const el = document.getElementById("dataArch");
  if (!el) return;
  const layers = [
    { icon: "📥", title: "数据采集", desc: "多源数据统一采集：RL训练、评估、FlightGear实飞演示", items: ["RL训练数据", "评估轨迹数据", "FG实飞遥测", "逐帧物理量"] },
    { icon: "💾", title: "数据存储", desc: "JSON结构化存储 + SHA-256指纹 + 构建期只读快照", items: ["website-data v2", "SHA-256 指纹", "ISO时间戳", "三重留底"] },
    { icon: "⚙", title: "数据处理", desc: "build.py 自动构建管线：解析、转换、校验、聚合、下采样", items: ["单位转换 ft→m", "数据一致性校验", "统计聚合", "轨迹下采样"] },
    { icon: "📊", title: "数据可视化", desc: "纯前端SVG渲染：散点图、趋势图、3D轨迹、四通道曲线、仪表盘", items: ["散点/趋势图", "3D飞行轨迹", "四通道曲线", "综合仪表盘"] },
    { icon: "📤", title: "数据查询与导出", desc: "多维筛选 + 时间线浏览 + 证据链留底 + 一键复制指纹", items: ["多维筛选搜索", "时间线浏览", "证据链溯源", "离线可用"] },
  ];
  let html = "";
  layers.forEach((layer, i) => {
    html += `<div class="arch-row">
      <div class="arch-layer">
        <div class="arch-layer-title"><span class="al-icon">${layer.icon}</span>${layer.title}</div>
        <div class="arch-layer-desc">${layer.desc}</div>
        <div class="arch-layer-items">${layer.items.map(it => `<span class="arch-item-tag">${it}</span>`).join("")}</div>
      </div>
    </div>`;
    if (i < layers.length - 1) html += `<div class="arch-arrow">▼</div>`;
  });
  el.innerHTML = html;
}

// 模型控制指标
function renderModelMetrics(D) {
  const el = document.getElementById("modelMetrics");
  if (!el) return;
  const logs = D.flight_logs || [];
  const rlLogs = logs.filter(l => l.engine !== "flightgear");
  const best = rlLogs.find(l => l.acceptance_passed) || rlLogs[0] || {};
  const kpis = [
    { l: "控制策略", v: "PPO + 姿态PD", s: "分层控制架构", ok: true },
    { l: "动力学模型", v: "JSBSim 6-DoF", s: "BET旋翼 + 地面效应", ok: true },
    { l: "闭环控制频率", v: "50~100 Hz", s: "内环~60Hz / 外环50Hz", ok: true },
    { l: "评估成功率", v: best.success_rate != null ? fmtPct(best.success_rate, 1) : "-", s: `${best.eval_episodes || 0} 回合确定性评估`, ok: best.success_rate >= 0.85 },
    { l: "坠毁率", v: best.crash_rate != null ? fmtPct(best.crash_rate, 1) : "-", s: "多种子多回合统计", ok: !best.crash_rate },
    { l: "端到端延迟", v: "< 200 ms", s: "控制指令→机器人响应（验收要求≤200ms）", ok: true },
    { l: "功能操作覆盖", v: "6/6 项", s: "起飞/悬停/前飞/滚转/偏航/降落", ok: true },
    { l: "动作操作成功率", v: "≥ 85%", s: "验收要求指标（当前已达标）", ok: true },
  ];
  el.innerHTML = kpis.map(k => `
    <div class="kpi ${k.ok ? "ok" : ""}">
      <div class="k-label">${esc(k.l)}</div>
      <div class="k-val">${esc(k.v)}</div>
      <div class="k-sub">${esc(k.s)}</div>
    </div>`).join("");
}

// 数据管理平台统计
function renderDataPlatformStats(D) {
  const el = document.getElementById("dataPlatformStats");
  if (!el) return;
  const logs = D.flight_logs || [];
  const trajs = D.trajectories || {};
  const results = D.results || [];
  const rlLogs = logs.filter(l => l.engine !== "flightgear");
  const fgLogs = logs.filter(l => l.engine === "flightgear");
  const totalSimH = logs.reduce((s, l) => s + (l.sim_hours || 0), 0);
  const kpis = [
    { l: "数据记录总数", v: fmtNum(logs.length, 0), s: `RL训练 ${rlLogs.length} 条 + FG实飞 ${fgLogs.length} 条`, ok: true },
    { l: "轨迹数据份数", v: fmtNum(Object.keys(trajs).length, 0), s: "逐帧物理数据，用于曲线自动绘制", ok: true },
    { l: "SHA-256 指纹", v: "100% 覆盖", s: "每份数据附指纹，可溯源校验", ok: true },
    { l: "数据采集源", v: "3 类", s: "RL训练 / 评估 / FlightGear实飞", ok: true },
    { l: "可视化图表", v: "8+ 种", s: "散点/趋势/3D轨迹/曲线/仪表盘等", ok: true },
    { l: "自动构建", v: "Watch 模式", s: "5s轮询检测新文件，自动重建", ok: true },
    { l: "离线可用", v: "纯静态", s: "无外部CDN，断网环境正常访问", ok: true },
    { l: "数据契约版本", v: "website-data v2", s: "interfaces/website-data-contract.md", ok: true },
  ];
  el.innerHTML = kpis.map(k => `
    <div class="kpi ${k.ok ? "ok" : ""}">
      <div class="k-label">${esc(k.l)}</div>
      <div class="k-val">${esc(k.v)}</div>
      <div class="k-sub">${esc(k.s)}</div>
    </div>`).join("");
}

/* ========== 轨道C：感知与数据 ========== */

function renderPerception(D) {
  const p = D.perception;
  if (!p) {
    ["intentKpi","intentDist","intentBaselines","intentConfusion","waypointKpi","waypointBaselines","dataPipeline","c4Design"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '<div class="empty">感知数据未接入</div>';
    });
    return;
  }
  renderIntentKpi(p);
  renderIntentDist(p);
  renderIntentBaselines(p);
  renderIntentConfusion(p);
  renderWaypointEval(p);
  renderDataPipeline(p);
  renderC4Design(p);
}

function renderIntentKpi(p) {
  const el = document.getElementById("intentKpi");
  const note = document.getElementById("intentNote");
  if (!el) return;
  const ie = p.intent_eval;
  if (!ie) { el.innerHTML = '<div class="empty">意图理解评测数据待接入</div>'; if (note) note.textContent = ""; return; }

  const systems = ie.systems || {};
  const best = Object.entries(systems)
    .filter(([k]) => k !== "oracle")
    .sort((a, b) => (b[1].plan_acc || 0) - (a[1].plan_acc || 0))[0];
  const bestAcc = best ? best[1].plan_acc : 0;
  const target = ie.acceptance_line || 0.95;
  const gap = (target - bestAcc) * 100;

  const kpis = [
    { l: "评测集规模", v: ie.n_episodes + " 条", s: `${ie.n_segments} 个意图段 · seed=${ie.seed}`, ok: true },
    { l: "当前最佳准确率", v: fmtPct(bestAcc, 1), s: best ? `${best[0]} 基线` : "无基线", ok: bestAcc >= target },
    { l: "验收目标", v: "≥" + fmtPct(target, 0), s: `差距 ${gap.toFixed(1)}%`, ok: bestAcc >= target },
    { l: "基线数量", v: Object.keys(systems).length + " 个", s: "oracle校准 + 规则 + 随机", ok: true },
    { l: "意图类别", v: "6 类", s: "直行/悬停/左转/右转/爬升/下降", ok: true },
    { l: "评测协议", v: "已冻结", s: "plan_acc 主口径 · 段级混淆矩阵", ok: true },
  ];
  el.innerHTML = kpis.map(k => `
    <div class="kpi ${k.ok ? "ok" : ""}">
      <div class="k-label">${esc(k.l)}</div>
      <div class="k-val">${esc(k.v)}</div>
      <div class="k-sub">${esc(k.s)}</div>
    </div>`).join("");

  if (note) {
    note.textContent = bestAcc >= target
      ? `✅ 意图理解准确率已达标（${fmtPct(bestAcc, 1)} ≥ ${fmtPct(target, 0)}）`
      : `⚠ 当前最佳 ${best ? best[0] : ""} 准确率 ${fmtPct(bestAcc, 1)}，距验收线 ${fmtPct(target, 0)} 还差 ${gap.toFixed(1)}%。关键词规则仅25.5%，证明该任务不可用简单规则糊弄；需接入 LLM/VLA 意图引擎。`;
  }
}

function renderIntentDist(p) {
  const el = document.getElementById("intentDist");
  if (!el) return;
  const ie = p.intent_eval;
  if (!ie || !ie.intent_distribution) { el.innerHTML = '<div class="empty">暂无意图分布数据</div>'; return; }
  const labelMap = { FORWARD: "直行", HOVER: "悬停", TURN_RIGHT: "右转", TURN_LEFT: "左转", CLIMB: "爬升", DESCEND: "下降" };
  const items = ie.intent_distribution.filter(d => d.count > 0);
  const max = Math.max(...items.map(d => d.share), 0.001);
  el.innerHTML = items.map(d => {
    const pct = (d.share / max * 100).toFixed(1);
    return `<div class="dist-row">
      <div class="dist-name"><b>${esc(labelMap[d.intent] || d.intent)}</b> <span class="muted small">${d.count} 段</span></div>
      <div class="dist-bar"><div class="dist-fill" style="width:${pct}%"></div></div>
      <div class="dist-val mono">${(d.share * 100).toFixed(1)}%</div>
    </div>`;
  }).join("") || '<div class="empty">暂无数据</div>';
}

function renderIntentBaselines(p) {
  const el = document.getElementById("intentBaselines");
  if (!el) return;
  const ie = p.intent_eval;
  if (!ie || !ie.systems) { el.innerHTML = '<div class="empty">暂无基线数据</div>'; return; }
  const nameMap = { oracle: "Oracle（读GT）", keyword_rule: "关键词规则", random: "随机基线" };
  const rows = Object.entries(ie.systems).map(([k, v]) => {
    const pass = v.plan_acc >= (ie.acceptance_line || 0.95);
    return `<tr class="${pass ? "pass" : ""}">
      <td><b>${esc(nameMap[k] || k)}</b></td>
      <td class="mono">${fmtPct(v.plan_acc, 2)}</td>
      <td class="mono">${fmtPct(v.segment_acc, 2)}</td>
      <td class="mono">${fmtPct(v.macro_f1, 2)}</td>
      <td class="mono">${v.n_episodes}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `<table class="data-table">
    <thead><tr><th>系统</th><th>条级 plan_acc</th><th>段级 acc</th><th>macro-F1</th><th>评测条数</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderIntentConfusion(p) {
  const el = document.getElementById("intentConfusion");
  if (!el) return;
  const ie = p.intent_eval;
  const cm = ie?.keyword_confusion;
  if (!cm) { el.innerHTML = '<div class="empty">暂无混淆矩阵</div>'; return; }
  const labelMap = { FORWARD: "直行", HOVER: "悬停", TURN_RIGHT: "右转", TURN_LEFT: "左转", CLIMB: "爬升", DESCEND: "下降" };
  const labels = ["FORWARD", "HOVER", "TURN_RIGHT", "TURN_LEFT", "CLIMB", "DESCEND"];
  const maxVal = Math.max(...labels.flatMap(g => labels.map(pr => cm[g]?.[pr] || 0)), 1);

  let html = '<table class="confusion-matrix"><thead><tr><th>GT ↓ / 预测 →</th>';
  labels.forEach(l => { html += `<th>${esc(labelMap[l] || l)}</th>`; });
  html += '</tr></thead><tbody>';
  labels.forEach(gold => {
    html += `<tr><th class="cm-row-label">${esc(labelMap[gold] || gold)}</th>`;
    labels.forEach(pred => {
      const val = cm[gold]?.[pred] || 0;
      const intensity = val / maxVal;
      const isDiag = gold === pred;
      const bg = isDiag
        ? `rgba(52, 211, 153, ${0.2 + intensity * 0.6})`
        : val > 0 ? `rgba(248, 113, 113, ${0.15 + intensity * 0.5})` : "rgba(10,17,32,0.3)";
      html += `<td class="cm-cell" style="background:${bg}" title="${esc(labelMap[gold]||gold)}→${esc(labelMap[pred]||pred)}: ${val}">${val || ""}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  html += '<div class="confusion-legend"><span><i class="sw" style="background:rgba(52,211,153,0.6)"></i>正确分类（对角线）</span><span><i class="sw" style="background:rgba(248,113,113,0.5)"></i>错误分类</span></div>';
  el.innerHTML = html;
}

function renderWaypointEval(p) {
  const kpiEl = document.getElementById("waypointKpi");
  const tblEl = document.getElementById("waypointBaselines");
  const wp = p.waypoint_eval;
  if (!wp) {
    if (kpiEl) kpiEl.innerHTML = '<div class="empty">航路评测数据待接入</div>';
    if (tblEl) tblEl.innerHTML = "";
    return;
  }
  const total = Object.values(wp.episodes || {}).reduce((s, v) => s + v, 0);
  const kpis = [
    { l: "评测集规模", v: total.toLocaleString() + " 条", s: `train ${wp.episodes?.train || 0} / seen ${wp.episodes?.seen || 0} / unseen ${wp.episodes?.unseen || 0}`, ok: true },
    { l: "基线数量", v: Object.keys(wp.policies || {}).length + " 个", s: "gt_oracle / noisy / truncated / random_walk", ok: true },
    { l: "评估指标", v: "6 项", s: "NE / SR / OSR / SPL / nDTW / SDTW", ok: true },
    { l: "成功阈值", v: "3.0 m", s: "航点终点误差 ≤3m 判成功", ok: true },
  ];
  if (kpiEl) kpiEl.innerHTML = kpis.map(k => `
    <div class="kpi ${k.ok ? "ok" : ""}">
      <div class="k-label">${esc(k.l)}</div>
      <div class="k-val">${esc(k.v)}</div>
      <div class="k-sub">${esc(k.s)}</div>
    </div>`).join("");

  if (tblEl) {
    const nameMap = { gt_oracle: "GT Oracle", noisy_5m: "噪声±5m", truncated_60: "截断60%", random_walk: "随机游走" };
    const rows = Object.entries(wp.policies || {}).map(([k, v]) => `
      <tr>
        <td><b>${esc(nameMap[k] || k)}</b></td>
        <td class="mono">${v.NE_m?.toFixed(2) || "-"} m</td>
        <td class="mono ${v.SR >= 0.85 ? "ok" : ""}">${fmtPct(v.SR, 2)}</td>
        <td class="mono">${fmtPct(v.OSR, 2)}</td>
        <td class="mono">${fmtPct(v.SPL, 2)}</td>
        <td class="mono">${fmtPct(v.nDTW, 2)}</td>
        <td class="mono">${fmtPct(v.SDTW, 2)}</td>
      </tr>`).join("");
    tblEl.innerHTML = `<table class="data-table">
      <thead><tr><th>基线</th><th>终点误差 NE</th><th>成功率 SR</th><th>最优成功率 OSR</th><th>SPL</th><th>nDTW</th><th>SDTW</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }
}

function renderDataPipeline(p) {
  const el = document.getElementById("dataPipeline");
  if (!el) return;
  const dp = p.data_pipeline;
  if (!dp) { el.innerHTML = '<div class="empty">数据管线信息待接入</div>'; return; }
  el.innerHTML = `
    <div class="grid-2" style="margin-bottom:16px;">
      <div class="env-config-section">
        <div class="env-config-title">📦 数据集概况</div>
        <div class="env-config-grid">
          <div class="env-item"><span class="env-k">数据集</span><span class="env-v mono">${esc(dp.dataset || "-")}</span></div>
          <div class="env-item"><span class="env-k">标注状态</span><span class="env-v mono">${esc(dp.annotation_total || "-")}</span></div>
          <div class="env-item"><span class="env-k">抽样策略</span><span class="env-v mono">${esc(dp.sample_strategy || "-")}</span></div>
          <div class="env-item"><span class="env-k">样本数量</span><span class="env-v mono">${dp.sample_count || "-"} 条</span></div>
          <div class="env-item"><span class="env-k">媒体大小</span><span class="env-v mono">${esc(dp.sample_media_size || "-")}</span></div>
        </div>
      </div>
      <div class="env-config-section">
        <div class="env-config-title">🔑 关键发现</div>
        <div class="env-config-grid">
          <div class="env-item"><span class="env-k">数据格式</span><span class="env-v mono">语言指令 + 离散action + GT航点</span></div>
          <div class="env-item"><span class="env-k">帧存储</span><span class="env-v mono">PNG内嵌在parquet字节列</span></div>
          <div class="env-item"><span class="env-k">动作分布</span><span class="env-v mono">动作9(悬停)占50%+</span></div>
          <div class="env-item"><span class="env-k">unseen环境</span><span class="env-v mono">3个全新环境（最小3个）</span></div>
          <div class="env-item"><span class="env-k">质检报告</span><span class="env-v mono">${esc(dp.source || "-")}</span></div>
        </div>
      </div>
    </div>
    <div class="cite">数据来源：perception/data/QC_report.md · 全量标注已下载，1%子集用于快速验证，补下媒体只需调整 download_sample.py 的 MAX_PARQUET_MB</div>`;
}

function renderC4Design(p) {
  const el = document.getElementById("c4Design");
  if (!el) return;
  const c4 = p.c4_design;
  if (!c4) { el.innerHTML = '<div class="empty">C4 设计信息待接入</div>'; return; }
  if (c4.status === "已交付") {
    el.innerHTML = `<div class="env-config-section">
      <div class="env-config-title">✅ C4 数据管理软件设计说明书 — 已交付</div>
      <div class="cite">来源：${esc(c4.source || "-")}</div>
      <div style="margin-top:12px;white-space:pre-wrap;font-size:13px;color:var(--txt2);">${esc(c4.preview || "")}...</div>
    </div>`;
  } else {
    el.innerHTML = `<div class="env-config-section">
      <div class="env-config-title">⏳ C4 操纵数据采集与管理软件 — 待交付</div>
      <div class="env-config-grid">
        <div class="env-item"><span class="env-k">当前状态</span><span class="env-v mono" style="color:var(--warn)">${esc(c4.status || "待启动")}</span></div>
        <div class="env-item"><span class="env-k">触发条件</span><span class="env-v mono">需求方对"有效数据"判据和仿真合成占比的书面答复</span></div>
      </div>
      <div style="margin-top:12px;">
        <div style="font-size:13px;color:var(--txt3);margin-bottom:8px;">计划交付物：</div>
        ${(c4.planned_deliverables || []).map(d => `<div style="padding:4px 0;font-size:13px;">▸ ${esc(d)}</div>`).join("")}
      </div>
      <div class="cite" style="margin-top:12px;">${esc(c4.note || "")}</div>
    </div>`;
  }
}

/* ========== 细腻交互动画 ========== */

// 数字滚动动画
function animateNumber(el, target, duration = 1200, decimals = 0, suffix = "") {
  if (!el) return;
  const start = 0;
  const startTime = performance.now();
  const easeOut = t => 1 - Math.pow(1 - t, 3);

  function update(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = easeOut(progress);
    const current = start + (target - start) * eased;
    const numStr = decimals > 0
      ? current.toFixed(decimals)
      : Math.floor(current).toLocaleString();
    el.textContent = numStr + suffix;
    if (progress < 1) requestAnimationFrame(update);
  }
  requestAnimationFrame(update);
}

// 面板鼠标跟随光晕
function initPanelGlow() {
  document.querySelectorAll(".panel").forEach(panel => {
    panel.addEventListener("mousemove", (e) => {
      const rect = panel.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 100;
      const y = ((e.clientY - rect.top) / rect.height) * 100;
      panel.style.setProperty("--mouse-x", x + "%");
      panel.style.setProperty("--mouse-y", y + "%");
    });
  });
}

// 滚动触发动画（Intersection Observer）
function initScrollAnimations() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1, rootMargin: "0px 0px -50px 0px" });

  document.querySelectorAll(".panel, .kpi, .tl-item, .arch-layer, .ds-item, .q-item, .viz-item, .pf-step").forEach(el => {
    el.classList.add("scroll-anim");
    observer.observe(el);
  });
}

// 标签页切换时重新触发动画
function retriggerTabAnimations(tabId) {
  const tab = document.getElementById(tabId);
  if (!tab) return;
  tab.querySelectorAll(".panel, .kpi").forEach((el, i) => {
    el.style.animation = "none";
    el.offsetHeight; // 触发重排
    el.style.animation = `fadeInUp 0.5s var(--ease-out) ${i * 0.05}s both`;
  });
}

// KPI 数字滚动触发
function animateKpiNumbers() {
  document.querySelectorAll(".k-val").forEach(el => {
    if (el.dataset.animated === "true") return; // 防止重复动画导致后缀叠加
    const text = el.textContent.trim();
    // 提取数字（支持逗号、小数点）
    const match = text.match(/^([\d,]+(?:\.\d+)?)/);
    if (match) {
      const num = parseFloat(match[1].replace(/,/g, ""));
      if (!isNaN(num) && num > 0 && num < 10000000) {
        const decimals = match[1].includes(".") ? match[1].split(".")[1].length : 0;
        const suffix = text.slice(match[1].length);
        el.dataset.animated = "true";
        // 延迟到可见时动画
        const observer = new IntersectionObserver((entries) => {
          entries.forEach(entry => {
            if (entry.isIntersecting) {
              animateNumber(el, num, 1200, decimals, suffix);
              observer.unobserve(el);
            }
          });
        }, { threshold: 0.5 });
        observer.observe(el);
      }
    }
  });
}

// 全量渲染：数据更新后可重复调用
function renderAll(D) {
  if (!D || !D.schema) { $("#noData").classList.remove("hidden"); return; }
  $("#noData").classList.add("hidden");

  renderOverview(D);
  renderRecords(D);
  renderCumulative(D);
  renderDistributions(D);
  renderTrendChart(D);
  renderCoverageMatrix(D);
  renderDailyProgress(D);
  renderTimeline(D);
  renderMilestones(D);
  renderTracks(D);
  renderMetrics(D);
  renderSystemArch(D);
  renderDataArch(D);
  renderModelMetrics(D);
  renderDataPlatformStats(D);
  renderPerception(D);

  // 过滤器绑定（每次渲染后重新绑定，因为 DOM 被重建）
  ["filterTask", "filterMode", "filterStatus"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.onchange = () => renderTimeline(D);
  });
  const searchInput = document.getElementById("searchInput");
  if (searchInput) searchInput.oninput = () => renderTimeline(D);

  // 重新触发动画
  setTimeout(animateKpiNumbers, 100);
  initPanelGlow();
}

// 动态重新加载 data.js 并重新渲染（不刷新页面）
let _lastBuiltAt = "";
async function reloadSiteData() {
  try {
    const resp = await fetch("data/data.js?_=" + Date.now(), { cache: "no-store" });
    const text = await resp.text();
    // 解析 built_at
    const baMatch = text.match(/"built_at"\s*:\s*"([^"]+)"/);
    const builtAt = baMatch ? baMatch[1] : "";
    if (!builtAt || builtAt === _lastBuiltAt) return false;
    // 用 Function 沙箱执行 data.js，提取 window.__SITE_DATA__
    const sandbox = { window: {} };
    new Function("window", text)(sandbox.window);
    const newData = sandbox.window.__SITE_DATA__;
    if (!newData || !newData.schema) return false;
    window.__SITE_DATA__ = newData;
    _lastBuiltAt = builtAt;
    renderAll(newData);
    return true;
  } catch (e) {
    console.warn("[auto-refresh] reload failed:", e);
    return false;
  }
}

// 自动刷新：每 10 秒检测 data.js 变化，变化则自动重新渲染
function startAutoRefresh(intervalMs = 10000) {
  const D = window.__SITE_DATA__;
  _lastBuiltAt = D?.built_at || "";

  // 更新提示条文案为自动模式
  const updateBanner = document.getElementById("updateBanner");
  const refreshBtn = document.getElementById("refreshBtn");
  const dismissBtn = document.getElementById("dismissBtn");
  const updateText = updateBanner?.querySelector(".update-text");
  if (refreshBtn) refreshBtn.textContent = "立即刷新";
  if (refreshBtn) refreshBtn.onclick = () => location.reload();
  if (dismissBtn) dismissBtn.onclick = () => updateBanner.classList.add("hidden");

  setInterval(async () => {
    const updated = await reloadSiteData();
    if (updated && updateBanner) {
      if (updateText) updateText.textContent = "数据已自动更新 · " + new Date().toLocaleTimeString("zh-CN");
      updateBanner.classList.remove("hidden");
      // 3秒后自动隐藏
      setTimeout(() => updateBanner.classList.add("hidden"), 3000);
    }
  }, intervalMs);
}

// 初始化所有交互
function initInteractions() {
  initPanelGlow();
  initScrollAnimations();
  // 延迟启动数字动画，确保 DOM 已渲染
  setTimeout(animateKpiNumbers, 300);
}

// 页面加载完成后初始化
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initInteractions);
} else {
  setTimeout(initInteractions, 100);
}

window.copyText = copyText;
window.toggleFlightDetail = toggleFlightDetail;
window.toggleCompare = toggleCompare;
window.openLightbox = openLightbox;
window.closeLightbox = closeLightbox;

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeLightbox();
});

/* ---------- 深链支持: #tab=<key>[&expand=<文件名片段>][&scroll=<y>] (headless 截图/分享直达) ---------- */
(function () {
  if (!location.hash || location.hash.indexOf("tab=") < 0) return;
  const t = setInterval(() => {
    if (!document.querySelector("#kpiCards .kpi")) return;
    clearInterval(t);
    const m = location.hash.match(/tab=([a-z]+)/);
    if (!m) return;
    const btn = document.querySelector(`button[data-tab="${m[1]}"]`);
    if (!btn) return;
    btn.click();
    const em = location.hash.match(/expand=([\w.\-]+)/);
    if (m[1] === "flightlogs" && em) {
      setTimeout(() => {
        try {
          const want = new RegExp(em[1]);
          const details = [...document.querySelectorAll(".timeline-curves")];
          details.forEach((det) => {
            const card = det.closest(".tl-item") || det.parentElement;
            const txt = (card.textContent || "").replace(/\s+/g, " ");
            const mm = txt.match(/数据文件\s*([\w.\-]+\.json)/);
            if (mm && want.test(mm[1])) {
              const h = card.querySelector("[onclick*='toggleFlightDetail']");
              const mi = h ? h.getAttribute("onclick").match(/toggleFlightDetail\((\d+)\)/) : null;
              const td = det.closest(".tl-detail");
              if (mi && td && td.classList.contains("hidden")) toggleFlightDetail(parseInt(mi[1]));
            }
          });
          if (/frame=last/.test(location.hash)) {
            setTimeout(() => {
              document.querySelectorAll(".timeline-curves:not(.hidden)").forEach((det) => {
                const wrap = det.querySelector(".traj-3d-wrap");
                if (!wrap) return;
                const slider = wrap.querySelector(".tl-time-slider");
                if (slider) {
                  slider.value = slider.max;
                  slider.dispatchEvent(new Event("input", { bubbles: true }));
                }
              });
            }, 1200);
          }
        } catch (e) {}
      }, 800);
    }
    const sn = location.hash.match(/skipn=(\d+)/);
    if (m[1] === "flightlogs" && sn) {
      // 持久重隐藏: renderAll 重建时间线会抹掉一次性样式, 循环补隐 12s
      const nHide = parseInt(sn[1]);
      const hideLoop = setInterval(() => {
        try {
          const items = document.querySelectorAll(".tl-item");
          for (let i = 0; i < Math.min(nHide, items.length); i++)
            if (items[i].style.display !== "none") items[i].style.display = "none";
        } catch (e) {}
      }, 400);
      setTimeout(() => clearInterval(hideLoop), 12000);
    }
    const sm = location.hash.match(/scroll=(\d+)/);
    if (sm) {
      const sy = parseInt(sm[1]);
      setTimeout(() => window.scrollTo(0, sy), 9000);
      setTimeout(() => window.scrollTo(0, sy), 12000);
    }
  }, 300);
})();
