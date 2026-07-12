const toast = document.querySelector(".toast");
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
let scannerEnabled = false;
let liveMarketSession = null;
let selectedAnalysisItem = null;
let appVersion = null;

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
  const value = (selector) => Number(document.querySelector(selector)?.value || 0);
  return {
    targetRate: value("#cfgTargetRate") / 100,
    stopRate: value("#cfgStopRate") / 100,
    maxDailyOrders: value("#cfgMaxDailyOrders"),
    maxOpenPositions: value("#cfgMaxOpenPositions"),
    maxConsecutiveLosses: value("#cfgMaxLosses"),
  };
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
    renderStrategyConfig(payload.config);
    if (payload.paperSummary) renderPaperSummary({ paperSummary: payload.paperSummary });
    showToast("전략 안전장치 설정을 저장했습니다.");
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
    if (response.ok) renderStrategyConfig(payload.config);
  } catch (_) {
    // Analysis status also carries the latest strategy config.
  }
}
function renderAnalysisLog(items) {
  const log = document.querySelector("#analysisLog");
  if (!log) return;
  log.replaceChildren();
  (items || []).slice(0, 4).forEach((item) => {
    const analysis = item.verdict;
    const row = document.createElement("div");
    const icon = document.createElement("i");
    icon.className = analysis.verdict === "진입 불가" ? "alert" : "safe";
    icon.textContent = analysis.icon;
    const copy = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = `${item.name || item.symbol} · ${analysis.verdict}`;
    const detail = document.createElement("small");
    detail.textContent = `${analysis.reason} · ${signedPercent(item.dailyRate)}`;
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
  list.replaceChildren();
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

function renderPaperSummary(state) {
  const summary = state.paperSummary || {};
  const averageReturn = Number(summary.averageReturn || 0);
  const targetRate = Number(summary.targetRate || 0.01);
  const stopRate = Number(summary.stopRate || -0.005);
  const todayOrderCount = Number(summary.todayOrderCount || 0);
  const openPositionCount = Number(summary.openPositionCount || 0);

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
  document.querySelector("#riskLimitLabel").textContent = `${signedPercent(averageReturn)} / ${signedPercent(stopRate)}`;
  document.querySelector("#riskRemainingStop").textContent = `손실선까지 여유 ${signedPercent(Number(decision.remainingToStop ?? averageReturn - stopRate))}`;
  document.querySelector("#riskRemainingTarget").textContent = `목표까지 ${signedPercent(Number(decision.remainingToTarget ?? targetRate - averageReturn))}`;

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

  document.querySelector("#botStatus").textContent = `${openPositionCount}개 포지션 · 오늘 ${todayOrderCount}건`;
  document.querySelector("#positionInsight").textContent = `${openPositionCount}개`;
  document.querySelector("#orderInsight").textContent = `${todayOrderCount}건`;
  const avgInsight = document.querySelector("#avgReturnInsight");
  avgInsight.textContent = signedPercent(averageReturn);
  applyTone(avgInsight, averageReturn);

  const updatedAt = state.lastRunAt
    ? new Date(state.lastRunAt).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    : "대기 중";
  document.querySelector("#analysisUpdatedAt").textContent = summary.locked ? "오늘 거래 잠금" : updatedAt;

  const progress = targetRate > 0 ? Math.max(0, Math.min(100, (averageReturn / targetRate) * 100)) : 0;
  document.querySelector("#analysisPulseBar").style.width = `${summary.locked && averageReturn < 0 ? 100 : Math.max(4, progress)}%`;
  document.querySelector("#analysisPulseBar").classList.toggle("danger", averageReturn < 0);
  document.querySelector("#analysisCycleCopy").textContent = summary.locked
    ? summary.lockReason
    : `${state.activeMarket} 시장 · 일 목표 ${signedPercent(targetRate)} · 현재 ${signedPercent(averageReturn)} · 손실선 ${signedPercent(stopRate)}`;
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

    const tossOk = Boolean(health.toss?.configured && health.toss?.connected);
    toss.textContent = tossOk ? "연결됨" : (health.toss?.configured ? "확인 필요" : "미설정");
    setHealthTone(toss, tossOk);

    const kakaoOk = Boolean(health.kakao?.configured && health.kakao?.enabled && !health.kakao?.lastError);
    kakao.textContent = kakaoOk ? "자동 발송" : (health.kakao?.configured ? "대기/확인" : "미설정");
    setHealthTone(kakao, Boolean(health.kakao?.configured), !kakaoOk);

    const analysisOk = Boolean(health.analysis?.enabled && !health.analysis?.lastError);
    analysis.textContent = analysisOk ? `${health.analysis?.activeSession || "분석 중"}` : "중지/오류";
    setHealthTone(analysis, analysisOk);

    server.textContent = `실행 ${formatUptime(health.uptimeSec)}`;
    setHealthTone(server, Boolean(health.server?.running));

    updated.textContent = health.release?.message || "변경 확인 중";
    updated.title = health.release?.version
      ? `${health.release.version} · ${health.release.committedAt || ""}`
      : "";
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
      renderMarketReports(state);
    }
  } catch (_) {
    // Dashboard connection badge handles connectivity errors.
  }
}

document.querySelector(".mobile-menu").addEventListener("click", () => {
  document.querySelector(".sidebar").classList.toggle("open");
});

document.querySelector(".add-btn").addEventListener("click", () => showToast("전략 만들기 화면을 준비 중입니다."));
document.querySelector(".strategy-btn").addEventListener("click", () => document.querySelector("#strategySettings")?.scrollIntoView({ behavior: "smooth", block: "center" }));
document.querySelector("#strategySaveBtn")?.addEventListener("click", saveStrategyConfig);
document.querySelectorAll("[data-slack-test]").forEach((button) => {
  button.addEventListener("click", () => testSlackChannel(button.dataset.slackTest, button));
});

loadDashboard();
loadAnalysisStatus();
loadHealthStatus();
loadStrategyConfig();
updateMarketClock();
window.setInterval(updateMarketClock, 1_000);
window.setInterval(loadDashboard, 60_000);
window.setInterval(loadAnalysisStatus, 60_000);
window.setInterval(loadHealthStatus, 60_000);
