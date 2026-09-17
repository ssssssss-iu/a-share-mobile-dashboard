const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]));
const pctClass = value => value > 0 ? "up" : value < 0 ? "down" : "flat";
const fmtPct = value => Number.isFinite(value) ? `${value > 0 ? "+" : ""}${value.toFixed(2)}%` : "—";
const fmtAmount = value => !Number.isFinite(value) ? "—" : value >= 1e12 ? `${(value / 1e12).toFixed(2)}万亿` : `${(value / 1e8).toFixed(1)}亿`;
const fmtTime = value => {
  if (!value) return "时间未知";
  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false, timeZone:"Asia/Shanghai"}).format(date);
};
const stateClass = state => state === "通过" ? "pass" : state === "不通过" ? "fail" : "insufficient";

let DATA = null;
let FILTER = "all";
let LOAD_IN_FLIGHT = false;
const AUTO_REFRESH_MS = 60 * 1000;
const UPDATE_POINTS = ["09:25","09:40","09:55","10:10","10:25","10:40","10:55","11:10","11:25","13:05","13:20","13:35","13:50","14:05","14:20","14:35","14:50","15:05"];

function renderStatus(data) {
  const panel = $("#status-panel");
  panel.classList.remove("skeleton");
  if (data.status !== "SUCCESS") {
    panel.className = "error-panel";
    panel.innerHTML = `<h2>本次数据更新失败</h2><p>${escapeHtml(data.error || "核心行情不完整，系统已停止筛选。")}</p><p>页面没有沿用旧候选。${data.last_successful ? ` 上次成功：${escapeHtml(data.last_successful.generated_at)}` : ""}</p>`;
    return;
  }
  const market = data.market;
  const labels = {RISK_ON:"进攻", NEUTRAL:"中性", RISK_OFF:"防守"};
  const classes = {RISK_ON:"risk-on", NEUTRAL:"neutral", RISK_OFF:"risk-off"};
  const notes = {
    RISK_ON:"市场广度与强势板块允许主动观察，仍需个股模块和价格条件全部满足。",
    NEUTRAL:"资金分歧较大，优先等待板块连续性和个股确认，减少临盘猜测。",
    RISK_OFF:"普通隔夜通道关闭。热点通道也必须有连续强板块与个股结构确认。"
  };
  const previousClose = data.data_context?.code === "PREVIOUS_CLOSE";
  const snapshotNote = previousClose ? `<p class="snapshot-note"><strong>上一交易日收盘 · ${escapeHtml(data.trade_date || "日期未知")}</strong><br>${escapeHtml(data.data_context.message)}</p>` : "";
  panel.className = "status-panel";
  panel.innerHTML = `
    <div>
      ${snapshotNote}
      <p class="eyebrow">市场环境闸门</p>
      <div class="regime-label ${classes[market.regime]}">${labels[market.regime]}</div>
      <p class="regime-note">${notes[market.regime]} 全市场涨跌中位数 ${fmtPct(market.median_pct)}，成交额 ${fmtAmount(market.market_amount)}。</p>
    </div>
    <div class="breadth-block">
      <p class="eyebrow">上涨占比</p>
      <div class="breadth-number">${(market.breadth * 100).toFixed(1)}%</div>
      <div class="breadth-track" aria-label="上涨占比 ${(market.breadth * 100).toFixed(1)}%"><span class="breadth-up" style="width:${Math.max(0, Math.min(100, market.breadth * 100))}%"></span></div>
      <div class="breadth-caption"><span class="up">涨 ${market.advancers}</span><span>平 ${market.flat}</span><span class="down">跌 ${market.decliners}</span></div>
    </div>`;
}

function renderCheckpoints(data) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {weekday:"short", hour:"2-digit", minute:"2-digit", hour12:false, timeZone:"Asia/Shanghai"}).formatToParts(new Date()).map(part => [part.type, part.value]));
  const nowMinute = Number(parts.hour) * 60 + Number(parts.minute);
  const next = UPDATE_POINTS.find(time => {
    const [hour, minute] = time.split(":").map(Number);
    return hour * 60 + minute > nowMinute;
  });
  const workday = !["Sat", "Sun"].includes(parts.weekday);
  const nextLabel = workday && next ? `今天 ${next}` : "下一工作日 09:25";
  const points = [
    [fmtTime(data.generated_at), "上次生成"],
    ["每15分钟", "上午09:25起 · 下午13:05起"],
    [nextLabel, "下次计划"],
  ];
  $("#checkpoint-panel").innerHTML = points.map(([value, label], index) => `<div class="checkpoint ${index === 2 ? "current" : ""}">${escapeHtml(value)}<small>${escapeHtml(label)}</small></div>`).join("");
}

function renderIndices(data) {
  const panel = $("#indices-panel");
  if (!data.indices?.length) { panel.innerHTML = ""; return; }
  panel.innerHTML = data.indices.map(item => `<article class="index-card"><span class="index-name">${escapeHtml(item.name)}</span><p class="index-price">${Number(item.price).toFixed(2)} <span class="${pctClass(item.change_pct)}">${fmtPct(item.change_pct)}</span></p></article>`).join("");
}

function renderSectors(data) {
  const panel = $("#sectors-panel");
  if (data.status !== "SUCCESS" || !data.sectors?.length) { panel.innerHTML = ""; return; }
  const strong = data.sectors.filter(item => item.strong).slice(0, 8);
  if (!strong.length) { panel.innerHTML = `<div class="sector-pill"><strong>无确认强板块</strong><span>等待下一节点</span></div>`; return; }
  panel.innerHTML = strong.map(item => `<div class="sector-pill"><strong>${item.confirmed ? '<i class="confirmed-dot"></i>' : ""}${escapeHtml(item.sector)}</strong><span>相对强度 ${item.relative_strength > 0 ? "+" : ""}${item.relative_strength.toFixed(2)}｜上涨 ${(item.breadth * 100).toFixed(0)}%</span></div>`).join("");
}

function renderAnalysis(data) {
  const panel = $("#analysis-panel");
  const analysis = data.analysis;
  $("#analysis-mode").textContent = analysis?.mode || "规则模板";
  if (!analysis) {
    panel.innerHTML = `<div class="analysis-summary">本次快照没有盘面分析数据，请等待下一次更新。</div>`;
    return;
  }
  if (!analysis.sections?.length) {
    panel.innerHTML = `<div class="analysis-summary analysis-warning">${escapeHtml(analysis.summary)}</div>`;
    return;
  }
  panel.innerHTML = analysis.sections.map(item => `<article class="analysis-item"><h3>${escapeHtml(item.label)}</h3><p>${escapeHtml(item.text)}</p></article>`).join("");
}

function renderAI(data) {
  const section = $("#ai-section");
  const analysis = data.ai_analysis;
  if (!analysis || analysis.status !== "SUCCESS" || !analysis.text) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  $("#ai-mode").textContent = `${analysis.provider || "AI"} · ${analysis.model || "模型"}`;
  $("#ai-panel").innerHTML = `<p class="ai-copy">${escapeHtml(analysis.text)}</p><p class="ai-disclaimer">${escapeHtml(analysis.disclaimer || "AI解读不参与评分。")}</p>`;
}

function channelBadge(candidate, key, label) {
  const channel = candidate.channels[key];
  const cls = channel.actionable_now ? "ready" : channel.qualified ? "wait" : "closed";
  const state = channel.actionable_now ? "条件就绪" : channel.qualified ? "等待窗口" : "未通过";
  return `<span class="badge ${cls}">${label} · ${state}</span>`;
}

function candidateCard(item) {
  const modules = item.modules.map(module => `<div class="module ${stateClass(module.state)}" title="${escapeHtml(module.state)}"><span class="module-key">${module.key}</span><span class="module-score">${module.score}/${module.max}</span></div>`).join("");
  const evidence = item.modules.map(module => `<div class="evidence-item"><strong>${module.key} ${escapeHtml(module.label)} · ${module.state} · ${module.score}/${module.max}</strong><p>${module.evidence.map(escapeHtml).join("；")}</p></div>`).join("");
  const risks = item.risks?.length ? `<span class="badge closed">风险：${escapeHtml(item.risks.join("、"))}</span>` : "";
  const planState = item.plan.feasible ? "" : `<span class="badge wait">买点未确认</span>`;
  const inCandidatePool = item.in_candidate_pool ?? Number(item.score) >= 60;
  const poolBadge = inCandidatePool ? `<span class="badge ready">已进入候选池</span>` : `<span class="badge closed">未达到候选线</span>`;
  return `<article class="candidate-card" data-ordinary="${item.channels.ordinary.qualified}" data-hot="${item.channels.hot.qualified}">
    <div class="score-rail"><span class="score-value">${item.score}</span><span class="score-max">/ 100</span></div>
    <div class="candidate-body">
      <div class="candidate-top">
        <div><h3 class="stock-name">${escapeHtml(item.name)}</h3><div class="stock-meta">${item.code} · ${escapeHtml(item.sector)} · #${item.rank}</div></div>
        <div class="price">${item.price.toFixed(2)}<br><span class="${pctClass(item.change_pct)}">${fmtPct(item.change_pct)}</span></div>
      </div>
      <div class="channel-row">${poolBadge}${channelBadge(item,"ordinary","普通隔夜")}${channelBadge(item,"hot","热点波段")}<span class="badge">${escapeHtml(item.level)}</span>${planState}${risks}</div>
      <div class="module-grid" aria-label="六模块评分">${modules}</div>
      <div class="trade-plan">
        <div class="plan-cell"><span>承接参考</span><strong>${item.plan.hold_above?.toFixed(2) ?? "—"}</strong></div>
        <div class="plan-cell"><span>突破确认</span><strong>${item.plan.breakout?.toFixed(2) ?? "—"}</strong></div>
        <div class="plan-cell"><span>不追高</span><strong>${item.plan.no_chase_above?.toFixed(2) ?? "—"}</strong></div>
        <div class="plan-cell"><span>失效参考</span><strong>${item.plan.invalid_below?.toFixed(2) ?? "—"}</strong></div>
      </div>
      <details class="evidence"><summary>查看逐项证据与数据边界</summary><div class="evidence-list"><div class="evidence-item"><strong>买点结构</strong><p>${escapeHtml(item.plan.message)}</p></div>${evidence}</div></details>
    </div>
  </article>`;
}

function renderCandidates(data) {
  const list = $("#candidate-list");
  if (data.status !== "SUCCESS") {
    $("#candidate-count").textContent = "0 只";
    list.innerHTML = `<div class="empty"><h3>停止筛选</h3><p>核心行情失败时不发布候选，也不使用旧结果补位。</p></div>`;
    return;
  }
  const rankings = data.rankings || data.candidates || [];
  const items = rankings.filter(item => FILTER === "all" || item.channels[FILTER]?.qualified);
  const candidateCount = (data.candidates || []).length;
  $("#candidate-count").textContent = FILTER === "all" ? `${items.length} 只 · 候选 ${candidateCount} 只` : `${items.length} 只`;
  list.innerHTML = items.length ? items.map(candidateCard).join("") : `<div class="empty"><h3>${FILTER === "all" ? "本次没有有效评分" : "该通道没有合格股票"}</h3><p>评分榜只用于比较强弱；未达到条件时保持空候选。</p></div>`;
}

function renderMethod(data) {
  const sources = (data.sources || []).map(source => `<li><a href="${escapeHtml(source.source_url)}" rel="noreferrer">${escapeHtml(source.source)}</a></li>`).join("");
  const ordinary = data.channels?.ordinary;
  const hot = data.channels?.hot;
  const ai = data.ai_analysis;
  const aiStatus = ai?.status === "SUCCESS" ? `${escapeHtml(ai.provider)} ${escapeHtml(ai.model)} 已生成` : ai?.status === "FAILED" ? "调用失败，已保留规则模板" : "未启用，使用规则模板";
  $("#method-panel").innerHTML = `<strong>榜单边界：</strong>每次展示评分前5只；只有达到60分的股票才进入严格候选池，评分榜不等于推荐。<br><strong>通道状态：</strong>普通隔夜 ${ordinary?.open ? "开启" : "关闭"}（${escapeHtml(ordinary?.message || "—")}）；热点波段 ${hot?.open ? "开启" : "关闭"}（${escapeHtml(hot?.message || "—")}）。<br><strong>AI解读：</strong>${aiStatus}。<br><strong>参数状态：</strong>${escapeHtml(data.strategy?.note || "")}${sources ? `<ul class="source-list">${sources}</ul>` : ""}<p>${escapeHtml(data.disclaimer || "")}</p>`;
}

function render(data) {
  DATA = data;
  const context = data.data_context;
  const label = context?.label || data.phase?.label || "状态未知";
  const tradeDate = context?.trade_date ? ` · ${context.trade_date}` : "";
  $("#asof").textContent = `${label}${tradeDate}\n${fmtTime(data.generated_at)} 生成`;
  renderStatus(data); renderCheckpoints(data); renderIndices(data); renderSectors(data); renderAnalysis(data); renderAI(data); renderCandidates(data); renderMethod(data);
}

document.addEventListener("click", event => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  FILTER = button.dataset.filter;
  document.querySelectorAll(".filter").forEach(item => item.classList.toggle("active", item === button));
  renderCandidates(DATA);
});

async function loadLatest({initial = false} = {}) {
  if (LOAD_IN_FLIGHT) return;
  LOAD_IN_FLIGHT = true;
  try {
    const response = await fetch(`latest.json?t=${Date.now()}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const next = await response.json();
    if (!DATA || next.generated_at !== DATA.generated_at || next.status !== DATA.status) render(next);
    else renderCheckpoints(next);
  } catch (error) {
    if (initial || !DATA) {
      render({status:"FAILED", generated_at:new Date().toISOString(), phase:{code:"CLOSED",label:"读取失败"}, channels:{}, rankings:[], candidates:[], error:`页面无法读取 latest.json：${error.message}`, strategy:{note:""}, disclaimer:"请检查 GitHub Actions 运行日志。"});
    }
  } finally {
    LOAD_IN_FLIGHT = false;
  }
}

loadLatest({initial:true});
setInterval(loadLatest, AUTO_REFRESH_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadLatest(); });
window.addEventListener("focus", loadLatest);
window.addEventListener("online", loadLatest);
