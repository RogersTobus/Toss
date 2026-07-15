const toast = document.querySelector(".toast");
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
let scannerEnabled = false;
let liveMarketSession = null;
let selectedAnalysisItem = null;
let appVersion = null;
let currentStrategies = [];
let selectedJournalEntry = null;

function signedWon(value) {
  const amount = Number(value || 0);
  return `${amount >= 0 ? "+" : "−"}${won.format(Math.abs(amount))}`;
}

function plainWon(value) {
  return won.format(Math.abs(Number(value || 0)));
}

function signedPercent(value) {
  const rate = Number(value || 0) * 100;
  return `${rate >= 0 ? "+" : "−"}${Math.abs(rate).toFixed(2)}%`;
}

function applyTone(element, value) {
  element.classList.toggle("positive-text", Number(value) >= 0);
  element.classList.toggle("negative-text", Number(value) < 0);
}

function updateMarketClock() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const hour = Number(values.hour);
  const label = liveMarketSession || (hour >= 8 && hour < 16
    ? "KR 장중"
    : (hour >= 17 || hour < 6) ? "US 장중" : "장외");
  document.querySelector("#marketClockLabel").textContent = label;
  document.querySelector("#marketClockTime").textContent = `${values.hour}:${values.minute}:${values.second} KST`;
}

function updateGreeting() {
  const now = new Date();
  const dateEl = document.querySelector("#greetingDate");
  const titleEl = document.querySelector("#greetingTitle");
  const dateParts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", weekday: "long", month: "short", day: "numeric",
  }).formatToParts(now);
  const dp = Object.fromEntries(dateParts.map((p) => [p.type, p.value]));
  if (dateEl) dateEl.textContent = `${dp.weekday}, ${dp.month} ${dp.day}`.toUpperCase();
  const hour = Number(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Seoul", hour: "2-digit", hour12: false,
  }).format(now));
  if (titleEl) {
    titleEl.textContent = hour < 6 ? "편안한 새벽이에요."
      : hour < 12 ? "좋은 아침이에요."
      : hour < 18 ? "좋은 오후예요."
      : "좋은 저녁이에요.";
  }
}

function analyzeHolding(item) {
  const dailyRate = Number(item.dailyRate || 0);
  const quantity = Number(item.quantity || 0);
  if (Math.abs(dailyRate) >= 0.03) {
    return { verdict: "진입 불가", reason: "당일 변동성 과다", tone: "neutral", icon: "!" };
  }
  if (quantity > 0) {
    return { verdict: "추가 진입 보류", reason: "기보유 포지션", tone: "hold", icon: "—" };
  }
  return { verdict: "분석 중", reason: "전략 신호 대기", tone: "buy", icon: "⌁" };
}


function technicalDetail(item) {
  const rate = Number(item?.dailyRate ?? item?.profitRate ?? item?.returnRate ?? 0);
  const rank = Number(item?.rank || 0);
  const tradingAmount = Number(item?.tradingAmount || 0);
  const momentum = rate >= 0.03 ? "강한 상승" : rate >= 0.01 ? "상승 확인" : rate > -0.01 ? "중립" : "하락 경계";
  const volatility = Math.abs(rate) >= 0.08 ? "과열" : Math.abs(rate) >= 0.035 ? "높음" : "보통";
  const volume = rank > 0 && rank <= 5 ? "최상위" : rank > 0 && rank <= 15 ? "상위" : tradingAmount > 0 ? "관찰" : "데이터 대기";
  const trendScore = (rate > 0.02 ? 35 : rate > 0 ? 20 : -15) + (rank > 0 && rank <= 10 ? 25 : 8) + (Math.abs(rate) < 0.08 ? 20 : -10);
  const finalCall = item?.verdict === "정밀 분석" && trendScore >= 50
    ? "진입 후보"
    : item?.verdict === "진입 불가" || volatility === "과열"
      ? "진입 보류"
      : rate < 0
        ? "손절/관망"
        : "관찰 유지";
  const tone = finalCall === "진입 후보" ? "safe" : finalCall === "진입 보류" || finalCall === "손절/관망" ? "danger" : "caution";
  return {
    momentum,
    volatility,
    volume,
    trendScore: Math.max(0, Math.min(100, trendScore)),
    finalCall,
    tone,
    note: item?.reason || "거래대금·가격 흐름을 추가 확인하세요.",
  };
}

function renderSymbolDetail(item) {
  const card = document.querySelector("#symbolDetailCard");
  if (!card || !item) return;
  selectedAnalysisItem = item;
  const detail = technicalDetail(item);
  const price = item.currency === "USD"
    ? `$${number.format(Number(item.lastPrice || 0))}`
    : won.format(Number(item.lastPrice || 0));
  card.className = `symbol-detail-card ${detail.tone}`;
  card.innerHTML = `
    <div class="symbol-detail-head">
      <div><span>자동 기술 분석</span><b>${item.name || item.symbol}</b><small>${item.symbol || "-"} · ${item.marketCountry || (item.currency === "USD" ? "US" : "KR")}</small></div>
      <em>${detail.finalCall}</em>
    </div>
    <div class="detail-metrics">
      <div><span>현재가</span><b>${price}</b></div>
      <div><span>당일 흐름</span><b>${signedPercent(item.dailyRate || 0)}</b></div>
      <div><span>거래대금 순위</span><b>${item.rank ? `${item.rank}위` : "대기"}</b></div>
      <div><span>추세 점수</span><b>${detail.trendScore}점</b></div>
    </div>
    <div class="detail-checks">
      <p><b>추세</b><span>${detail.momentum}</span></p>
      <p><b>변동성</b><span>${detail.volatility}</span></p>
      <p><b>수급</b><span>${detail.volume}</span></p>
    </div>
    <div class="detail-note">${detail.note}</div>
  `;
}

function renderSafetyRules(summary) {
  const list = document.querySelector("#safetyRules");
  const label = document.querySelector("#safetyGateLabel");
  if (!list) return;
  const rules = summary.safetyRules || [];
  const blockers = rules.filter((rule) => rule.tone === "danger");
  if (label) {
    label.textContent = blockers.length ? `${blockers.length}개 확인 필요` : "정상 범위";
    label.classList.toggle("negative-text", Boolean(blockers.length));
    label.classList.toggle("positive-text", !blockers.length);
  }
  list.replaceChildren();
  if (!rules.length) {
    list.innerHTML = `<span class="safety-chip safe"><b>실주문 보호</b><em>PAPER</em></span>`;
    return;
  }
  rules.forEach((rule) => {
    const chip = document.createElement("span");
    chip.className = `safety-chip ${rule.tone || "safe"}`;
    chip.title = rule.detail || "";
    chip.innerHTML = `<b>${rule.label}</b><em>${rule.status}</em>`;
    list.append(chip);
  });
}

function percentInputValue(rate) {
  return (Number(rate || 0) * 100).toFixed(1);
}

function renderStrategyConfig(config = {}) {
  const pairs = [
    ["#cfgTargetRate", percentInputValue(config.targetRate ?? 0.01)],
    ["#cfgStopRate", percentInputValue(config.stopRate ?? -0.005)],
    ["#cfgMaxDailyOrders", config.maxDailyOrders ?? 3],
    ["#cfgMaxOpenPositions", config.maxOpenPositions ?? 3],
    ["#cfgMaxLosses", config.maxConsecutiveLosses ?? 2],
  ];
  pairs.forEach(([selector, value]) => {
    const input = document.querySelector(selector);
    if (input && document.activeElement !== input) input.value = value;
  });
}

function readStrategyConfigForm() {
  const payload = { strategies: readStrategyTower() };
  const value = (selector) => {
    const input = document.querySelector(selector);
    return input ? Number(input.value || 0) : null;
  };
  const targetRate = value("#cfgTargetRate");
  const stopRate = value("#cfgStopRate");
  const maxDailyOrders = value("#cfgMaxDailyOrders");
  const maxOpenPositions = value("#cfgMaxOpenPositions");
  const maxLosses = value("#cfgMaxLosses");
  if (targetRate !== null) payload.targetRate = targetRate / 100;
  if (stopRate !== null) payload.stopRate = stopRate / 100;
  if (maxDailyOrders !== null) payload.maxDailyOrders = maxDailyOrders;
  if (maxOpenPositions !== null) payload.maxOpenPositions = maxOpenPositions;
  if (maxLosses !== null) payload.maxConsecutiveLosses = maxLosses;
  return payload;
}

function renderStrategyTower(strategies = []) {
  const list = document.querySelector("#strategyTower");
  if (!list) return;
  currentStrategies = strategies.map((item) => ({ ...item }));
  list.replaceChildren();
  if (!currentStrategies.length) {
    const empty = document.createElement("div");
    empty.className = "strategy-empty";
    empty.textContent = "전략 설정을 불러오지 못했습니다.";
    list.append(empty);
    return;
  }
  currentStrategies.forEach((strategy, index) => {
    const row = document.createElement("div");
    row.className = "strategy-row";
    row.dataset.strategyId = strategy.id;

    const numberCell = document.createElement("b");
    numberCell.textContent = String(index + 1);

    const titleWrap = document.createElement("div");
    titleWrap.className = "strat-title";
    const title = document.createElement("textarea");
    title.className = "strat-title-input";
    title.rows = 1;
    title.value = strategy.title || "";
    title.setAttribute("aria-label", "전략 제목");
    const description = document.createElement("textarea");
    description.className = "strat-desc-input";
    description.rows = 2;
    description.value = strategy.description || "";
    description.setAttribute("aria-label", "전략 설명");
    titleWrap.append(title, description);

    const judge = document.createElement("textarea");
    judge.className = "strat-judge";
    judge.rows = 2;
    judge.value = strategy.judge || "";
    judge.setAttribute("aria-label", "수익 유효성 판단");

    const ai = document.createElement("em");
    ai.className = "strat-ai";
    ai.textContent = strategy.aiAdvice || "AI 조언 대기";

    const aiWrap = document.createElement("div");
    aiWrap.className = "strat-ai-wrap";
    const applyAdvice = document.createElement("button");
    applyAdvice.type = "button";
    applyAdvice.className = "apply-ai-btn";
    applyAdvice.textContent = "AI 조언 반영";
    applyAdvice.addEventListener("click", () => {
      judge.value = strategy.aiAdvice || "";
      judge.focus();
      showToast("AI 조언을 이 전략의 판단 문구에 반영했습니다.");
    });
    aiWrap.append(ai, applyAdvice);

    const toggle = document.createElement("label");
    toggle.className = "switch small";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = strategy.enabled !== false;
    const slider = document.createElement("span");
    toggle.append(checkbox, slider);

    row.append(numberCell, titleWrap, judge, aiWrap, toggle);
    list.append(row);
  });
}

function readStrategyTower() {
  const rows = [...document.querySelectorAll("#strategyTower .strategy-row")];
  if (!rows.length) return currentStrategies;
  return rows.map((row) => ({
    id: row.dataset.strategyId,
    title: row.querySelector(".strat-title-input")?.value || "",
    description: row.querySelector(".strat-desc-input")?.value || "",
    judge: row.querySelector(".strat-judge")?.value || "",
    enabled: Boolean(row.querySelector(".switch input")?.checked),
  }));
}

function renderStrategyPayload(payload = {}) {
  renderStrategyConfig(payload.config || {});
  if (Array.isArray(payload.strategies)) renderStrategyTower(payload.strategies);
  renderOverallAdvice(payload.overallAdvice || {});
}

function renderOverallAdvice(advice = {}) {
  const card = document.querySelector("#aiOverviewCard");
  if (!card) return;
  card.className = `ai-overview-card ${advice.tone || "neutral"}`;
  document.querySelector("#aiOverviewHeadline").textContent = advice.headline || "AI 현황 분석 대기";
  document.querySelector("#aiOverviewSummary").textContent = advice.summary || "실시간 분석 상태를 불러오고 있습니다.";
  document.querySelector("#aiOverviewAdvice").textContent = advice.advice || "시장 추세와 전략 상태를 확인한 뒤 조언을 표시합니다.";
  const metrics = document.querySelector("#aiOverviewMetrics");
  if (!metrics) return;
  metrics.replaceChildren();
  (advice.metrics || []).forEach((item) => {
    const chip = document.createElement("span");
    chip.innerHTML = `<b>${item.label || "-"}</b><em>${item.value || "-"}</em>`;
    metrics.append(chip);
  });
}

async function saveStrategyConfig() {
  const button = document.querySelector("#strategySaveBtn");
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/strategy/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readStrategyConfigForm()),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "설정 저장 실패");
    renderStrategyPayload(payload);
    if (payload.paperSummary) renderPaperSummary({ paperSummary: payload.paperSummary });
    showToast("전략 컨트롤타워 설정을 저장했습니다.");
    loadAnalysisStatus();
  } catch (error) {
    showToast(error.message || "설정 저장에 실패했습니다.");
  } finally {
    if (button) button.disabled = false;
  }
}

async function testSlackChannel(channel, button) {
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/slack/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "슬랙 테스트 실패");
    showToast(`${payload.label || "Slack"} 테스트 메시지를 보냈습니다.`);
    loadHealthStatus();
  } catch (error) {
    showToast(error.message || "슬랙 테스트에 실패했습니다.");
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadStrategyConfig() {
  try {
    const response = await fetch("/api/strategy/config", { cache: "no-store" });
    const payload = await response.json();
    if (response.ok) renderStrategyPayload(payload);
  } catch (_) {
    // Analysis status also carries the latest strategy config.
  }
}
function renderAnalysisLog(items) {
  const log = document.querySelector("#analysisLog");
  if (!log) return;
  log.replaceChildren();
  (items || []).slice(0, 20).forEach((item) => {
    const row = document.createElement("div");
    const icon = document.createElement("i");
    icon.className = item.verdict === "진입 불가" ? "alert" : item.verdict === "정밀 분석" ? "safe" : "";
    icon.textContent = item.verdict === "진입 불가" ? "!" : item.verdict === "정밀 분석" ? "✓" : "·";
    const copy = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = `${item.name || item.symbol} · ${item.verdict || "분석 중"}`;
    const detail = document.createElement("small");
    detail.textContent = `${item.reason || "근거 수집 중"} · ${signedPercent(item.dailyRate)}`;
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
    copy.append(title, detail);
    row.append(icon, copy, time);
    log.append(row);
  });
}

function renderLongTermHoldings(items) {
  const list = document.querySelector("#longTermHoldings");
  if (!list) return;
  const targets = (items || []).filter((item) => {
    const text = `${item.name || ""} ${item.symbol || ""}`.toLowerCase();
    return /(s&p|snp|nasdaq|나스닥|다우|dow|500|100|미국)/i.test(text);
  });
  const fallback = [
    { name: "나스닥", symbol: "NASDAQ", profitRate: 0, quantity: 0 },
    { name: "에스앤피", symbol: "S&P 500", profitRate: 0, quantity: 0 },
    { name: "다우존스", symbol: "DOW", profitRate: 0, quantity: 0 },
  ];
  const display = (targets.length ? targets : fallback).slice(0, 3);
  list.replaceChildren();
  display.forEach((item) => {
    const row = document.createElement("div");
    const name = item.name || item.symbol || "장기 지수";
    const quantity = Number(item.quantity || 0);
    const rate = Number(item.profitRate ?? item.returnRate ?? item.dailyRate ?? 0);
    row.innerHTML = `<b>${name}</b><small>${item.symbol || "장기 지수투자"}</small><strong>${quantity ? `${number.format(quantity)}주 · ${signedPercent(rate)}` : "적립 대기"}</strong>`;
    list.append(row);
  });
}

function renderMarketPulse(summary, items) {
  const exchange = document.querySelector("#usdKrw");
  if (!exchange) return;
  exchange.textContent = `₩${number.format(Number(summary.usdKrw || 0))}`;
}

function renderHoldings(items) {
  if (!items?.length) return;
  renderLongTermHoldings(items);
  if (scannerEnabled) return;
  const table = document.querySelector("#holdingsTable");
  const rows = items.slice(0, 5).map((item) => {
    const row = document.createElement("div");
    row.className = "table-row";

    const identity = document.createElement("span");
    const ticker = document.createElement("b");
    ticker.className = `ticker ${item.marketCountry === "KR" ? "kr" : "nv"}`;
    ticker.textContent = item.name?.slice(0, 1) || item.symbol?.slice(0, 1) || "·";
    const title = document.createElement("strong");
    title.textContent = item.name || item.symbol;
    const meta = document.createElement("small");
    meta.textContent = `${item.symbol} · ${item.marketCountry}`;
    title.append(meta);
    identity.append(ticker, title);

    const price = document.createElement("span");
    price.textContent = item.currency === "KRW"
      ? won.format(Number(item.lastPrice || 0))
      : `$${number.format(Number(item.lastPrice || 0))}`;

    const analysis = analyzeHolding(item);
    const stateWrap = document.createElement("span");
    const state = document.createElement("em");
    state.className = `signal ${analysis.tone}`;
    state.textContent = analysis.verdict;
    stateWrap.append(state);

    const reason = document.createElement("span");
    reason.className = "analysis-reason";
    reason.textContent = analysis.reason;

    row.append(identity, price, stateWrap, reason);
    row.tabIndex = 0;
    row.classList.add("clickable-row");
    row.addEventListener("click", () => renderSymbolDetail({ ...item, verdict: analysis.verdict, reason: analysis.reason }));
    return row;
  });
  table.querySelectorAll(".table-row:not(.table-head)").forEach((row) => row.remove());
  table.append(...rows);
  renderAnalysisLog(items);
}

function renderScannerResults(items) {
  if (!items?.length) return;
  const normalized = items.map((item) => ({
    ...item,
    marketCountry: item.marketCountry || (item.sourceCurrency === "USD" ? "US" : "KR"),
    profitRate: item.dailyRate,
    quantity: 0,
  }));
  const table = document.querySelector("#holdingsTable");
  const rows = normalized.slice(0, 20).map((item) => {
    const row = document.createElement("div");
    row.className = "table-row";
    const identity = document.createElement("span");
    const ticker = document.createElement("b");
    ticker.className = `ticker ${item.marketCountry === "US" ? "nv" : "kr"}`;
    ticker.textContent = item.name?.slice(0, 1) || "·";
    const title = document.createElement("strong");
    title.textContent = item.name;
    const meta = document.createElement("small");
    meta.textContent = `${item.symbol} · 거래대금 ${item.rank}위`;
    title.append(meta);
    identity.append(ticker, title);
    const price = document.createElement("span");
    price.textContent = item.currency === "USD"
      ? `$${number.format(Number(item.lastPrice || 0))}`
      : won.format(Number(item.lastPrice || 0));
    const stateWrap = document.createElement("span");
    const state = document.createElement("em");
    state.className = `signal ${item.verdict === "진입 불가" ? "neutral" : item.verdict === "정밀 분석" ? "buy" : "hold"}`;
    state.textContent = item.verdict;
    stateWrap.append(state);
    const reason = document.createElement("span");
    reason.className = "analysis-reason";
    reason.textContent = item.reason;
    row.append(identity, price, stateWrap, reason);
    row.tabIndex = 0;
    row.classList.add("clickable-row");
    row.addEventListener("click", () => renderSymbolDetail(item));
    return row;
  });
  table.querySelectorAll(".table-row:not(.table-head)").forEach((row) => row.remove());
  table.append(...rows);
  renderAnalysisLog(normalized);
}

function renderPaperOrders(orders, market) {
  const list = document.querySelector("#paperOrders");
  const recent = (orders || []).slice(-5).reverse();
  if (!list) { renderDayTradeStatus(recent); return; }
  list.replaceChildren();
  recent.forEach((order) => {
    const row = document.createElement("div");
    row.className = "session active";
    const badge = document.createElement("i");
    badge.textContent = order.market;
    const copy = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = `${order.name} 모의 ${order.side === "BUY" ? "매수" : "매도"}`;
    const detail = document.createElement("small");
    const price = order.currency === "USD" ? `$${number.format(order.price)}` : won.format(order.price);
    detail.textContent = `${order.quantity}주 · ${price}`;
    const status = document.createElement("em");
    status.textContent = "체결";
    copy.append(title, detail);
    row.append(badge, copy, status);
    list.append(row);
  });
  if (!recent.length) {
    const empty = document.createElement("div");
    empty.className = "session";
    empty.innerHTML = `<i>${market}</i><span><b>모의 주문 대기</b><small>시장 분석 중</small></span><em>PAPER</em>`;
    list.append(empty);
  }
  renderDayTradeStatus(recent);
}

function renderDayTradeStatus(orders) {
  const list = document.querySelector("#dayTradeStatus");
  if (!list) return;
  const rows = (orders || []).slice(0, 5);
  list.replaceChildren();
  if (!rows.length) {
    ["대기", "대기", "대기", "대기", "대기"].forEach((name) => {
      const row = document.createElement("div");
      row.innerHTML = `<b>${name}</b><span>분석 대기</span><strong>-</strong>`;
      list.append(row);
    });
    return;
  }
  rows.forEach((order) => {
    const row = document.createElement("div");
    const status = order.side === "SELL" ? "매도 완료" : "보유중";
    const rate = Number(order.returnRate ?? order.profitRate ?? 0);
    row.innerHTML = `<b>${order.name || order.symbol || "종목"}</b><span>${status}</span><strong>${signedPercent(rate)}</strong>`;
    list.append(row);
  });
}

function renderMarketReports(state) {
  const list = document.querySelector("#marketReports");
  const status = document.querySelector("#kakaoReportStatus");
  const reportInsight = document.querySelector("#reportInsight");
  if (!list || !status) return;

  const reportStatus = state.reportStatus || {};
  const connected = Boolean(reportStatus.enabled && !reportStatus.lastError);
  status.textContent = reportStatus.enabled
    ? (reportStatus.lastError ? "발송 확인 필요" : "슬랙 연결")
    : "발송 대기";
  status.classList.toggle("negative-text", Boolean(reportStatus.lastError));
  status.classList.toggle("positive-text", connected);
  if (reportInsight) {
    reportInsight.textContent = connected ? "자동 발송" : "대기";
    applyTone(reportInsight, connected ? 1 : 0);
  }

  const reports = (state.reports || []).slice(-2).reverse();
  list.replaceChildren();
  if (!reports.length) {
    const empty = document.createElement("p");
    empty.textContent = reportStatus.lastError || "한국장/미국장 종료 후 자동 리포트를 준비합니다.";
    list.append(empty);
    return;
  }

  reports.forEach((report) => {
    const row = document.createElement("div");
    row.className = "report-item";
    const created = report.createdAt ? new Date(report.createdAt).toLocaleString("ko-KR", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false
    }) : "방금";
    row.innerHTML = `<span>${report.marketName || report.market}</span><b>${signedWon(report.todayProfitKrw)} · ${signedPercent(report.todayReturnRate)}</b><small>${created} · ${report.sent ? "Slack 발송" : "저장됨"}</small>`;
    list.append(row);
  });
}


function formatJournalTime(value) {
  if (!value) return "시간 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  return date.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatTradingDay(value) {
  if (!value) return "거래일 미지정";
  const date = new Date(`${value}T12:00:00+09:00`);
  if (Number.isNaN(date.getTime())) return `${value} 거래일`;
  return `${date.toLocaleDateString("ko-KR", { month: "2-digit", day: "2-digit", weekday: "short" })} 거래일`;
}

function formatTradePrice(entry) {
  const price = Number(entry.entryPrice || 0);
  if (entry.currency === "USD") return `$${number.format(price)}`;
  return won.format(price);
}

function createJournalRow(entry, compact = false, category = null) {
  const row = document.createElement("button");
  row.type = "button";
  const violation = entry.ruleViolation || null;
  const view = category || (entry.status === "청산" ? "SELL_DONE" : "HOLDING");
  const isSellView = view === "SELL_DONE";
  const isHoldingView = view === "HOLDING";
  row.className = `journal-row ${isSellView ? "sell" : "buy"}${isHoldingView ? " holding" : ""}${compact ? " compact" : ""}${violation && isSellView ? " has-violation" : ""}`;
  row.dataset.journalId = entry.id;
  const isOpen = selectedJournalEntry?.id === entry.id;
  row.classList.toggle("is-open", isOpen);
  row.setAttribute("aria-expanded", String(isOpen));
  const status = isSellView
    ? (violation ? `${violation.label} 위반` : "매도 완료")
    : (isHoldingView ? "보유 중" : "매수 완료");
  const categoryLabel = isSellView ? "매도 완료" : (isHoldingView ? "보유 중" : "매수 완료");
  const priceText = isSellView
    ? `${formatTradePrice({ ...entry, entryPrice: entry.lastPrice })} 매도`
    : `${formatTradePrice(entry)} 매수`;
  const eventTime = isSellView ? (entry.closedAt || entry.createdAt) : (entry.openedAt || entry.createdAt);
  row.innerHTML = `
    <span><b>${entry.name || entry.symbol}</b><small>${formatJournalTime(eventTime)} · ${entry.market || "-"} · ${categoryLabel} · ${priceText}</small></span>
    <em class="${Number(entry.returnRate || 0) >= 0 ? "positive-text" : "negative-text"}">${signedPercent(entry.returnRate || 0)}</em>
    <strong class="${violation && isSellView ? `violation-${violation.severity || "minor"}` : ""}">${status}</strong>
  `;
  row.addEventListener("click", () => openJournalEditor(entry));
  return row;
}

function renderJournalSummary(target, summary = {}, page = false) {
  if (!target) return;
  const today = summary.periodReturns?.today || {};
  const activeDay = summary.activeDay || {};
  const summaryItems = page
    ? [["거래일", formatTradingDay(summary.activeTradingDay).replace(" 거래일", "")], ["기록", `${Number(activeDay.count || 0)}건 (${Number(activeDay.openCount || 0)}/${Number(activeDay.closedCount || 0)})`], ["거래일 손익", signedWon(activeDay.totalProfit || 0)], ["거래일 승률", signedPercent(activeDay.winRate || 0)]]
    : [["기록", `${Number(summary.count || 0)}건`], ["보유/청산", `${Number(summary.openCount || 0)} / ${Number(summary.closedCount || 0)}`], ["오늘 손익", signedWon(today.profitKrw || 0)], ["오늘 수익률", signedPercent(today.returnRate || 0)]];
  target.replaceChildren();
  summaryItems.forEach(([label, value], index) => {
    const box = document.createElement("div");
    box.innerHTML = `<span>${label}</span><b>${value}</b>`;
    if ((page && index >= 2) || (!page && index >= 2)) applyTone(box.querySelector("b"), page ? (index === 2 ? activeDay.totalProfit : activeDay.winRate) : (index === 2 ? today.profitKrw : today.returnRate));
    target.append(box);
  });
}

function renderMistakeNotebook(payload = {}) {
  const coaching = payload.coaching || {};
  const note = coaching.active || {};
  const entries = payload.entries || [];
  const stats = note.stats || {};
  const violations = note.violations || [];
  const tone = note.tone || "neutral";
  const headline = note.headline || "오늘의 매매를 복기하고 있습니다.";
  const reflection = note.reflection || "청산 결과가 나오면 실수와 다음 원칙을 자동으로 정리합니다.";

  const mini = document.querySelector("#journalCoachMini");
  const miniHeadline = document.querySelector("#journalCoachMiniHeadline");
  const miniCopy = document.querySelector("#journalCoachMiniCopy");
  const miniCount = document.querySelector("#journalCoachMiniCount");
  if (mini) mini.dataset.tone = tone;
  if (miniHeadline) miniHeadline.textContent = headline;
  if (miniCopy) miniCopy.textContent = note.nextRule || reflection;
  if (miniCount) {
    miniCount.textContent = note.appliedImmediately
      ? `위반 ${violations.length} · 즉시 적용`
      : (violations.length ? `위반 ${violations.length}건` : "규칙 확인 완료");
  }

  const card = document.querySelector("#mistakeNoteCard");
  if (card) card.className = `mistake-note-card ${tone}`;
  const dateEl = document.querySelector("#mistakeNoteDate");
  const scoreEl = document.querySelector("#mistakeNoteScore");
  const headlineEl = document.querySelector("#mistakeNoteHeadline");
  const reflectionEl = document.querySelector("#mistakeNoteReflection");
  const lessonEl = document.querySelector("#mistakeNoteLesson");
  const ruleEl = document.querySelector("#mistakeNoteRule");
  if (dateEl) dateEl.textContent = `${formatTradingDay(note.tradingDay)} · ${note.author || "Orbit 자동 복기"}`;
  if (scoreEl) scoreEl.textContent = `${Number(stats.closedCount || 0)}청산 · ${Number(stats.winCount || 0)}승 · 오답 ${violations.length}`;
  if (headlineEl) headlineEl.textContent = headline;
  if (reflectionEl) reflectionEl.textContent = reflection;
  if (lessonEl) lessonEl.textContent = note.lesson || "결과와 실행 과정을 함께 확인합니다.";
  if (ruleEl) ruleEl.textContent = note.nextRule || "손실선과 진입 근거를 유지합니다.";

  const countEl = document.querySelector("#ruleViolationCount");
  const list = document.querySelector("#ruleViolationList");
  if (countEl) countEl.textContent = `${violations.length}건`;
  if (!list) return;
  list.replaceChildren();
  if (!violations.length) {
    const empty = document.createElement("div");
    empty.className = "rule-violation-empty";
    empty.textContent = "오늘 확인된 규칙 위반이 없습니다. 손절 실행이 계획 범위 안에 있습니다.";
    list.append(empty);
    return;
  }

  const entriesById = new Map(entries.map((entry) => [entry.id, entry]));
  violations.forEach((violation) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `rule-violation-item ${violation.severity || "minor"}`;
    button.innerHTML = `
      <em>${violation.label || "확인"}</em>
      <span><b>${violation.name || violation.symbol}</b><small>손실선 초과 ${Number(violation.excessRate || 0) * 100 >= 0 ? "+" : ""}${(Number(violation.excessRate || 0) * 100).toFixed(2)}%p</small></span>
      <strong>${signedPercent(violation.returnRate || 0)}</strong>
    `;
    const entry = entriesById.get(violation.id);
    if (entry) {
      button.addEventListener("click", () => {
        showJournalEditor(entry);
        document.querySelector("#journalPageEditor")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
    list.append(button);
  });
}

function formatLearningCooldown(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (!value) return "대기 없음";
  if (value < 60) return `${Math.ceil(value)}초 대기`;
  return `${Math.ceil(value / 60)}분 대기`;
}

function formatStudyPrice(value, market) {
  const numeric = Number(value || 0);
  if (!numeric) return "-";
  return market === "US" ? `$${number.format(numeric)}` : won.format(numeric);
}

function renderOfflineSymbolStudies(study = {}) {
  const list = document.querySelector("#offlineSymbolStudyList");
  const count = document.querySelector("#symbolStudyCount");
  if (!list) return;
  const symbols = study.symbolStudies || [];
  if (count) count.textContent = `${symbols.length}종목 · 3주기 완료 ${Number(study.summary?.completeSymbolCount || 0)}개`;
  list.replaceChildren();
  if (!symbols.length) {
    const empty = document.createElement("div");
    empty.className = "learning-empty";
    empty.textContent = "휴장 연구가 끝나면 종목별 일봉·주봉·월봉 데이터가 한 묶음으로 쌓입니다.";
    list.append(empty);
    return;
  }
  symbols.forEach((symbol) => {
    const card = document.createElement("details");
    card.className = `symbol-study-card ${symbol.tone || "neutral"}`;
    const summary = document.createElement("summary");
    summary.innerHTML = `
      <span><em>${symbol.market || "-"}</em><b>${symbol.name || symbol.symbol}</b><small>${symbol.symbol || ""} · 패턴 관찰 ${Number(symbol.patternObservationCount || 0).toLocaleString("ko-KR")}건</small></span>
      <strong>${symbol.verdict || "분석 완료"}</strong>
      <i>${Number(symbol.completeTimeframeCount || 0)}/3</i>
    `;
    card.append(summary);
    const body = document.createElement("div");
    body.className = "symbol-timeframe-grid";
    (symbol.timeframes || []).forEach((timeframe) => {
      const technical = timeframe.technical || {};
      const backtest = timeframe.backtest || {};
      const pattern = (timeframe.topPatterns || [])[0] || {};
      const panel = document.createElement("article");
      panel.className = `timeframe-study-panel ${(technical.trend || "혼조") === "상승" ? "positive" : ((technical.trend || "혼조") === "하락" ? "negative" : "neutral")}`;
      panel.innerHTML = `
        <div class="timeframe-study-title"><b>${timeframe.label || timeframe.timeframe}</b><em>${technical.trend || "데이터 확인 중"}</em><span>${Number(technical.barCount || 0)}봉</span></div>
        <div class="timeframe-metric-grid">
          <div><span>최근 종가</span><b>${formatStudyPrice(technical.lastClose, symbol.market)}</b></div>
          <div><span>SMA 5 / 20</span><b>${formatStudyPrice(technical.sma5, symbol.market)} / ${formatStudyPrice(technical.sma20, symbol.market)}</b></div>
          <div><span>5봉 / 20봉</span><b>${signedPercent(technical.return5 || 0)} / ${signedPercent(technical.return20 || 0)}</b></div>
          <div><span>RSI 14</span><b>${Number(technical.rsi14 || 0).toFixed(1)}</b></div>
          <div><span>거래량 강도</span><b>${Number(technical.volumeRatio || 0).toFixed(2)}배</b></div>
          <div><span>변동성 20</span><b>${(Number(technical.volatility20 || 0) * 100).toFixed(2)}%</b></div>
          <div><span>60봉 최대낙폭</span><b>${signedPercent(technical.maxDrawdown60 || 0)}</b></div>
          <div><span>백테스트</span><b>${Number(backtest.tradeCount || 0)}회 · 승률 ${(Number(backtest.winRate || 0) * 100).toFixed(1)}%</b></div>
          <div><span>평균 결과</span><b>${signedPercent(backtest.averageReturn || 0)} · ${backtest.researchPass || "검증"}</b></div>
        </div>
        <div class="timeframe-pattern-note"><span>대표 관찰 패턴</span><b>${pattern.label || "신뢰할 패턴을 수집 중입니다."}</b><small>${Number(timeframe.patternObservationCount || 0).toLocaleString("ko-KR")}개 상태 관찰</small></div>
      `;
      body.append(panel);
    });
    card.append(body);
    list.append(card);
  });
}

function renderLearningBrain(learning = {}, entries = []) {
  const summary = learning.summary || {};
  const globalBrain = learning.global || {};
  const offlineStudy = learning.offlineStudy || {};
  const symbols = learning.symbols || [];
  const memories = globalBrain.revisions || [];
  const updated = document.querySelector("#learningBrainUpdatedAt");
  const applyStatus = document.querySelector("#learningApplyStatus");
  if (updated) {
    updated.textContent = learning.updatedAt
      ? `${formatJournalTime(learning.updatedAt)} · 거래와 휴장 연구가 전체 관점을 계속 수정`
      : "첫 청산 거래부터 모든 종목에 통하는 공용 기준을 쌓습니다.";
  }
  if (applyStatus) {
    applyStatus.textContent = summary.immediateApply && summary.coverage === "GLOBAL_ALL_SYMBOLS"
      ? "모든 종목의 다음 PAPER 거래 즉시 적용"
      : (summary.immediateApply ? "PAPER 다음 거래 즉시 적용" : "학습 준비 중");
    applyStatus.classList.toggle("is-live", Boolean(summary.immediateApply));
  }

  const summaryTarget = document.querySelector("#learningBrainSummary");
  if (summaryTarget) {
    const items = [
      ["학습 거래", `${Number(summary.learnedTradeCount || 0)}건`],
      ["점수 표본", `${Number(summary.scoreSampleCount || 0)}건`],
      ["기준 수정", `${Number(globalBrain.revisionCount || 0)}회`],
      ["휴장 관찰", `${Number(offlineStudy.summary?.patternObservationCount || 0).toLocaleString("ko-KR")}건`],
    ];
    summaryTarget.replaceChildren();
    items.forEach(([label, value]) => {
      const box = document.createElement("div");
      box.innerHTML = `<span>${label}</span><b>${value}</b>`;
      summaryTarget.append(box);
    });
  }

  const globalRule = document.querySelector("#learningGlobalRule");
  if (globalRule) {
    globalRule.textContent = `${globalBrain.phase || "초기 관찰"} · ${Number(globalBrain.entryThreshold || 80)}점 기준 · 실거래 표본 ${Number(globalBrain.sampleCount || 0)}건`;
  }
  const featureList = document.querySelector("#learningScoreFeatureList");
  if (featureList) {
    featureList.replaceChildren();
    const features = globalBrain.features || [];
    if (!features.length) {
      const empty = document.createElement("div");
      empty.className = "learning-empty";
      empty.textContent = "청산 표본부터 점수 항목별 적중률을 비교합니다.";
      featureList.append(empty);
    } else {
      features.forEach((feature) => {
        const weight = Number(feature.effectiveWeight || 1);
        const delta = weight - 1;
        const row = document.createElement("div");
        row.className = `global-score-feature ${delta > 0.002 ? "up" : (delta < -0.002 ? "down" : "flat")}`;
        row.innerHTML = `
          <span><b>${feature.label || feature.key}</b><small>승리 평균 ${(Number(feature.winnerAverage || 0) * 100).toFixed(0)} · 손실 평균 ${(Number(feature.loserAverage || 0) * 100).toFixed(0)} · ${Number(feature.sampleCount || 0)}표본</small></span>
          <em>${delta > 0.002 ? "강화" : (delta < -0.002 ? "약화" : "기본")}</em>
          <strong>${weight.toFixed(3)}배</strong>
        `;
        featureList.append(row);
      });
    }
  }

  const symbolList = document.querySelector("#learningSymbolList");
  if (symbolList) {
    symbolList.replaceChildren();
    if (!symbols.length) {
      const empty = document.createElement("div");
      empty.className = "learning-empty";
      empty.textContent = "청산 거래가 쌓이면 종목별 근거 사례가 남습니다.";
      symbolList.append(empty);
    } else {
      const latestBySymbol = new Map();
      entries.forEach((entry) => {
        if (!latestBySymbol.has(entry.symbol)) latestBySymbol.set(entry.symbol, entry);
      });
      symbols.forEach((profile) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = `learning-symbol-row ${profile.riskLevel || "stable"}`;
        const traits = (profile.traits || []).join(" · ") || "사례 수집";
        row.innerHTML = `
          <span><b>${profile.name || profile.symbol}</b><small>${profile.market || "-"} · ${Number(profile.tradeCount || 0)}회 · 승률 ${(Number(profile.winRate || 0) * 100).toFixed(0)}% · ${traits}</small></span>
          <em><small>평균 수익률</small><b>${signedPercent(profile.averageReturn || 0)}</b></em>
          <em><small>평균 진입</small><b>${Number(profile.averageScore || 0).toFixed(0)}점</b></em>
          <strong>공용 뇌의 근거 사례</strong>
        `;
        const entry = latestBySymbol.get(profile.symbol);
        if (entry) row.addEventListener("click", () => showJournalEditor(entry));
        symbolList.append(row);
      });
    }
  }

  const memoryList = document.querySelector("#learningMemoryList");
  if (memoryList) {
    memoryList.replaceChildren();
    if (!memories.length) {
      const empty = document.createElement("div");
      empty.className = "learning-empty";
      empty.textContent = "구조화된 점수 표본이 청산되면 전체 관점 수정 기록이 생깁니다.";
      memoryList.append(empty);
    } else {
      memories.slice(0, 8).forEach((memory) => {
        const item = document.createElement("div");
        item.className = `learning-memory-item ${memory.result === "규칙 오답" || memory.result === "손실 학습" ? "mistake" : ""}`;
        const threshold = memory.thresholdAfter && memory.thresholdBefore !== memory.thresholdAfter
          ? ` · 기준 ${memory.thresholdBefore}→${memory.thresholdAfter}점`
          : "";
        item.innerHTML = `
          <div><b>${memory.name || (memory.scope === "OFF_MARKET_BACKTEST" ? "휴장 연구" : "전체 투자 관점")}</b><em>${memory.result || "학습"}</em></div>
          <p>${memory.summary || "거래 결과로 공용 점수 기준을 다시 계산했습니다."}</p>
          <small>${memory.scope === "OFF_MARKET_BACKTEST" ? "저강도 검증 반영" : "다음 모든 종목 즉시 적용"}${threshold}</small>
        `;
        memoryList.append(item);
      });
    }
  }

  const offlineStatus = document.querySelector("#offlineStudyStatus");
  const offlineSummary = document.querySelector("#offlineStudySummary");
  const offlineJournal = document.querySelector("#offlineStudyJournal");
  if (offlineStatus) {
    offlineStatus.textContent = offlineStudy.status === "completed"
      ? `${offlineStudy.researchPass || "검증"} 완료 · ${formatJournalTime(offlineStudy.completedAt)}`
      : (offlineStudy.status === "error" ? "연구 오류 · 다음 주기 재시도" : "다음 휴장 학습 대기");
    offlineStatus.classList.toggle("is-live", offlineStudy.status === "completed");
  }
  if (offlineSummary) {
    const studySummary = offlineStudy.summary || {};
    const items = [
      ["분석 종목", `${Number(offlineStudy.universeCount || 0)}개`],
      ["차트 분석", `${Number(studySummary.analysisCount || 0)}건`],
      ["패턴 관찰", `${Number(studySummary.patternObservationCount || 0).toLocaleString("ko-KR")}건`],
      ["검증 패턴", `${Number(studySummary.reliablePatternCount || 0)}개`],
    ];
    offlineSummary.replaceChildren();
    items.forEach(([label, value]) => {
      const box = document.createElement("div");
      box.innerHTML = `<span>${label}</span><b>${value}</b>`;
      offlineSummary.append(box);
    });
  }
  if (offlineJournal) {
    offlineJournal.replaceChildren();
    const journal = offlineStudy.journal || [];
    if (!journal.length) {
      const empty = document.createElement("div");
      empty.className = "learning-empty";
      empty.textContent = offlineStudy.lastError || "한국·미국 장이 함께 닫히면 일·주·월봉 패턴 연구를 시작합니다.";
      offlineJournal.append(empty);
    } else {
      journal.slice(0, 10).forEach((note) => {
        const item = document.createElement("div");
        item.className = `offline-study-note ${note.kind === "실패 가설" ? "rejected" : "validated"}`;
        item.innerHTML = `<em>${note.kind || "연구"}</em><span><b>${note.pattern || "패턴"}</b><small>${note.note || "관찰 결과를 기록했습니다."}</small></span>`;
        offlineJournal.append(item);
      });
    }
  }
  renderOfflineSymbolStudies(offlineStudy);
}

function renderTradingJournal(payload = {}) {
  const summary = payload.summary || {};
  const entries = payload.entries || [];
  const updatedText = payload.updatedAt ? `${formatJournalTime(payload.updatedAt)} 갱신` : "기록 대기";
  const updated = document.querySelector("#journalUpdatedAt");
  const pageUpdated = document.querySelector("#journalPageUpdatedAt");
  const navCount = document.querySelector("#journalNavCount");
  if (updated) updated.textContent = updatedText;
  if (pageUpdated) pageUpdated.textContent = updatedText;
  if (navCount) navCount.textContent = Number(summary.count || 0);

  renderJournalSummary(document.querySelector("#journalSummary"), summary, false);
  renderJournalSummary(document.querySelector("#journalPageSummary"), summary, true);
  renderMistakeNotebook(payload);
  renderLearningBrain(payload.learning || {}, entries);

  const miniList = document.querySelector("#tradingJournalList");
  const allList = document.querySelector("#journalAllList");
  [miniList, allList].forEach((list, index) => {
    if (!list) return;
    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "journal-empty";
      empty.textContent = "모의 매매가 기록되면 자동으로 일지가 쌓입니다.";
      list.append(empty);
      return;
    }
    if (index === 0) {
      const rows = entries.filter((entry) => entry.tradingDay === summary.activeTradingDay).slice(0, 3);
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "journal-empty";
        empty.textContent = `${formatTradingDay(summary.activeTradingDay)} 기록이 없습니다.`;
        list.append(empty);
        return;
      }
      rows.forEach((entry) => list.append(createJournalRow(entry, true)));
      return;
    }

    const daySummaryByKey = new Map((summary.days || []).map((day) => [day.tradingDay, day]));
    const grouped = new Map();
    entries.forEach((entry) => {
      const day = entry.tradingDay || "거래일 미지정";
      if (!grouped.has(day)) grouped.set(day, []);
      grouped.get(day).push(entry);
    });
    grouped.forEach((dayEntries, day) => {
      const group = document.createElement("section");
      group.className = "journal-day-group";
      const daySummary = daySummaryByKey.get(day) || {};
      const heading = document.createElement("div");
      heading.className = "journal-day-heading";
      heading.innerHTML = `<b>${formatTradingDay(day)}</b><span>${Number(daySummary.count || dayEntries.length)}건 · ${signedWon(daySummary.totalProfit || 0)} · 승률 ${signedPercent(daySummary.winRate || 0)}</span>`;
      group.append(heading);
      const categories = [
        { key: "BUY_DONE", label: "매수 완료", entries: dayEntries },
        { key: "SELL_DONE", label: "매도 완료", entries: dayEntries.filter((entry) => entry.status === "청산") },
        { key: "HOLDING", label: "보유 중", entries: dayEntries.filter((entry) => entry.status !== "청산") },
      ];
      categories.forEach((category) => {
        const section = document.createElement("div");
        section.className = `journal-status-group ${category.key.toLowerCase().replace("_", "-")}`;
        const categoryHead = document.createElement("div");
        categoryHead.className = "journal-status-heading";
        categoryHead.innerHTML = `<b>${category.label}</b><span>${category.entries.length}건</span>`;
        section.append(categoryHead);
        if (category.entries.length) {
          category.entries.forEach((entry) => section.append(createJournalRow(entry, true, category.key)));
        } else {
          const empty = document.createElement("div");
          empty.className = "journal-status-empty";
          empty.textContent = `${category.label} 기록 없음`;
          section.append(empty);
        }
        group.append(section);
      });
      list.append(group);
    });
  });
}

function setEditorValues(prefix, entry) {
  const editor = document.querySelector(prefix === "page" ? "#journalPageEditor" : "#journalEditor");
  if (!editor) return;
  editor.hidden = false;
  const title = `${entry.name || entry.symbol} ${entry.sideLabel || "매매"} 메모`;
  const learningMeta = entry.learningPolicy?.reason ? ` · 학습: ${entry.learningPolicy.reason}` : "";
  const meta = `${formatJournalTime(entry.createdAt)} · ${entry.reason || "진입 사유 없음"}${learningMeta}`;
  const titleEl = document.querySelector(prefix === "page" ? "#journalPageEditorTitle" : "#journalEditorTitle");
  const metaEl = document.querySelector(prefix === "page" ? "#journalPageEditorMeta" : "#journalEditorMeta");
  const memoEl = document.querySelector(prefix === "page" ? "#journalPageMemo" : "#journalMemo");
  const reviewEl = document.querySelector(prefix === "page" ? "#journalPageReview" : "#journalReview");
  if (titleEl) titleEl.textContent = title;
  if (metaEl) metaEl.textContent = meta;
  if (memoEl) memoEl.value = entry.memo || "";
  if (reviewEl) reviewEl.value = entry.review || "";
}

function setJournalRowsExpanded(entryId = "") {
  document.querySelectorAll(".journal-row").forEach((row) => {
    const expanded = Boolean(entryId && row.dataset.journalId === entryId);
    row.classList.toggle("is-open", expanded);
    row.setAttribute("aria-expanded", String(expanded));
  });
}

function closeJournalEditor() {
  selectedJournalEntry = null;
  document.querySelector("#journalEditor")?.setAttribute("hidden", "");
  document.querySelector("#journalPageEditor")?.setAttribute("hidden", "");
  document.querySelector(".journal-workspace")?.classList.remove("detail-open");
  setJournalRowsExpanded();
}

function showJournalEditor(entry) {
  selectedJournalEntry = entry;
  setEditorValues("mini", entry);
  setEditorValues("page", entry);
  document.querySelector(".journal-workspace")?.classList.add("detail-open");
  setJournalRowsExpanded(entry.id);
}

function openJournalEditor(entry) {
  if (selectedJournalEntry?.id === entry.id) {
    closeJournalEditor();
    return;
  }
  showJournalEditor(entry);
}

async function loadTradingJournal() {
  try {
    const response = await fetch("/api/trading-journal", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "매매일지를 불러오지 못했습니다.");
    renderTradingJournal(payload);
  } catch (error) {
    const list = document.querySelector("#tradingJournalList");
    const allList = document.querySelector("#journalAllList");
    const message = error.message || "매매일지 연결 확인 필요";
    if (list) list.innerHTML = `<div class="journal-empty">${message}</div>`;
    if (allList) allList.innerHTML = `<div class="journal-empty">${message}</div>`;
  }
}

async function saveJournalMemo() {
  if (!selectedJournalEntry) {
    showToast("먼저 매매 기록을 선택해주세요.");
    return;
  }
  const usePageEditor = document.body.dataset.page === "journal";
  const button = document.querySelector(usePageEditor ? "#journalPageSaveBtn" : "#journalSaveBtn");
  const memoValue = document.querySelector(usePageEditor ? "#journalPageMemo" : "#journalMemo")?.value || "";
  const reviewValue = document.querySelector(usePageEditor ? "#journalPageReview" : "#journalReview")?.value || "";
  const unchangedAutomaticDraft = selectedJournalEntry.noteSource === "auto"
    && memoValue === (selectedJournalEntry.memo || "")
    && reviewValue === (selectedJournalEntry.review || "");
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/trading-journal/note", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: selectedJournalEntry.id,
        memo: memoValue,
        review: reviewValue,
        autoGenerated: unchangedAutomaticDraft,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "일지 저장 실패");
    renderTradingJournal(payload);
    const refreshed = (payload.entries || []).find((entry) => entry.id === selectedJournalEntry.id);
    if (refreshed) showJournalEditor(refreshed);
    showToast("매매일지를 저장했습니다.");
  } catch (error) {
    showToast(error.message || "일지 저장에 실패했습니다.");
  } finally {
    if (button) button.disabled = false;
  }
}

function renderPaperSummary(state) {
  const summary = state.paperSummary || {};
  const capital = summary.capital || {};
  const averageReturn = Number(summary.averageReturn || 0);
  const targetRate = Number(summary.targetRate || 0.01);
  const stopRate = Number(summary.stopRate || -0.005);
  const todayOrderCount = Number(summary.todayOrderCount || 0);
  const openPositionCount = Number(summary.openPositionCount || 0);
  const learningSprint = summary.paperLearningSprint || {};

  const decision = summary.decision || {};
  const decisionCard = document.querySelector("#decisionCard");
  const decisionMode = document.querySelector("#decisionMode");
  const decisionReason = document.querySelector("#decisionReason");
  const decisionAction = document.querySelector("#decisionAction");
  if (decisionCard) {
    decisionCard.className = `decision-card ${decision.tone || "neutral"}`;
    decisionMode.textContent = decision.mode || "균형 모드";
    decisionReason.textContent = decision.reason || "손익과 리스크가 관리 가능한 범위입니다.";
    decisionAction.textContent = decision.action || "시장 강도 확인 후 소량 진입";
  }

  const stopProgress = Number(decision.stopProgress || 0);
  const riskLimitBar = document.querySelector("#riskLimitBar");
  if (riskLimitBar) {
    riskLimitBar.style.width = `${Math.max(4, Math.min(100, stopProgress * 100))}%`;
    riskLimitBar.classList.toggle("danger", stopProgress >= 0.8);
    riskLimitBar.classList.toggle("warning", stopProgress >= 0.5 && stopProgress < 0.8);
  }
  const riskLimitLabel = document.querySelector("#riskLimitLabel");
  const riskRemainingStop = document.querySelector("#riskRemainingStop");
  const riskRemainingTarget = document.querySelector("#riskRemainingTarget");
  if (riskLimitLabel) riskLimitLabel.textContent = `${signedPercent(averageReturn)} / ${signedPercent(stopRate)}`;
  if (riskRemainingStop) riskRemainingStop.textContent = `손실선까지 여유 ${signedPercent(Number(decision.remainingToStop ?? averageReturn - stopRate))}`;
  if (riskRemainingTarget) riskRemainingTarget.textContent = `목표까지 ${signedPercent(Number(decision.remainingToTarget ?? targetRate - averageReturn))}`;

  renderSafetyRules(summary);
  renderStrategyConfig(summary.strategyConfig || { targetRate, stopRate });

  const periodReturns = summary.periodReturns || {};
  const profitTargets = [
    ["#paperMonthProfit", "#paperMonthMeta", periodReturns.month],
    ["#paperWeekProfit", "#paperWeekMeta", periodReturns.week],
    ["#paperTodayProfit", "#paperTodayMeta", periodReturns.today],
  ];
  profitTargets.forEach(([profitSelector, metaSelector, item]) => {
    const profitElement = document.querySelector(profitSelector);
    const metaElement = document.querySelector(metaSelector);
    if (!profitElement) return;
    const profit = Number((item || {}).profitKrw || 0);
    const rate = Number((item || {}).returnRate || 0);
    const invested = Number((item || {}).investedKrw || 0);
    const count = Number((item || {}).positionCount || 0);
    profitElement.textContent = `${signedWon(profit)} · ${signedPercent(rate)}`;
    applyTone(profitElement, profit || rate);
    if (metaElement) metaElement.textContent = `투입금 ${plainWon(invested)} · ${count}개`;
  });

  const equityKrw = Number(capital.equityKrw ?? capital.startingCapitalKrw ?? 1_000_000);
  const workingCapitalKrw = Number(capital.workingCapitalKrw ?? capital.startingCapitalKrw ?? 1_000_000);
  const investedKrw = Number(capital.openInvestedKrw || 0);
  const cashKrw = Number(capital.cashKrw || 0);
  const utilizationRate = Number(capital.utilizationRate ?? (workingCapitalKrw ? investedKrw / workingCapitalKrw : 0));
  const unlimitedFunding = capital.fundingLimit === "UNLIMITED" || summary.capitalAllocationPolicy?.fundingLimit === "UNLIMITED";
  const targetUtilizationRate = unlimitedFunding ? 1 : Number(capital.targetUtilizationRate || 0.9);
  const remainingDeployableKrw = Number(capital.remainingDeployableKrw || 0);
  const utilizationCard = document.querySelector("#capitalUtilizationCard");
  const utilizationBar = document.querySelector("#capitalUtilizationBar");
  const targetMarker = document.querySelector("#capitalTargetMarker");
  const utilizationStatus = document.querySelector("#capitalUtilizationStatus");
  const allocationRule = document.querySelector("#capitalAllocationRule");
  if (utilizationCard) {
    utilizationCard.classList.toggle("unlimited-funding", unlimitedFunding);
    utilizationCard.classList.toggle("target-met", !unlimitedFunding && utilizationRate >= targetUtilizationRate);
    utilizationCard.classList.toggle("capital-low", !unlimitedFunding && utilizationRate < Math.max(0, targetUtilizationRate - 0.3));
  }
  if (utilizationBar) utilizationBar.style.width = `${Math.max(0, Math.min(100, utilizationRate * 100))}%`;
  if (targetMarker) {
    targetMarker.hidden = unlimitedFunding;
    targetMarker.style.left = `${Math.max(0, Math.min(100, targetUtilizationRate * 100))}%`;
  }
  if (utilizationStatus) utilizationStatus.textContent = capital.utilizationStatus || "진입 기회 대기";
  if (allocationRule) {
    allocationRule.textContent = learningSprint.enabled && unlimitedFunding
      ? "PAPER 경험 가속 · 자금/횟수/포지션 무제한 · 전역 학습점수/손절 유지"
      : (learningSprint.enabled
      ? "PAPER 학습 가속 · 진입 횟수 무제한 · 전체 투자 공용 점수 유지"
      : (summary.learningCoverage === "GLOBAL_ALL_SYMBOLS"
      ? "모든 신규 거래 · 전역 학습 점수 적용 후 배정"
      : "전체 투자 공용 학습 기준 확인 중"));
  }
  const investedTarget = document.querySelector("#capitalInvestedKrw");
  const cashTarget = document.querySelector("#capitalCashKrw");
  const utilizationTarget = document.querySelector("#capitalUtilizationRate");
  const targetText = document.querySelector("#capitalTargetText");
  const remainingText = document.querySelector("#capitalRemainingText");
  const cashLabel = document.querySelector("#capitalCashLabel");
  const utilizationLabel = document.querySelector("#capitalUtilizationLabel");
  if (investedTarget) investedTarget.textContent = plainWon(investedKrw);
  if (cashTarget) cashTarget.textContent = unlimitedFunding ? "무제한" : plainWon(cashKrw);
  if (utilizationTarget) utilizationTarget.textContent = `${(utilizationRate * 100).toFixed(1)}%`;
  if (cashLabel) cashLabel.textContent = unlimitedFunding ? "가상자금" : "대기 현금";
  if (utilizationLabel) utilizationLabel.textContent = unlimitedFunding ? "기준금 대비" : "현재 활용률";
  if (targetText) targetText.textContent = unlimitedFunding
    ? "기준금 100만원은 성과 비교용 · 가상자금 한도 없음"
    : `운용 목표 ${(targetUtilizationRate * 100).toFixed(0)}% · 현금 예비 ${(Number(capital.reserveRate || 0.1) * 100).toFixed(0)}%`;
  if (remainingText) {
    remainingText.textContent = unlimitedFunding
      ? "점수·학습 통과 후보 계속 진입"
      : (remainingDeployableKrw > 0
      ? `추가 배정 가능 ${plainWon(remainingDeployableKrw)}`
      : "운용 목표 충족");
  }
  const paperModeBadge = document.querySelector("#paperModeBadge");
  if (paperModeBadge) paperModeBadge.textContent = learningSprint.enabled ? "PAPER SPRINT" : "PAPER";
  document.querySelector("#botStatus").textContent = `${unlimitedFunding ? "모의 순자산" : "모의자산"} ${plainWon(equityKrw)} · ${openPositionCount}개 포지션 · 오늘 ${todayOrderCount}건`;
  const positionInsight = document.querySelector("#positionInsight");
  const orderInsight = document.querySelector("#orderInsight");
  if (positionInsight) positionInsight.textContent = `${openPositionCount}개`;
  if (orderInsight) orderInsight.textContent = `${todayOrderCount}건`;
  const avgInsight = document.querySelector("#avgReturnInsight");
  if (avgInsight) {
    avgInsight.textContent = signedPercent(averageReturn);
    applyTone(avgInsight, averageReturn);
  }

  const updatedAt = state.lastRunAt
    ? new Date(state.lastRunAt).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    : "대기 중";
  document.querySelector("#analysisUpdatedAt").textContent = learningSprint.enabled
    ? `무제한 진입 · ${updatedAt}`
    : (summary.locked ? "오늘 거래 잠금" : updatedAt);

  const progress = targetRate > 0 ? Math.max(0, Math.min(100, (averageReturn / targetRate) * 100)) : 0;
  document.querySelector("#analysisPulseBar").style.width = `${summary.locked && averageReturn < 0 ? 100 : Math.max(4, progress)}%`;
  document.querySelector("#analysisPulseBar").classList.toggle("danger", averageReturn < 0);
  document.querySelector("#analysisCycleCopy").textContent = learningSprint.enabled
    ? `${state.activeMarket} 시장 · 오늘 ${todayOrderCount}건 학습 · 점수/개별 손절 유지`
    : (summary.locked
    ? summary.lockReason
    : `${state.activeMarket} 시장 · 일 목표 ${signedPercent(targetRate)} · 현재 ${signedPercent(averageReturn)} · 손실선 ${signedPercent(stopRate)}`);
}

function setHealthTone(element, ok, warning = false) {
  if (!element) return;
  element.classList.toggle("positive-text", Boolean(ok) && !warning);
  element.classList.toggle("negative-text", !ok);
  element.classList.toggle("warning-text", Boolean(ok) && warning);
}

function renderSlackConnection(slack) {
  const badge = document.querySelector("#slackConnection");
  if (!badge) return;
  const channels = ["alert", "report", "log"];
  const isConfigured = (channel) => Boolean(slack?.[channel]?.configured ?? slack?.[channel]);
  const isEnabled = (channel) => Boolean(slack?.[channel]?.enabled ?? slack?.[channel]);
  const connectedCount = channels.filter(isConfigured).length;
  const enabledCount = channels.filter(isEnabled).length;
  badge.classList.toggle("offline", connectedCount === 0);
  badge.classList.toggle("warning", connectedCount > 0 && connectedCount < channels.length);
  badge.querySelector("b").textContent = `${connectedCount}/${channels.length}`;
  badge.title = [
    `Alert: ${isConfigured("alert") ? "연결" : "미설정"}`,
    `Report: ${isConfigured("report") ? "연결" : "미설정"}`,
    `Log: ${isConfigured("log") ? "연결" : "미설정"}`,
    `Enabled: ${enabledCount}/${channels.length}`,
  ].join(" · ");
  document.querySelectorAll("[data-slack-test]").forEach((button) => {
    const channel = button.dataset.slackTest;
    const configured = isConfigured(channel);
    const enabled = isEnabled(channel);
    button.disabled = !configured || !enabled;
    button.title = configured
      ? (enabled ? "테스트 메시지를 발송합니다." : "해당 슬랙 채널이 비활성화되어 있습니다.")
      : "웹훅 URL이 .env에 없습니다.";
  });
}

function formatSyncTime(value) {
  if (!value) return "기록 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "기록 오류";
  return date.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function renderDeployConnection(deploy) {
  const badge = document.querySelector("#deployConnection");
  if (!badge) return;
  const available = Boolean(deploy?.available);
  const failed = available && deploy?.status === "failed";
  const changed = available && Boolean(deploy?.deployed);
  badge.classList.toggle("offline", !available || failed);
  badge.classList.toggle("warning", available && !failed && !changed);
  badge.querySelector("b").textContent = available ? formatSyncTime(deploy.checkedAt) : "기록 없음";
  badge.title = available
    ? [
        `상태: ${deploy.status || "checked"}`,
        `반영: ${changed ? "새 버전 적용" : "최신 상태 확인"}`,
        `현재: ${deploy.localCommit ? String(deploy.localCommit).slice(0, 7) : "-"}`,
        `원격: ${deploy.remoteCommit ? String(deploy.remoteCommit).slice(0, 7) : "-"}`,
      ].join(" · ")
    : "AWS 자동배포가 아직 기록을 남기지 않았습니다.";
}

function formatUptime(seconds) {
  const total = Number(seconds || 0);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}시간 ${minutes}분`;
  return `${minutes}분`;
}

async function loadHealthStatus() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const health = await response.json();
    if (!response.ok) throw new Error(health.error || "운영 상태를 불러오지 못했습니다.");

    if (health.version) {
      if (appVersion && appVersion !== health.version) {
        if (health.release?.message) {
          window.sessionStorage.setItem("orbitUpdatedMessage", health.release.message);
        }
        window.location.reload();
        return;
      }
      appVersion = health.version;
    }
    const updatedMessage = window.sessionStorage.getItem("orbitUpdatedMessage");
    if (updatedMessage) {
      window.sessionStorage.removeItem("orbitUpdatedMessage");
      showToast(`업데이트 반영: ${updatedMessage}`);
    }
    renderSlackConnection(health.slack || {});
    renderDeployConnection(health.deploy || {});

    const toss = document.querySelector("#healthToss");
    const kakao = document.querySelector("#healthKakao");
    const analysis = document.querySelector("#healthAnalysis");
    const server = document.querySelector("#healthServer");
    const updated = document.querySelector("#healthUpdated");

    // Ops status strip was removed from the UI; guard each element so a missing
    // node never throws and resets the connection badges above.
    const tossOk = Boolean(health.toss?.configured && health.toss?.connected);
    if (toss) toss.textContent = tossOk ? "연결됨" : (health.toss?.configured ? "확인 필요" : "미설정");
    setHealthTone(toss, tossOk);

    const kakaoOk = Boolean(health.kakao?.configured && health.kakao?.enabled && !health.kakao?.lastError);
    if (kakao) kakao.textContent = kakaoOk ? "자동 발송" : (health.kakao?.configured ? "대기/확인" : "미설정");
    setHealthTone(kakao, Boolean(health.kakao?.configured), !kakaoOk);

    const analysisOk = Boolean(health.analysis?.enabled && !health.analysis?.lastError);
    if (analysis) analysis.textContent = analysisOk ? `${health.analysis?.activeSession || "분석 중"}` : "중지/오류";
    setHealthTone(analysis, analysisOk);

    if (server) server.textContent = `실행 ${formatUptime(health.uptimeSec)}`;
    setHealthTone(server, Boolean(health.server?.running));

    if (updated) {
      updated.textContent = health.release?.message || "변경 확인 중";
      updated.title = health.release?.version
        ? `${health.release.version} · ${health.release.committedAt || ""}`
        : "";
    }
  } catch (error) {
    renderSlackConnection({});
    renderDeployConnection({});
    ["#healthToss", "#healthKakao", "#healthAnalysis", "#healthServer"].forEach((selector) => {
      const element = document.querySelector(selector);
      if (element) {
        element.textContent = "확인 실패";
        setHealthTone(element, false);
      }
    });
  }
}

async function loadDashboard() {
  const badge = document.querySelector("#apiConnection");
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok || !data.connected) throw new Error(data.error || "연결 실패");

    badge.classList.remove("offline");
    badge.querySelector("b").textContent = "Connected";
    const { summary } = data;
    document.querySelector("#totalAssets").textContent = won.format(summary.totalKrw);
    document.querySelector("#totalReturn").textContent = `${summary.profitRate >= 0 ? "↗" : "↘"} ${Math.abs(summary.profitRate * 100).toFixed(2)}%`;
    document.querySelector("#dailyProfit").textContent = signedWon(summary.dailyProfitKrw);
    document.querySelector("#dailyReturn").textContent = signedPercent(summary.dailyProfitRate);
    const portfolioValue = document.querySelector("#portfolioValue");
    const portfolioReturn = document.querySelector("#portfolioReturn");
    if (portfolioValue) portfolioValue.textContent = won.format(summary.totalKrw);
    if (portfolioReturn) portfolioReturn.textContent = signedPercent(summary.profitRate);
    renderMarketPulse(summary, data.holdings);
    applyTone(document.querySelector("#dailyProfit"), summary.dailyProfitKrw);
    applyTone(document.querySelector("#dailyReturn"), summary.dailyProfitRate);
    if (portfolioReturn) applyTone(portfolioReturn, summary.profitRate);
    renderHoldings(data.holdings);
  } catch (error) {
    badge.classList.add("offline");
    badge.querySelector("b").textContent = "연결 오류";
    showToast(error.message || "토스증권 연결을 확인해주세요.");
  }
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 1800);
}

function openPage(page) {
  const target = ["overview", "quant", "journal"].includes(page) ? page : "overview";
  document.body.dataset.page = target;
  document.querySelectorAll(".nav-item[data-page]").forEach((nav) => {
    nav.classList.toggle("active", nav.dataset.page === target);
  });
  if (target === "quant") showToast("전략 설정 컨트롤타워를 열었습니다.");
  if (target === "journal") {
    showToast("전체 매매일지를 열었습니다.");
    loadTradingJournal();
  }
}

document.querySelectorAll(".nav-item[data-page]").forEach((item) => {
  item.addEventListener("click", () => openPage(item.dataset.page));
});
document.querySelectorAll("[data-open-page]").forEach((button) => {
  button.addEventListener("click", () => openPage(button.dataset.openPage));
});

document.querySelectorAll(".chart-range button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".chart-range button").forEach((item) => item.classList.remove("selected"));
    button.classList.add("selected");
    showToast(`${button.textContent} 자산 흐름으로 변경했습니다.`);
  });
});

document.querySelector("#botToggle").addEventListener("change", async (event) => {
  const status = document.querySelector("#botStatus");
  const enabled = event.target.checked;
  event.target.disabled = true;
  try {
    const response = await fetch(`/api/analysis/${enabled ? "start" : "stop"}`, { method: "POST" });
    if (!response.ok) throw new Error("분석 상태 변경 실패");
    status.textContent = enabled ? "실시간 분석 중" : "모의 운용 중";
    showToast(enabled ? "읽기 전용 라이브 분석을 시작했습니다." : "라이브 분석을 중지했습니다.");
  } catch (error) {
    event.target.checked = !enabled;
    showToast(error.message);
  } finally {
    event.target.disabled = false;
  }
});

async function loadAnalysisStatus() {
  try {
    const response = await fetch("/api/analysis/status", { cache: "no-store" });
    const state = await response.json();
    scannerEnabled = Boolean(state.enabled);
    liveMarketSession = state.activeSession || null;
    document.querySelector("#botToggle").checked = Boolean(state.enabled);
    document.querySelector("#botStatus").textContent = state.enabled
      ? `${state.activeMarket} 모의매매 · ${state.cycle}회`
      : "모의 운용 중";
    if (state.enabled) {
      renderScannerResults(state.results);
      renderPaperOrders(state.paperOrders, state.activeMarket);
      renderPaperSummary(state);
      renderMarketReports(state);
      loadTradingJournal();
      if (document.body.dataset.page === "quant" && !document.activeElement?.closest?.("#strategyTower")) {
        loadStrategyConfig();
      }
    }
  } catch (_) {
    // Dashboard connection badge handles connectivity errors.
  }
}

document.querySelector(".mobile-menu").addEventListener("click", () => {
  document.querySelector(".sidebar").classList.toggle("open");
});

document.querySelector(".add-btn")?.addEventListener("click", () => showToast("전략 만들기 화면을 준비 중입니다."));
// 전략 컨트롤타워 이동은 [data-open-page] 핸들러가 처리합니다.
document.querySelector("#strategySaveBtn")?.addEventListener("click", saveStrategyConfig);
document.querySelector("#journalSaveBtn")?.addEventListener("click", saveJournalMemo);
document.querySelector("#journalPageSaveBtn")?.addEventListener("click", saveJournalMemo);
document.querySelectorAll("[data-slack-test]").forEach((button) => {
  button.addEventListener("click", () => testSlackChannel(button.dataset.slackTest, button));
});

loadDashboard();
loadAnalysisStatus();
loadHealthStatus();
loadStrategyConfig();
loadTradingJournal();
updateMarketClock();
updateGreeting();
window.setInterval(updateMarketClock, 1_000);
window.setInterval(updateGreeting, 60_000);
window.setInterval(loadDashboard, 60_000);
window.setInterval(loadAnalysisStatus, 60_000);
window.setInterval(loadHealthStatus, 60_000);
window.setInterval(loadTradingJournal, 60_000);
