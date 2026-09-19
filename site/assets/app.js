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
let HISTORY_INDEX = [];
let HISTORY_DAY = null;
let HISTORY_SLOT = null;
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
  const previousClose = ["PREVIOUS_CLOSE", "NON_TRADING_DAY"].includes(data.data_context?.code);
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

function signed(value, suffix = "") {
  if (!Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value}${suffix}`;
}

function historyChange(item) {
  const change = item.change || {};
  const labels = {FIRST_ENTRY:"首次入榜", REENTRY:"再次入榜", UP:"排名上升", DOWN:"排名下降", SAME:"排名不变"};
  const cls = change.type === "UP" || change.type === "FIRST_ENTRY" ? "positive" : change.type === "DOWN" ? "negative" : "neutral";
  return `<span class="history-change ${cls}">${escapeHtml(labels[change.type] || "变化未知")}</span>`;
}

function historyRankingRow(item) {
  const moduleDeltas = (item.change?.module_deltas || []).map(module => `<span class="history-module-delta">${escapeHtml(module.key)} ${signed(module.delta)}</span>`).join("");
  const firstSeen = item.first_seen ? `首次 ${escapeHtml(item.first_seen.slot)} · ${Number(item.first_seen.price).toFixed(2)}` : "首次时间未知";
  return `<article class="history-rank-row">
    <div class="history-rank-number">${item.rank}</div>
    <div class="history-stock">
      <strong>${escapeHtml(item.name)}</strong>
      <span>${escapeHtml(item.code)} · ${escapeHtml(item.sector || "板块未知")}</span>
      <small>${firstSeen}</small>
    </div>
    <div class="history-quote"><strong>${Number(item.score).toFixed(0)}分</strong><span>${Number(item.price).toFixed(2)} · <i class="${pctClass(item.change_pct)}">${fmtPct(item.change_pct)}</i></span></div>
    <div class="history-delta">${historyChange(item)}<p>${escapeHtml(item.change?.summary || "暂无上一节点可比较")}</p>${moduleDeltas ? `<div>${moduleDeltas}</div>` : ""}</div>
    <div class="history-plan"><span>确认 ${item.plan?.breakout?.toFixed(2) ?? "—"}</span><span>不追 ${item.plan?.no_chase_above?.toFixed(2) ?? "—"}</span><span>失效 ${item.plan?.invalid_below?.toFixed(2) ?? "—"}</span></div>
  </article>`;
}

function renderHistorySnapshot(snapshot) {
  const detail = $("#history-detail");
  if (!detail || !snapshot) return;
  const quality = snapshot.data_quality || {};
  const coverage = Number.isFinite(quality.coverage) ? `${(quality.coverage * 100).toFixed(1)}%` : "未知";
  const exited = snapshot.exited?.length ? `<p class="history-exited">本节点退出前五：${snapshot.exited.map(item => `${escapeHtml(item.name)}（原第${item.previous_rank}）`).join("、")}</p>` : "";
  detail.innerHTML = `
    <div class="history-meta">
      <span>${escapeHtml(snapshot.slot.label)} 节点</span>
      <span>${fmtTime(snapshot.generated_at)} 实际生成</span>
      <span>行情覆盖 ${coverage}</span>
      <span>${snapshot.slot.kind === "manual" ? "手动记录" : snapshot.slot.source === "backup" ? "备援成功" : "主任务成功"}</span>
    </div>
    <div class="history-rankings">${(snapshot.rankings || []).map(historyRankingRow).join("")}</div>
    ${exited}`;
}

function selectHistorySlot(key) {
  if (!HISTORY_DAY) return;
  HISTORY_SLOT = key;
  document.querySelectorAll("[data-history-slot]").forEach(button => button.classList.toggle("active", button.dataset.historySlot === key));
  renderHistorySnapshot(HISTORY_DAY.snapshots.find(item => item.slot.key === key));
}

function renderHistoryDay(day) {
  const panel = $("#history-panel");
  if (!day?.snapshots?.length) {
    panel.innerHTML = `<div class="history-empty">这个交易日没有成功的盘中榜单记录。</div>`;
    return;
  }
  HISTORY_DAY = day;
  const latest = day.snapshots[day.snapshots.length - 1];
  panel.innerHTML = `<div class="history-tape" role="tablist" aria-label="盘中更新节点">${day.snapshots.map(item => `<button type="button" role="tab" class="history-slot" data-history-slot="${escapeHtml(item.slot.key)}"><span>${escapeHtml(item.slot.label)}</span><small>${item.slot.kind === "manual" ? "手动" : item.slot.source === "backup" ? "备援" : "主任务"}</small></button>`).join("")}</div><div id="history-detail" class="history-detail"></div>`;
  selectHistorySlot(latest.slot.key);
}

async function loadHistoryDay(tradeDate) {
  const panel = $("#history-panel");
  panel.innerHTML = `<div class="history-empty">正在读取 ${escapeHtml(tradeDate)} 的盘中记录…</div>`;
  try {
    const response = await fetch(`intraday/${encodeURIComponent(tradeDate)}.json?t=${Date.now()}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderHistoryDay(await response.json());
  } catch (error) {
    panel.innerHTML = `<div class="history-empty">该交易日的历史文件暂时无法读取。</div>`;
  }
}

async function loadHistoryIndex(preferredDate) {
  const select = $("#history-date");
  try {
    const response = await fetch(`intraday/index.json?t=${Date.now()}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    HISTORY_INDEX = payload.dates || [];
  } catch (error) {
    HISTORY_INDEX = [];
  }
  if (!HISTORY_INDEX.length) {
    select.innerHTML = `<option>尚无记录</option>`;
    select.disabled = true;
    $("#history-panel").innerHTML = `<div class="history-empty">历史记录从本功能上线后的第一次成功更新开始积累。</div>`;
    return;
  }
  select.disabled = false;
  select.innerHTML = HISTORY_INDEX.map(item => `<option value="${escapeHtml(item.date)}">${escapeHtml(item.date)} · ${item.snapshot_count}个节点</option>`).join("");
  const selected = HISTORY_INDEX.some(item => item.date === preferredDate) ? preferredDate : HISTORY_INDEX[0].date;
  select.value = selected;
  await loadHistoryDay(selected);
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
  const score = Number.isFinite(channel.normalized_score) ? ` · ${channel.normalized_score.toFixed(0)}` : "";
  const reason = channel.reasons?.length ? ` title="${escapeHtml(channel.reasons.join("；"))}"` : "";
  return `<span class="badge ${cls}"${reason}>${label} · ${state}${score}</span>`;
}

function candidateCard(item) {
  const modules = item.modules.map(module => `<div class="module ${stateClass(module.state)}" title="${escapeHtml(module.state)}"><span class="module-key">${module.key}</span><span class="module-score">${module.score}/${module.max}</span></div>`).join("");
  const evidence = item.modules.map(module => `<div class="evidence-item"><strong>${module.key} ${escapeHtml(module.label)} · ${module.state} · ${module.score}/${module.max}</strong><p>${module.evidence.map(escapeHtml).join("；")}</p></div>`).join("");
  const risks = item.risks?.length ? `<span class="badge closed">风险：${escapeHtml(item.risks.join("、"))}</span>` : "";
  const planState = item.plan.feasible ? "" : `<span class="badge wait">买点未确认</span>`;
  const inScorePool = item.in_score_pool ?? item.in_candidate_pool ?? Number(item.score) >= 60;
  const poolBadge = inScorePool ? `<span class="badge ready">评分观察池</span>` : `<span class="badge closed">未进入观察池</span>`;
  const confidence = item.data_confidence || {};
  const confidenceClass = confidence.level === "高" ? "ready" : confidence.level === "中" ? "wait" : "closed";
  const confidenceBadge = Number.isFinite(confidence.score) ? `<span class="badge ${confidenceClass}">数据可信度 ${confidence.score}/100</span>` : "";
  const sourceQuality = confidence.source_quality || {};
  const sourceQualityText = Number.isFinite(confidence.source_quality_score) ? `来源质量 ${confidence.source_quality_score}/100${sourceQuality.issues?.length ? `（${sourceQuality.issues.map(escapeHtml).join("；")}）` : ""}` : "来源质量未提供追踪字段";
  const confidenceEvidence = confidence.components?.length ? `<div class="evidence-item"><strong>数据可信度 · ${confidence.level} · ${confidence.score}/100</strong><p>字段完整性 ${confidence.completeness_score ?? "—"}/100；${sourceQualityText}；${confidence.components.map(part => `${escapeHtml(part.label)} ${part.score}/${part.max}（${escapeHtml(part.detail)}）`).join("；")}</p></div>` : "";
  const provenance = item.data_provenance || {};
  const provenanceEvidence = (provenance.quote_source || provenance.daily_source || provenance.minute_source) ? `<div class="evidence-item"><strong>行情来源与时间</strong><p>报价：${escapeHtml(provenance.quote_source || "未知")}，时间 ${escapeHtml(fmtTime(provenance.quote_provider_time))}；日K：${escapeHtml(provenance.daily_source || "未知")}；分钟K：${escapeHtml(provenance.minute_source || "未知")}；${provenance.fallback ? "存在回退数据" : "未标记回退"}${Number.isFinite(provenance.minute_latency_seconds) ? `，分钟线延迟 ${provenance.minute_latency_seconds.toFixed(1)} 秒` : ""}</p></div>` : "";
  return `<article class="candidate-card" data-ordinary="${item.channels.ordinary.qualified}" data-hot="${item.channels.hot.qualified}">
    <div class="score-rail"><span class="score-value">${item.score}</span><span class="score-max">/ 100</span></div>
    <div class="candidate-body">
      <div class="candidate-top">
        <div><h3 class="stock-name">${escapeHtml(item.name)}</h3><div class="stock-meta">${item.code} · ${escapeHtml(item.sector)} · #${item.rank}</div></div>
        <div class="price">${item.price.toFixed(2)}<br><span class="${pctClass(item.change_pct)}">${fmtPct(item.change_pct)}</span></div>
      </div>
      <div class="channel-row">${poolBadge}${channelBadge(item,"ordinary","普通隔夜")}${channelBadge(item,"hot","热点波段")}${confidenceBadge}<span class="badge">${escapeHtml(item.level)}</span>${planState}${risks}</div>
      <div class="module-grid" aria-label="六模块评分">${modules}</div>
      <div class="trade-plan">
        <div class="plan-cell"><span>承接参考</span><strong>${item.plan.hold_above?.toFixed(2) ?? "—"}</strong></div>
        <div class="plan-cell"><span>突破确认</span><strong>${item.plan.breakout?.toFixed(2) ?? "—"}</strong></div>
        <div class="plan-cell"><span>不追高</span><strong>${item.plan.no_chase_above?.toFixed(2) ?? "—"}</strong></div>
        <div class="plan-cell"><span>失效参考</span><strong>${item.plan.invalid_below?.toFixed(2) ?? "—"}</strong></div>
      </div>
      <details class="evidence"><summary>查看逐项证据与数据边界</summary><div class="evidence-list"><div class="evidence-item"><strong>买点结构</strong><p>${escapeHtml(item.plan.message)}</p></div>${provenanceEvidence}${confidenceEvidence}${evidence}</div></details>
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
  const layers = data.layers || {};
  const scorePoolCount = Number.isFinite(layers.score_pool_count) ? layers.score_pool_count : (data.score_pool || data.candidates || []).length;
  const qualifiedCount = Number(layers.ordinary_qualified_count || 0) + Number(layers.hot_qualified_count || 0);
  $("#candidate-count").textContent = FILTER === "all" ? `${items.length} 只 · 观察池 ${scorePoolCount} · 通道合格 ${qualifiedCount}` : `${items.length} 只`;
  list.innerHTML = items.length ? items.map(candidateCard).join("") : `<div class="empty"><h3>${FILTER === "all" ? "本次没有有效评分" : "该通道没有合格股票"}</h3><p>评分榜只用于比较强弱；未达到条件时保持空候选。</p></div>`;
}

function renderMethod(data) {
  const sources = (data.sources || []).map(source => `<li><a href="${escapeHtml(source.source_url)}" rel="noreferrer">${escapeHtml(source.source)}</a></li>`).join("");
  const ordinary = data.channels?.ordinary;
  const hot = data.channels?.hot;
  const ai = data.ai_analysis;
  const aiStatus = ai?.status === "SUCCESS" ? `${escapeHtml(ai.provider)} ${escapeHtml(ai.model)} 已生成` : ai?.status === "FAILED" ? "调用失败，已保留规则模板" : "未启用，使用规则模板";
  const layers = data.layers || {};
  const tdx = data.diagnostics?.tdxaidata;
  const trace = data.diagnostics?.source_trace || {};
  const prefilter = data.diagnostics?.prefilter || {};
  const expansion = data.diagnostics?.detail_expansion || {};
  const tdxStatus = !tdx ? "尚无检测结果" : tdx.status === "CONNECTED" ? `${tdx.mode === "shadow" ? "影子验证已连接" : "主源已连接"}${Number.isFinite(tdx.quote_match_ratio) ? `，报价一致率 ${(tdx.quote_match_ratio * 100).toFixed(1)}%` : ""}` : `${tdx.message || tdx.status}`;
  const sourceTrace = trace.quote_sources ? `全市场行情源 ${escapeHtml(JSON.stringify(trace.universe_quote_sources || {}))}；评分行情源 ${escapeHtml(JSON.stringify(trace.quote_sources))}；行情时间 ${escapeHtml(fmtTime(trace.quote_provider_time_max))}；报价最大延迟 ${Number.isFinite(trace.quote_latency_seconds_max) ? `${trace.quote_latency_seconds_max.toFixed(1)}秒` : "未知"}；详细数据源 ${escapeHtml(JSON.stringify(trace.detail_sources || {}))}` : "尚无字段级来源追踪";
  const detailFlow = `全市场 ${prefilter.input ?? "—"} → 预筛选 ${prefilter.passed ?? "—"} → 详细评分 ${expansion.final_limit ?? data.diagnostics?.details_requested ?? "—"}（${expansion.expanded ? `已扩展，${(expansion.steps || []).join("→")}` : "初始范围"}）→ 展示前5`;
  $("#method-panel").innerHTML = `<strong>四层边界：</strong>评分榜是完整评分前5；观察池是总分达到60分；通道合格要求对应必过模块、风险和买点结构通过；当前可执行还要求处于执行窗口。四层结果分别展示，评分榜不等于推荐。<br><strong>筛选范围：</strong>${detailFlow}。<br><strong>本次统计：</strong>观察池 ${layers.score_pool_count ?? "—"}只；普通隔夜合格 ${layers.ordinary_qualified_count ?? ordinary?.qualified_count ?? 0}只；热点波段合格 ${layers.hot_qualified_count ?? hot?.qualified_count ?? 0}只；当前可执行 ${Number(layers.ordinary_actionable_count || 0) + Number(layers.hot_actionable_count || 0)}个通道机会。<br><strong>通道状态：</strong>普通隔夜 ${ordinary?.open ? "开启" : "关闭"}（${escapeHtml(ordinary?.message || "—")}）；热点波段 ${hot?.open ? "开启" : "关闭"}（${escapeHtml(hot?.message || "—")}）。<br><strong>TdxAiData：</strong>${escapeHtml(tdxStatus)}。<br><strong>字段级来源：</strong>${sourceTrace}。<br><strong>AI解读：</strong>${aiStatus}。<br><strong>参数状态：</strong>${escapeHtml(data.strategy?.note || "")}${sources ? `<ul class="source-list">${sources}</ul>` : ""}<p>${escapeHtml(data.disclaimer || "")}</p>`;
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
  const historyButton = event.target.closest("[data-history-slot]");
  if (historyButton) {
    selectHistorySlot(historyButton.dataset.historySlot);
    return;
  }
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  FILTER = button.dataset.filter;
  document.querySelectorAll(".filter").forEach(item => item.classList.toggle("active", item === button));
  renderCandidates(DATA);
});

$("#history-date").addEventListener("change", event => loadHistoryDay(event.target.value));

async function loadLatest({initial = false} = {}) {
  if (LOAD_IN_FLIGHT) return;
  LOAD_IN_FLIGHT = true;
  try {
    const response = await fetch(`latest.json?t=${Date.now()}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const next = await response.json();
    if (!DATA || next.generated_at !== DATA.generated_at || next.status !== DATA.status) {
      render(next);
      await loadHistoryIndex(next.trade_date);
    }
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
