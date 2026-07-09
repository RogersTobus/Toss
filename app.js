const toast = document.querySelector(".toast");
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
let scannerEnabled = false;
let liveMarketSession = null;

function signedWon(value) {
  const amount = Number(value || 0);
  return `${amount >= 0 ? "+" : "−"}${won.format(Math.abs(amount))}`;
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

function renderAnalysisLog(items) {
  const log = document.querySelector("#analysisLog");
  log.replaceChildren();
  const now = new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
  items.slice(0, 4).forEach((item) => {
    const analysis = item.verdict
      ? { verdict: item.verdict, reason: item.reason, icon: item.verdict === "진입 불가" ? "!" : "⌁" }
      : analyzeHolding(item);
    const entry = document.createElement("div");
    const icon = document.createElement("i");
    icon.className = analysis.verdict === "진입 불가" ? "alert" : "safe";
    icon.textContent = analysis.icon;
    const copy = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = `${item.name || item.symbol} · ${analysis.verdict}`;
    const detail = document.createElement("small");
    detail.textContent = `${analysis.reason} · ${signedPercent(item.dailyRate)}`;
    const time = document.createElement("time");
    time.textContent = now;
    copy.append(title, detail);
    entry.append(icon, copy, time);
    log.append(entry);
  });
}

function renderLongTermHoldings(items) {
  const list = document.querySelector("#longTermHoldings");
  list.replaceChildren();
  items.slice(0, 4).forEach((item) => {
    const row = document.createElement("div");
    const identity = document.createElement("span");
    const name = document.createElement("b");
    name.textContent = item.name || item.symbol;
    const meta = document.createElement("small");
    meta.textContent = `${item.symbol} · ${number.format(Number(item.quantity || 0))}주`;
    identity.append(name, meta);
    const rate = document.createElement("strong");
    rate.textContent = signedPercent(item.profitRate);
    applyTone(rate, item.profitRate);
    row.append(identity, rate);
    list.append(row);
  });
}

function renderMarketPulse(summary, items) {
  document.querySelector("#usdKrw").textContent = `₩${number.format(Number(summary.usdKrw || 0))}`;
  const rates = (items || []).map((item) => Number(item.dailyRate || 0));
  const average = rates.length ? rates.reduce((sum, rate) => sum + rate, 0) / rates.length : 0;
  const positiveRatio = rates.length ? rates.filter((rate) => rate > 0).length / rates.length : 0;
  const tone = document.querySelector("#marketTone");
  const regime = document.querySelector("#marketRegime");
  const detail = document.querySelector("#marketDetail");

  tone.className = "status market-status";
  if (average >= 0.01 && positiveRatio >= 0.6) {
    tone.classList.add("bullish");
    tone.textContent = "상승 우위";
    regime.textContent = "매수세 우위";
  } else if (average <= -0.01 && positiveRatio <= 0.4) {
    tone.classList.add("bearish");
    tone.textContent = "하락 우위";
    regime.textContent = "방어적 접근";
  } else {
    tone.classList.add("mixed");
    tone.textContent = "혼조";
    regime.textContent = "방향성 탐색";
  }
  detail.textContent = `보유 종목 평균 ${signedPercent(average)} · 상승 비중 ${Math.round(positiveRatio * 100)}%`;
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
    marketCountry: item.currency === "USD" ? "US" : "KR",
    profitRate: item.dailyRate,
    quantity: 0,
  }));
  const table = document.querySelector("#holdingsTable");
  const rows = normalized.slice(0, 5).map((item) => {
    const row = document.createElement("div");
    row.className = "table-row";
    const identity = document.createElement("span");
    const ticker = document.createElement("b");
    ticker.className = `ticker ${item.currency === "USD" ? "nv" : "kr"}`;
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
    return row;
  });
  table.querySelectorAll(".table-row:not(.table-head)").forEach((row) => row.remove());
  table.append(...rows);
  renderAnalysisLog(normalized);
}

function renderPaperOrders(orders, market) {
  const list = document.querySelector("#paperOrders");
  list.replaceChildren();
  const recent = (orders || []).slice(-3).reverse();
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
}

function renderPaperSummary(state) {
  const summary = state.paperSummary || {};
  const averageReturn = Number(summary.averageReturn || 0);
  const targetRate = Number(summary.targetRate || 0.01);
  const stopRate = Number(summary.stopRate || -0.005);
  const todayOrderCount = Number(summary.todayOrderCount || 0);
  const openPositionCount = Number(summary.openPositionCount || 0);

  document.querySelector("#paperOrderCount").textContent = `${todayOrderCount}건`;
  document.querySelector("#paperPositionCount").textContent = `${openPositionCount}개`;
  const returnElement = document.querySelector("#paperReturn");
  returnElement.textContent = signedPercent(averageReturn);
  applyTone(returnElement, averageReturn);

  const updatedAt = state.lastRunAt
    ? new Date(state.lastRunAt).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    : "대기 중";
  document.querySelector("#analysisUpdatedAt").textContent = summary.locked ? "오늘 거래 잠금" : updatedAt;

  const progress = targetRate > 0 ? Math.max(0, Math.min(100, (averageReturn / targetRate) * 100)) : 0;
  document.querySelector("#analysisPulseBar").style.width = `${summary.locked && averageReturn < 0 ? 100 : Math.max(4, progress)}%`;
  document.querySelector("#analysisCycleCopy").textContent = summary.locked
    ? summary.lockReason
    : `${state.activeMarket} 시장 · 일 목표 ${signedPercent(targetRate)} · 현재 ${signedPercent(averageReturn)} · 손실선 ${signedPercent(stopRate)}`;
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
    document.querySelector("#portfolioValue").textContent = won.format(summary.totalKrw);
    document.querySelector("#portfolioReturn").textContent = signedPercent(summary.profitRate);
    renderMarketPulse(summary, data.holdings);
    applyTone(document.querySelector("#dailyProfit"), summary.dailyProfitKrw);
    applyTone(document.querySelector("#dailyReturn"), summary.dailyProfitRate);
    applyTone(document.querySelector("#portfolioReturn"), summary.profitRate);
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

document.querySelectorAll(".nav-item[data-page]").forEach((item) => {
  item.addEventListener("click", () => {
    document.querySelectorAll(".nav-item[data-page]").forEach((nav) => nav.classList.remove("active"));
    item.classList.add("active");
    if (item.dataset.page !== "overview") showToast("이 화면은 다음 단계에서 연결할게요.");
  });
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
    }
  } catch (_) {
    // Dashboard connection badge handles connectivity errors.
  }
}

document.querySelector(".mobile-menu").addEventListener("click", () => {
  document.querySelector(".sidebar").classList.toggle("open");
});

document.querySelector(".add-btn").addEventListener("click", () => showToast("전략 만들기 화면을 준비 중입니다."));
document.querySelector(".strategy-btn").addEventListener("click", () => showToast("전략 설정 화면을 준비 중입니다."));

loadDashboard();
loadAnalysisStatus();
updateMarketClock();
window.setInterval(updateMarketClock, 1_000);
window.setInterval(loadDashboard, 60_000);
window.setInterval(loadAnalysisStatus, 60_000);
