"""Local dashboard server and Toss Securities Open API gateway.

Secrets stay on the server in .env. The browser only receives normalized
portfolio data and never receives the OAuth access token or account number.
"""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
PAPER_PATH = ROOT / "paper_state.json"
REPORT_PATH = ROOT / "report_state.json"
JOURNAL_PATH = ROOT / "journal_state.json"
LEARNING_PATH = ROOT / "learning_state.json"
STRATEGY_CONFIG_PATH = ROOT / "strategy_config.json"
DEPLOY_STATE_PATH = ROOT / ".deploy" / "last_sync.json"
BASE_URL = "https://openapi.tossinvest.com"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_LOCK = threading.Lock()
LEARNING_LOCK = threading.Lock()
TOKEN: dict[str, Any] = {"value": None, "expires_at": 0.0}
STARTED_AT = time.time()
KST = ZoneInfo("Asia/Seoul")
PAPER_SCHEMA_VERSION = 2
PAPER_STARTING_CAPITAL_KRW = 1_000_000
PAPER_TARGET_RATE = 0.01
PAPER_STOP_RATE = -0.005
PAPER_MAX_DAILY_ORDERS = 3
PAPER_MAX_OPEN_POSITIONS = 3
PAPER_MAX_CONSECUTIVE_LOSSES = 2
PAPER_CAPITAL_TARGET_RATE = 0.90
PAPER_CASH_RESERVE_RATE = 0.10
PAPER_MAX_SINGLE_POSITION_RATE = 0.60
PAPER_LEARNING_SPRINT_MODE = True
PAPER_UNLIMITED_VIRTUAL_CAPITAL = True
PAPER_UNLIMITED_OPEN_POSITIONS = True
PAPER_MIN_EXPERIENCE_ENTRY_RATE = 0.30
LEARNING_SCHEMA_VERSION = 1
LEARNING_BASE_ENTRY_SCORE = 80
DEFAULT_STRATEGY_CONFIG = {
    "targetRate": PAPER_TARGET_RATE,
    "stopRate": PAPER_STOP_RATE,
    "maxDailyOrders": PAPER_MAX_DAILY_ORDERS,
    "maxOpenPositions": PAPER_MAX_OPEN_POSITIONS,
    "maxConsecutiveLosses": PAPER_MAX_CONSECUTIVE_LOSSES,
}
DEFAULT_STRATEGIES = [
    {
        "id": "liquidity-momentum-filter",
        "title": "유동성·모멘텀 후보 필터",
        "description": "한국·미국 주식/ETF/ADR 중 거래대금, 당일 상승률, 최소 가격, 스프레드, 상대 거래량, 돌파 유지 조건을 통과한 종목만 후보로 올립니다.",
        "judge": "필수조건 통과 전에는 진입 금지",
        "enabled": True,
    },
    {
        "id": "score-entry-80",
        "title": "100점 평가·80점 이상 진입",
        "description": "필수조건 통과 종목을 거래대금 강도, 상대 거래량, 돌파 유지, 시장 상태, 호가 품질로 점수화하고 80점 이상만 점수순으로 진입합니다.",
        "judge": "점수 높은 종목 우선",
        "enabled": True,
    },
    {
        "id": "adaptive-capital-utilization",
        "title": "무제한 가상자금·점수 비중 자동배분",
        "description": "100만 원은 성과 비교 기준금으로만 사용하고, 점수를 통과한 종목마다 기준금의 30~60%를 배정합니다. 종목별 오답노트의 비중 축소는 마지막 단계에서 모든 신규 거래에 적용합니다.",
        "judge": "자금 한도 없음 · 학습 비중 우선",
        "enabled": True,
    },
    {
        "id": "paper-learning-sprint",
        "title": "PAPER 무제한 경험 축적",
        "description": "일일 횟수·가상자금·동시 포지션·일 손익·연속 손실에 따른 신규 진입 잠금을 해제하고, 점수와 종목 학습 규칙을 통과한 모든 후보의 성공과 실패를 오답 표본으로 쌓습니다.",
        "judge": "진입 제한 없음 · 개별 손절 유지",
        "enabled": True,
    },
    {
        "id": "unlimited-paper-experience",
        "title": "무제한 가상자금 경험 랩",
        "description": "100만 원은 성과 비교 기준으로만 유지합니다. 가상자금과 동시 포지션 수에는 상한을 두지 않고, 80점 이상과 종목별 학습 규칙을 통과한 후보는 최소 1주 이상 경험 데이터로 기록합니다.",
        "judge": "자금·포지션 무제한 · 점수 필수",
        "enabled": True,
    },
    {
        "id": "hard-stop-loss",
        "title": "−0.5% 절대 손절",
        "description": "평균 체결가 대비 −0.5%에 닿으면 즉시 전량 청산하고 손절선을 불리한 방향으로 옮기거나 물타기하지 않습니다.",
        "judge": "자본 보호 최우선",
        "enabled": True,
    },
    {
        "id": "profit-trailing",
        "title": "+1% 부분익절·추적손절",
        "description": "+1% 도달 시 50%를 익절하고, 잔여 물량은 고점 대비 −0.5% 추적손절로 관리합니다.",
        "judge": "정상 수익은 +1% 이상",
        "enabled": True,
    },
    {
        "id": "three-minute-exit",
        "title": "3분 시간청산",
        "description": "진입 후 3분 안에 의미 있는 상승이 없거나 돌파·VWAP·거래량 논리가 무너지면 청산합니다.",
        "judge": "기회비용 관리",
        "enabled": True,
    },
    {
        "id": "daily-risk-kill-switch",
        "title": "일일 통합 리스크 차단",
        "description": "통합계좌 손실 −0.8%에서 신규 진입을 멈추고, −1.0% 도달 시 미체결 취소 및 포지션 정리를 우선합니다.",
        "judge": "한국·미국 손실 예산 통합",
        "enabled": True,
    },
    {
        "id": "reentry-cooldown",
        "title": "재진입·연속 손절 제한",
        "description": "동일 종목 재진입은 당일 1회로 제한하고, 2회 연속 손절 시 10분간 해당 시장 신규 진입을 중단합니다.",
        "judge": "복수 손실 방지",
        "enabled": True,
    },
    {
        "id": "overnight-extended-session",
        "title": "익일 보유·시간외 분리",
        "description": "익일 보유와 미국 시간외 전략은 정규장 검증 후 별도 전략으로 관리하며, 초기에는 보수적으로 비활성/관찰합니다.",
        "judge": "검증 후 단계적 활성화",
        "enabled": False,
    },
]
ANALYSIS_LOCK = threading.Lock()
ANALYSIS: dict[str, Any] = {
    "enabled": True,
    "cycle": 0,
    "lastRunAt": None,
    "lastError": None,
    "results": [],
    "activeMarket": "KR",
    "activeSession": "시장 확인 중",
    "paperOrders": [],
    "paperSummary": {
        "targetRate": PAPER_TARGET_RATE,
        "stopRate": PAPER_STOP_RATE,
        "averageReturn": 0,
        "locked": False,
        "lockReason": None,
    },
    "reports": [],
    "reportStatus": {"enabled": False, "lastSentAt": None, "lastError": None},
}
CALENDAR_CACHE: dict[str, Any] = {"expiresAt": 0.0, "KR": {}, "US": {}}
FX_CACHE: dict[str, Any] = {"expiresAt": 0.0, "usdKrw": 0.0}



def clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def clean_text(value: Any, fallback: str, limit: int = 260) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:limit]


def normalize_strategies(raw: Any = None) -> list[dict[str, Any]]:
    stored_by_id = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                stored_by_id[str(item.get("id"))] = item
    strategies: list[dict[str, Any]] = []
    for base in DEFAULT_STRATEGIES:
        stored = stored_by_id.get(str(base["id"]), {})
        strategies.append(
            {
                "id": base["id"],
                "title": clean_text(stored.get("title"), str(base["title"]), 80),
                "description": clean_text(stored.get("description"), str(base["description"]), 360),
                "judge": clean_text(stored.get("judge"), str(base["judge"]), 120),
                "enabled": bool(stored.get("enabled", base["enabled"])),
            }
        )
    return strategies


def strategy_config() -> dict[str, Any]:
    config = dict(DEFAULT_STRATEGY_CONFIG)
    stored_strategies = None
    if STRATEGY_CONFIG_PATH.exists():
        try:
            stored = json.loads(STRATEGY_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                config.update(stored)
                stored_strategies = stored.get("strategies")
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "targetRate": clamp(config.get("targetRate"), 0.001, 0.05, PAPER_TARGET_RATE),
        "stopRate": clamp(config.get("stopRate"), -0.05, -0.001, PAPER_STOP_RATE),
        "maxDailyOrders": int(clamp(config.get("maxDailyOrders"), 1, 20, PAPER_MAX_DAILY_ORDERS)),
        "maxOpenPositions": int(clamp(config.get("maxOpenPositions"), 1, 20, PAPER_MAX_OPEN_POSITIONS)),
        "maxConsecutiveLosses": int(clamp(config.get("maxConsecutiveLosses"), 1, 10, PAPER_MAX_CONSECUTIVE_LOSSES)),
        "paperLearningSprint": PAPER_LEARNING_SPRINT_MODE,
        "strategies": normalize_strategies(stored_strategies),
    }


def save_strategy_config(payload: dict[str, Any]) -> dict[str, Any]:
    current = strategy_config()
    if "targetRate" in payload:
        current["targetRate"] = clamp(payload.get("targetRate"), 0.001, 0.05, current["targetRate"])
    if "stopRate" in payload:
        current["stopRate"] = clamp(payload.get("stopRate"), -0.05, -0.001, current["stopRate"])
    if "maxDailyOrders" in payload:
        current["maxDailyOrders"] = int(clamp(payload.get("maxDailyOrders"), 1, 20, current["maxDailyOrders"]))
    if "maxOpenPositions" in payload:
        current["maxOpenPositions"] = int(clamp(payload.get("maxOpenPositions"), 1, 20, current["maxOpenPositions"]))
    if "maxConsecutiveLosses" in payload:
        current["maxConsecutiveLosses"] = int(clamp(payload.get("maxConsecutiveLosses"), 1, 10, current["maxConsecutiveLosses"]))
    if isinstance(payload.get("strategies"), list):
        current["strategies"] = normalize_strategies(payload.get("strategies"))
    STRATEGY_CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def strategy_ai_advice(strategy: dict[str, Any], analysis: dict[str, Any] | None = None) -> str:
    analysis = analysis or {}
    summary = analysis.get("paperSummary") or {}
    decision = summary.get("decision") or {}
    results = analysis.get("results") or []
    verdict_counts: dict[str, int] = {}
    for item in results:
        verdict = str(item.get("verdict") or "분석 중")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    active_market = str(analysis.get("activeMarket") or "CLOSED")
    avg = decimal(summary.get("averageReturn"))
    open_positions = int(summary.get("openPositionCount") or 0)
    today_orders = int(summary.get("todayOrderCount") or 0)
    sid = str(strategy.get("id"))

    if not strategy.get("enabled"):
        return "비활성 상태입니다. 검증 결과가 충분할 때만 다시 켜세요."
    if analysis.get("lastError"):
        return "분석 오류가 있어 전략 변경보다 API/서버 상태 확인이 먼저입니다."
    if active_market == "CLOSED":
        return "현재 시장 휴장입니다. 다음 장에서는 기존 기준을 유지하고 결과만 관찰하세요."
    if sid == "liquidity-momentum-filter":
        return f"후보 {len(results)}개 중 정밀 분석 {verdict_counts.get('정밀 분석', 0)}개입니다. 후보가 적으면 필터 완화보다 거래대금 품질 유지가 우선입니다."
    if sid == "score-entry-80":
        return "장중 추세가 강한 종목만 선별하세요. 100건 검증 전에는 80점 기준을 낮추지 않는 편이 안전합니다."
    if sid == "hard-stop-loss":
        return f"현재 평균 손익 {percent(avg)}입니다. 손실선은 전략의 안전벨트라 완화하지 않는 게 좋습니다."
    if sid == "profit-trailing":
        return "수익은 +1%부터 확인하고, 잔여 물량은 추적손절로 시장에 맡기는 흐름이 좋습니다."
    if sid == "three-minute-exit":
        return f"오늘 진입 {today_orders}건입니다. 진입 후 힘이 없으면 빨리 회수해 다음 후보로 넘기는 구조를 유지하세요."
    if sid == "daily-risk-kill-switch":
        return str(decision.get("action") or "일일 손실 예산을 넘기지 않는 것이 내일도 매매할 권리를 지킵니다.")
    if sid == "reentry-cooldown":
        return f"보유 {open_positions}개입니다. 손절 후 즉시 재진입보다 10분 대기가 과매매를 줄입니다."
    if sid == "overnight-extended-session":
        return "시간외·익일 보유는 스프레드와 유동성 리스크가 커서 정규장 모의 100건 이후 분리 검증하세요."
    return "현재 추세를 관찰하면서 한 번에 한 변수만 바꾸는 방식이 좋습니다."


def overall_ai_analysis(analysis: dict[str, Any] | None = None, strategies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    analysis = analysis or {}
    strategies = strategies or []
    summary = analysis.get("paperSummary") or {}
    decision = summary.get("decision") or {}
    results = analysis.get("results") or []
    active_market = str(analysis.get("activeMarket") or "CLOSED")
    active_session = str(analysis.get("activeSession") or "시장 확인 중")
    avg = decimal(summary.get("averageReturn"))
    target = decimal(summary.get("targetRate") or PAPER_TARGET_RATE)
    stop = decimal(summary.get("stopRate") or PAPER_STOP_RATE)
    open_positions = int(summary.get("openPositionCount") or 0)
    today_orders = int(summary.get("todayOrderCount") or 0)
    enabled_count = sum(1 for item in strategies if item.get("enabled"))
    precision_count = sum(1 for item in results if item.get("verdict") == "정밀 분석")
    blocked = bool((summary.get("decision") or {}).get("tone") == "danger" or summary.get("locked"))

    if analysis.get("lastError"):
        tone = "danger"
        headline = "분석 상태 확인 필요"
        advice = "전략 조정보다 API/서버 오류 해소가 먼저입니다. 오류가 사라진 뒤 전략 판단을 재개하세요."
    elif active_market == "CLOSED":
        tone = "neutral"
        headline = "시장 휴장 · 전략 유지"
        advice = "지금은 설정을 크게 바꾸기보다 내일 장중 데이터가 쌓이는지 확인하는 구간입니다."
    elif blocked:
        tone = "danger"
        headline = "방어 우선 구간"
        advice = "신규 진입을 줄이고 손실선·연속 손절 제한이 제대로 작동하는지 먼저 확인하세요."
    elif precision_count >= 3 and avg >= 0:
        tone = "safe"
        headline = "선별 진입 가능"
        advice = "후보가 충분하고 손익도 안정적입니다. 80점 이상 후보만 소량 진입하는 기준을 유지하세요."
    elif avg < 0:
        tone = "caution"
        headline = "주의 관찰 구간"
        advice = "평균 손익이 마이너스입니다. 필터를 완화하지 말고 기존 포지션과 손절 기준을 먼저 점검하세요."
    else:
        tone = "neutral"
        headline = "균형 모드"
        advice = "후보 품질을 확인하면서 거래대금과 추세가 동시에 붙는 종목만 좁혀보세요."

    return {
        "tone": tone,
        "headline": headline,
        "summary": f"{active_session} · 후보 {len(results)}개 · 정밀 분석 {precision_count}개 · 활성 전략 {enabled_count}개",
        "advice": advice,
        "metrics": [
            {"label": "현재 손익", "value": percent(avg)},
            {"label": "목표", "value": percent(target)},
            {"label": "손실선", "value": percent(stop)},
            {"label": "포지션", "value": f"{open_positions}개"},
            {"label": "오늘 진입", "value": f"{today_orders}건"},
        ],
    }


def strategy_payload() -> dict[str, Any]:
    config = strategy_config()
    analysis = analysis_snapshot()
    strategies = []
    for item in config.get("strategies") or []:
        row = dict(item)
        row["aiAdvice"] = strategy_ai_advice(row, analysis)
        strategies.append(row)
    return {"config": config, "strategies": strategies, "overallAdvice": overall_ai_analysis(analysis, strategies)}
def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def request_json(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = None
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        error = payload.get("error", {})
        if isinstance(error, dict):
            code = str(error.get("code") or payload.get("code") or "api-error")
            message = str(
                error.get("message")
                or payload.get("error_description")
                or payload.get("message")
                or "토스증권 API 요청에 실패했습니다."
            )
        else:
            code = str(payload.get("code") or error or "api-error")
            message = str(
                payload.get("error_description")
                or payload.get("message")
                or error
                or "토스증권 API 요청에 실패했습니다."
            )
        raise TossApiError(
            status=exc.code,
            code=code,
            message=message,
        ) from exc


class TossApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def post_form_json(url: str, form: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode()
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as exc:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        message = str(
            payload.get("error_description")
            or payload.get("msg")
            or payload.get("message")
            or "외부 알림 요청에 실패했습니다."
        )
        code = str(payload.get("error") or payload.get("code") or "notify-error")
        raise TossApiError(exc.code, code, message) from exc


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read().decode("utf-8")
            if not raw or raw == "ok":
                return {"ok": True}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": True, "body": raw}
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8")
        except UnicodeDecodeError:
            pass
        raise TossApiError(
            exc.code,
            "webhook-error",
            raw or "웹훅 발송에 실패했습니다.",
        ) from exc


def load_report_state() -> dict[str, Any]:
    if not REPORT_PATH.exists():
        return {"sentKeys": [], "reports": [], "issues": [], "lastActiveMarket": None, "lastOperationReportKey": None}
    try:
        data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        return {
            "sentKeys": data.get("sentKeys") or [],
            "reports": data.get("reports") or [],
            "issues": data.get("issues") or [],
            "lastActiveMarket": data.get("lastActiveMarket"),
            "lastOperationReportKey": data.get("lastOperationReportKey"),
        }
    except (json.JSONDecodeError, OSError):
        return {"sentKeys": [], "reports": [], "issues": [], "lastActiveMarket": None, "lastOperationReportKey": None}


def save_report_state(state: dict[str, Any]) -> None:
    state["sentKeys"] = (state.get("sentKeys") or [])[-80:]
    state["reports"] = (state.get("reports") or [])[-30:]
    state["issues"] = (state.get("issues") or [])[-200:]
    REPORT_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def now_kst() -> datetime:
    return datetime.now(KST)


def record_report_issue(error: Exception | str, category: str = "analysis") -> None:
    message = str(error).strip() or "원인을 확인하지 못한 오류"
    now = now_kst()
    trading_day = paper_trading_day(now)
    state = load_report_state()
    issues = state.get("issues") or []
    digest = hashlib.sha1(f"{category}:{message}".encode("utf-8", errors="ignore")).hexdigest()[:10]
    key = f"{trading_day}-{category}-{digest}"
    existing = next((item for item in issues if item.get("key") == key), None)
    if existing:
        existing["count"] = int(existing.get("count") or 1) + 1
        existing["lastAt"] = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    else:
        issues.append(
            {
                "key": key,
                "date": trading_day,
                "category": category,
                "message": message[:500],
                "count": 1,
                "firstAt": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "lastAt": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )
    state["issues"] = issues
    save_report_state(state)


def money(value: Any) -> str:
    amount = round(decimal(value))
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{abs(amount):,}원"


def percent(value: Any) -> str:
    rate = decimal(value) * 100
    sign = "+" if rate >= 0 else "-"
    return f"{sign}{abs(rate):.2f}%"


def market_money(value: Any, market: str) -> str:
    amount = decimal(value)
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{abs(round(amount)):,}원"


def strategy_close_review(
    market: str,
    orders: list[dict[str, Any]],
    summary: dict[str, Any],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    date_key = paper_trading_day()
    config = strategy_config()
    enabled = {
        str(item.get("id")): item
        for item in config.get("strategies") or []
        if item.get("enabled")
    }
    ledger = paper_trade_ledger(orders, {})
    closed = [
        item for item in ledger
        if item.get("market") == market
        and item.get("status") == "CLOSED"
        and paper_trading_day(item.get("closedAt")) == date_key
    ]
    entries = [
        item for item in orders
        if str(item.get("side") or "").upper() == "BUY"
        and item.get("market") == market
        and paper_trading_day(item.get("createdAt")) == date_key
    ]
    exits_by_id = {
        str(item.get("id") or ""): item for item in orders
        if str(item.get("side") or "").upper() == "SELL"
    }
    realized_profit = sum(decimal(item.get("profit")) for item in closed)
    realized_invested = sum(decimal(item.get("invested")) for item in closed)
    realized_return = realized_profit / realized_invested if realized_invested else 0.0
    target_exits = [
        item for item in closed
        if str((exits_by_id.get(str(item.get("exitOrderId") or "")) or {}).get("exitKind") or "") == "목표"
    ]
    stop_exits = [
        item for item in closed
        if str((exits_by_id.get(str(item.get("exitOrderId") or "")) or {}).get("exitKind") or "") == "손실선"
    ]
    stop_rate = decimal(config.get("stopRate"))
    delayed_stops = [item for item in stop_exits if decimal(item.get("returnRate")) < stop_rate - 0.003]
    open_count = len(open_paper_positions(orders, market))
    scores = [decimal(item.get("entryScore")) for item in entries if item.get("entryScore") is not None]
    average_score = sum(scores) / len(scores) if scores else 0.0

    good: list[str] = []
    bad: list[str] = []
    improvements: list[str] = []

    if closed and realized_return > 0:
        good.append(
            f"유동성·80점 진입 필터: 청산 {len(closed)}건, 실현 {percent(realized_return)}"
            + (f", 평균 진입점수 {average_score:.0f}점" if scores else "")
        )
    elif closed:
        bad.append(
            f"유동성·80점 진입 필터: 청산 {len(closed)}건, 실현 {percent(realized_return)}로 기대수익 미달"
        )
        improvements.append("진입 점수 자체보다 상승률 과열·스프레드·추세 지속 조건을 추가 검증")
    else:
        bad.append("진입 필터: 오늘 청산 표본이 없어 성과 판정 보류")
        improvements.append("설정을 바꾸지 말고 청산 표본을 더 축적한 뒤 평가")

    if target_exits:
        good.append(f"+1% 수익 관리: 목표 청산 {len(target_exits)}건 작동")
    elif "profit-trailing" in enabled and closed:
        bad.append("+1% 수익 관리: 목표 청산 0건으로 수익 구간 검증 부족")

    if stop_exits:
        good.append(f"−0.5% 절대 손절: 손실 청산 {len(stop_exits)}건 실행")
    if delayed_stops:
        bad.append(f"절대 손절: {len(delayed_stops)}건이 손실선보다 0.3%p 이상 불리하게 청산")
        improvements.append("손실선 근처에서는 시세 확인·청산 주기를 단축해 체결 괴리를 줄이기")

    if open_count:
        bad.append(f"장마감 포지션 관리: 미청산 {open_count}건 잔존")
        improvements.append("시장 종료 전 신규 진입 차단과 마감 청산 규칙을 별도 검증")
    elif entries:
        good.append("장마감 포지션 관리: 미청산 포지션 없음")

    if "three-minute-exit" in enabled and not any(
        str((exits_by_id.get(str(item.get("exitOrderId") or "")) or {}).get("exitKind") or "") == "시간청산"
        for item in closed
    ):
        bad.append("3분 시간청산: 실행 기록이 없어 규칙 작동 여부 검증 필요")
        improvements.append("진입 시각 기준 3분 경과·추세 약화 조건을 실행 로그에 명시")

    today_issues = [item for item in issues if item.get("date") == date_key]
    error_lines = [
        f"{item.get('message') or '원인 미상'} (반복 {int(item.get('count') or 1)}회)"
        for item in today_issues[-5:]
    ]
    if today_issues:
        improvements.append("반복 오류는 다음 장 시작 전 API 연결·재시도 로그를 우선 점검")

    return {
        "good": good or ["성과가 확인된 전략 없음"],
        "bad": bad or ["특이 부진 전략 없음"],
        "errors": error_lines or ["기록된 시스템 오류 없음"],
        "improvements": list(dict.fromkeys(improvements)) or ["현재 전략을 유지하고 표본을 추가 축적"],
        "realizedProfit": realized_profit,
        "realizedReturn": realized_return,
        "entryCount": len(entries),
        "closedCount": len(closed),
        "openCount": open_count,
    }


def build_market_close_report(
    market: str,
    orders: list[dict[str, Any]],
    summary: dict[str, Any],
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now = now_kst()
    date_key = paper_trading_day(now)
    market_name = "한국장" if market == "KR" else "미국장" if market == "US" else market
    period = summary.get("periodReturns") or {}
    week = period.get("week") or {}
    month = period.get("month") or {}
    review = strategy_close_review(market, orders, summary, issues or [])
    today_orders = [
        item for item in orders
        if str(item.get("side") or "").upper() == "BUY"
        and item.get("market") == market
        and paper_trading_day(item.get("createdAt")) == date_key
    ]
    top_names = list(dict.fromkeys(str(item.get("name") or item.get("symbol") or "-") for item in today_orders[-5:]))
    good_lines = [f"- {item}" for item in review["good"]]
    bad_lines = [f"- {item}" for item in review["bad"]]
    error_lines = [f"- {item}" for item in review["errors"]]
    improvement_lines = [f"- {item}" for item in review["improvements"]]
    lines = [
        ":clipboard: *Orbit 단타 장마감 회고 리포트*",
        f"시장: {market_name}",
        f"일시: {now.strftime('%Y-%m-%d %H:%M KST')}",
        "",
        "*1. 오늘 결산*",
        f"실현 단타 손익: {market_money(review['realizedProfit'], market)} ({percent(review['realizedReturn'])})",
        f"진입/청산/보유: {review['entryCount']} / {review['closedCount']} / {review['openCount']}건",
        f"이번주 전체 단타 손익: {market_money(week.get('profitKrw'), market)} ({percent(week.get('returnRate'))})",
        f"이번달 전체 단타 손익: {market_money(month.get('profitKrw'), market)} ({percent(month.get('returnRate'))})",
        "",
        "*2. 좋았던 전략*",
        *good_lines,
        "",
        "*3. 좋지 않았던 전략*",
        *bad_lines,
        "",
        "*4. 오늘 발생한 오류*",
        *error_lines,
        "",
        "*5. 다음 장 개선방안*",
        *improvement_lines,
    ]
    if top_names:
        lines.extend(["", "대표 매매 종목: " + ", ".join(top_names[:5])])
    if summary.get("locked"):
        lines.append("마감 상태: " + str(summary.get("lockReason") or "오늘 거래 잠금"))
    return {
        "id": f"{date_key}-{market}",
        "market": market,
        "marketName": market_name,
        "createdAt": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "todayProfitKrw": review["realizedProfit"],
        "todayReturnRate": review["realizedReturn"],
        "orderCount": len(today_orders),
        "positionCount": summary.get("openPositionCount", 0),
        "sent": False,
        "message": "\n".join(lines),
    }



def update_env_values(updates: dict[str, str]) -> None:
    path = ROOT / ".env"
    existing = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    seen: set[str] = set()
    lines: list[str] = []
    for raw in existing:
        if "=" not in raw or raw.strip().startswith("#"):
            lines.append(raw)
            continue
        key = raw.split("=", 1)[0].strip()
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(raw)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def kakao_redirect_uri(env: dict[str, str]) -> str:
    return env.get("KAKAO_REDIRECT_URI") or "http://127.0.0.1:4173/kakao/callback"


def kakao_auth_url(env: dict[str, str]) -> str:
    rest_key = env.get("KAKAO_REST_API_KEY", "")
    if not rest_key:
        raise TossApiError(400, "kakao-rest-key-missing", "KAKAO_REST_API_KEY가 .env에 없습니다.")
    params = {
        "response_type": "code",
        "client_id": rest_key,
        "redirect_uri": kakao_redirect_uri(env),
        "scope": "talk_message",
        "prompt": "consent",
    }
    return f"{KAKAO_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_kakao_code(env: dict[str, str], code: str) -> dict[str, Any]:
    rest_key = env.get("KAKAO_REST_API_KEY", "")
    if not rest_key:
        raise TossApiError(400, "kakao-rest-key-missing", "KAKAO_REST_API_KEY가 .env에 없습니다.")
    form = {
        "grant_type": "authorization_code",
        "client_id": rest_key,
        "redirect_uri": kakao_redirect_uri(env),
        "code": code,
    }
    client_secret = env.get("KAKAO_CLIENT_SECRET", "")
    if client_secret:
        form["client_secret"] = client_secret
    return post_form_json(KAKAO_TOKEN_URL, form)


def kakao_callback_page(title: str, body: str, ok: bool = True) -> bytes:
    color = "#5c7f2f" if ok else "#9c3b32"
    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>body{{font-family:Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f7f5ee;color:#171914;display:grid;place-items:center;min-height:100vh;margin:0}}main{{width:min(520px,92vw);background:#fff;border:1px solid #e6e0d0;border-radius:18px;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.08)}}h1{{font-size:22px;margin:0 0 12px;color:{color}}}p{{line-height:1.6}}button{{border:0;border-radius:10px;background:#171914;color:#fff;padding:12px 16px;font-weight:800;cursor:pointer}}</style></head>
<body><main><h1>{title}</h1><p>{body}</p><button onclick="location.href='/'">Orbit으로 돌아가기</button></main></body></html>"""
    return html.encode("utf-8")

def kakao_enabled(env: dict[str, str]) -> bool:
    return str(env.get("KAKAO_REPORT_ENABLED", "")).lower() in ("1", "true", "yes", "on")


def slack_enabled(env: dict[str, str], channel: str) -> bool:
    key = f"SLACK_{channel.upper()}_ENABLED"
    if not env.get(f"SLACK_{channel.upper()}_WEBHOOK_URL"):
        return False
    value = str(env.get(key, "")).strip().lower()
    if not value:
        return True
    return value not in ("0", "false", "no", "off")


def send_slack(channel: str, text: str) -> None:
    env = load_env()
    key = f"SLACK_{channel.upper()}_WEBHOOK_URL"
    webhook_url = env.get(key, "")
    if not webhook_url:
        raise TossApiError(400, "slack-webhook-missing", f"{key}가 .env에 없습니다.")
    post_json(webhook_url, {"text": text[:3500]})


def slack_status(env: dict[str, str]) -> dict[str, dict[str, bool]]:
    return {
        channel: {
            "configured": bool(env.get(f"SLACK_{channel.upper()}_WEBHOOK_URL")),
            "enabled": slack_enabled(env, channel),
        }
        for channel in ("alert", "report", "log")
    }


def test_slack_channel(channel: str) -> dict[str, Any]:
    if channel not in ("alert", "report", "log"):
        raise TossApiError(400, "slack-channel-invalid", "지원하지 않는 슬랙 채널입니다.")
    env = load_env()
    if not slack_enabled(env, channel):
        raise TossApiError(400, "slack-disabled", f"SLACK_{channel.upper()} 채널이 비활성화되어 있습니다.")
    label = {"alert": "긴급알림", "report": "결산리포트", "log": "운영로그"}[channel]
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    send_slack(
        channel,
        "\n".join(
            [
                ":satellite: *Orbit Slack 연결 테스트*",
                f"채널: {label}",
                f"시간: {now}",
                "상태: 웹훅 연결 정상",
            ]
        ),
    )
    return {"ok": True, "channel": channel, "label": label, "sentAt": now}


def handle_paper_alert(env: dict[str, str], market: str | None, summary: dict[str, Any]) -> None:
    if not market or not summary.get("locked") or not slack_enabled(env, "alert"):
        return
    stop_rate = decimal(summary.get("stopRate"))
    current_rate = decimal(summary.get("averageReturn"))
    reason = str(summary.get("lockReason") or "오늘 거래 잠금")
    if not (current_rate <= stop_rate or "손실" in reason or "중지" in reason):
        return
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    key = f"paper-alert-{today}-{market}-{reason}"
    state = load_report_state()
    sent_keys = set(state.get("sentKeys") or [])
    if key in sent_keys:
        return
    text = "\n".join(
        [
            ":rotating_light: *Orbit 긴급 알림*",
            f"시장: {market}",
            f"상태: {reason}",
            f"평균 평가손익: {percent(summary.get('averageReturn'))}",
            f"모의 주문: {summary.get('todayOrderCount', 0)}건",
            f"보유 포지션: {summary.get('openPositionCount', 0)}개",
        ]
    )
    try:
        send_slack("alert", text)
        sent_keys.add(key)
        state["sentKeys"] = list(sent_keys)
        save_report_state(state)
    except TossApiError:
        # Alert delivery must not stop the market analysis loop.
        pass


def handle_problem_alert(env: dict[str, str], error: Exception) -> None:
    try:
        record_report_issue(error, "analysis")
    except OSError:
        pass
    if not slack_enabled(env, "alert"):
        return
    now = now_kst()
    message = str(error) or error.__class__.__name__
    digest = hashlib.sha1(message.encode("utf-8", errors="ignore")).hexdigest()[:10]
    minute_slot = (now.minute // 10) * 10
    key = f"problem-alert-{now.strftime('%Y-%m-%d')}-{now.hour:02d}{minute_slot:02d}-{digest}"
    state = load_report_state()
    sent_keys = set(state.get("sentKeys") or [])
    if key in sent_keys:
        return
    text = "\n".join(
        [
            ":rotating_light: *Orbit 문제 발생*",
            f"시간: {now.strftime('%Y-%m-%d %H:%M %Z')}",
            "영향: 실시간 분석/모의 단타 판단이 지연될 수 있음",
            f"오류: {message[:500]}",
            "자동 조치: 다음 루프에서 재시도",
        ]
    )
    try:
        send_slack("alert", text)
        sent_keys.add(key)
        state["sentKeys"] = list(sent_keys)
        save_report_state(state)
    except TossApiError:
        pass


def send_kakao_memo(env: dict[str, str], text: str) -> None:
    rest_key = env.get("KAKAO_REST_API_KEY", "")
    refresh_token = env.get("KAKAO_REFRESH_TOKEN", "")
    if not rest_key or not refresh_token:
        raise TossApiError(400, "kakao-env-missing", "카카오 REST API 키 또는 refresh token이 없습니다.")
    token_response = post_form_json(
        KAKAO_TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": rest_key,
            "refresh_token": refresh_token,
        },
    )
    access_token = token_response.get("access_token")
    if not access_token:
        raise TossApiError(401, "kakao-token-missing", "카카오 access token을 발급받지 못했습니다.")
    template = {
        "object_type": "text",
        "text": text[:900],
        "link": {"web_url": "http://127.0.0.1:4173", "mobile_web_url": "http://127.0.0.1:4173"},
        "button_title": "Orbit 열기",
    }
    post_form_json(
        KAKAO_MEMO_URL,
        {"template_object": json.dumps(template, ensure_ascii=False)},
        {"Authorization": f"Bearer {access_token}"},
    )


def handle_market_close_report(
    previous_market: str | None,
    current_market: str | None,
    env: dict[str, str],
    orders: list[dict[str, Any]],
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = load_report_state()
    reports = state.get("reports") or []
    slack_report_enabled = slack_enabled(env, "report")
    kakao_report_enabled = kakao_enabled(env)
    status = {
        "enabled": slack_report_enabled or kakao_report_enabled,
        "channels": {
            "slackReport": slack_report_enabled,
            "kakao": kakao_report_enabled,
        },
        "lastSentAt": None,
        "lastError": None,
    }
    if previous_market in ("KR", "US") and previous_market != current_market:
        report = build_market_close_report(previous_market, orders, summary, state.get("issues") or [])
        sent_keys = set(state.get("sentKeys") or [])
        if report["id"] not in sent_keys:
            sent_channels = []
            errors = []
            try:
                if slack_report_enabled:
                    send_slack("report", report["message"])
                    sent_channels.append("slack-report")
                if kakao_report_enabled:
                    send_kakao_memo(env, report["message"])
                    sent_channels.append("kakao")
            except TossApiError as exc:
                errors.append(exc.message)
            report["sent"] = bool(sent_channels)
            report["sentChannels"] = sent_channels
            if sent_channels:
                status["lastSentAt"] = report["createdAt"]
            if errors:
                report["error"] = " / ".join(errors)
                status["lastError"] = report["error"]
            elif not sent_channels:
                status["lastError"] = "리포트 웹훅이 꺼져 있어 리포트만 저장했습니다."
            reports.append(report)
            sent_keys.add(report["id"])
            state["sentKeys"] = list(sent_keys)
    state["lastActiveMarket"] = current_market
    state["reports"] = reports[-30:]
    save_report_state(state)
    return state["reports"], status


def operation_report_interval_minutes(env: dict[str, str]) -> int:
    try:
        minutes = int(str(env.get("SLACK_OPERATION_REPORT_INTERVAL_MINUTES", "30")).strip() or "30")
    except ValueError:
        minutes = 30
    return max(5, min(60, minutes))


def analysis_interval_seconds(env: dict[str, str]) -> int:
    try:
        seconds = int(str(env.get("ANALYSIS_INTERVAL_SECONDS", "10")).strip() or "10")
    except ValueError:
        seconds = 10
    return max(5, min(60, seconds))


def build_operation_report(
    market: str,
    session: str,
    results: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    now = datetime.now().astimezone()
    market_name = "한국장" if market == "KR" else "미국장" if market == "US" else market
    period = summary.get("periodReturns") or {}
    today = period.get("today") or {}
    decision = summary.get("decision") or {}
    top_candidates = [
        item for item in results
        if str(item.get("verdict") or "").strip()
    ][:3]
    candidate_lines = []
    for item in top_candidates:
        name = str(item.get("name") or item.get("symbol") or "-")
        verdict = str(item.get("verdict") or "분석 중")
        reason = str(item.get("reason") or "근거 수집 중")
        candidate_lines.append(f"- {name}: {verdict} · {reason}")
    if not candidate_lines:
        candidate_lines.append("- 후보 없음: 현재는 관망 구간")

    return "\n".join(
        [
            ":bar_chart: *Orbit 30분 운영 중간보고*",
            f"시장: {market_name} · {session}",
            f"시간: {now.strftime('%Y-%m-%d %H:%M %Z')}",
            "",
            f"오늘 단타 손익: {money(today.get('profitKrw'))} ({percent(today.get('returnRate'))})",
            f"보유 포지션: {summary.get('openPositionCount', 0)}개",
            f"오늘 진입: {summary.get('todayOrderCount', 0)}건",
            f"운용 판단: {decision.get('mode') or '분석 중'}",
            f"다음 행동: {decision.get('action') or '시장 강도 확인'}",
            "",
            "실시간 분석 요약:",
            *candidate_lines,
            "",
            f"서버 상태: 정상 · 분석 루프 {len(results)}개 후보 확인",
        ]
    )


def handle_operation_report(
    env: dict[str, str],
    market: str | None,
    session: str,
    results: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    if market not in ("KR", "US") or not slack_enabled(env, "log"):
        return
    interval = operation_report_interval_minutes(env)
    now = datetime.now().astimezone()
    minute_slot = (now.minute // interval) * interval if interval < 60 else 0
    hour_slot = now.hour
    key = f"operation-{now.strftime('%Y-%m-%d')}-{market}-{hour_slot:02d}{minute_slot:02d}"
    state = load_report_state()
    if state.get("lastOperationReportKey") == key:
        return
    try:
        send_slack("log", build_operation_report(market, session, results, orders, summary))
        state["lastOperationReportKey"] = key
        save_report_state(state)
    except TossApiError:
        # Operational reporting must never interrupt analysis or paper trading.
        pass


def get_token(env: dict[str, str], force_refresh: bool = False) -> str:
    with TOKEN_LOCK:
        if not force_refresh and TOKEN["value"] and time.time() < TOKEN["expires_at"]:
            return str(TOKEN["value"])
        response = request_json(
            "POST",
            "/oauth2/token",
            form={
                "grant_type": "client_credentials",
                "client_id": env.get("TOSS_CLIENT_ID", ""),
                "client_secret": env.get("TOSS_CLIENT_SECRET", ""),
            },
        )
        token = response.get("access_token")
        if not token:
            raise TossApiError(401, "missing-token", "액세스 토큰을 발급받지 못했습니다.")
        expires_in = int(response.get("expires_in", 3600))
        TOKEN["value"] = token
        TOKEN["expires_at"] = time.time() + max(60, expires_in - 60)
        return str(token)


def toss_get(path: str, env: dict[str, str], account_seq: int | None = None) -> dict[str, Any]:
    token = get_token(env)
    headers = {"Authorization": f"Bearer {token}"}
    if account_seq is not None:
        headers["X-Tossinvest-Account"] = str(account_seq)
    try:
        return request_json("GET", path, headers=headers)
    except TossApiError as exc:
        if exc.code != "expired-token":
            raise
        token = get_token(env, force_refresh=True)
        headers["Authorization"] = f"Bearer {token}"
        return request_json("GET", path, headers=headers)


def account_seq(env: dict[str, str]) -> int:
    accounts = toss_get("/api/v1/accounts", env).get("result") or []
    if not accounts:
        raise TossApiError(404, "account-not-found", "사용 가능한 계좌를 찾지 못했습니다.")
    configured = env.get("TOSS_ACCOUNT_SEQ", "")
    for account in accounts:
        if str(account.get("accountSeq")) == configured:
            return int(account["accountSeq"])
    brokerage = next(
        (item for item in accounts if item.get("accountType") == "BROKERAGE"), accounts[0]
    )
    return int(brokerage["accountSeq"])


def decimal(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_dashboard() -> dict[str, Any]:
    env = load_env()
    missing = [
        key
        for key in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET")
        if not env.get(key)
    ]
    if missing:
        return {"connected": False, "error": "API 인증 정보가 설정되지 않았습니다."}

    seq = account_seq(env)
    holdings = toss_get("/api/v1/holdings", env, seq).get("result") or {}
    exchange = toss_get(
        "/api/v1/exchange-rate?baseCurrency=USD&quoteCurrency=KRW", env
    ).get("result") or {}
    market_schedule(env)
    kr_calendar = CALENDAR_CACHE["KR"]
    us_calendar = CALENDAR_CACHE["US"]

    rate = decimal(exchange.get("midRate") or exchange.get("rate")) or 1
    market_value = holdings.get("marketValue", {}).get("amountAfterCost", {})
    profit_loss = holdings.get("profitLoss", {})
    daily = holdings.get("dailyProfitLoss", {})
    total_krw = decimal(market_value.get("krw")) + decimal(market_value.get("usd")) * rate
    total_profit_krw = decimal(
        profit_loss.get("amountAfterCost", {}).get("krw")
    ) + decimal(profit_loss.get("amountAfterCost", {}).get("usd")) * rate
    daily_profit_krw = decimal(daily.get("amount", {}).get("krw")) + decimal(
        daily.get("amount", {}).get("usd")
    ) * rate

    items = []
    for item in (holdings.get("items") or []):
        items.append(
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "marketCountry": item.get("marketCountry"),
                "currency": item.get("currency"),
                "quantity": item.get("quantity"),
                "lastPrice": item.get("lastPrice"),
                "marketValue": item.get("marketValue", {}).get("amountAfterCost"),
                "profitRate": item.get("profitLoss", {}).get("rateAfterCost"),
                "dailyRate": item.get("dailyProfitLoss", {}).get("rate"),
            }
        )

    return {
        "connected": True,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": {
            "totalKrw": round(total_krw),
            "profitKrw": round(total_profit_krw),
            "profitRate": decimal(profit_loss.get("rateAfterCost")),
            "dailyProfitKrw": round(daily_profit_krw),
            "dailyProfitRate": decimal(daily.get("rate")),
            "usdKrw": rate,
            "holdingCount": len(items),
        },
        "holdings": items,
        "markets": {
            "kr": kr_calendar.get("today"),
            "us": us_calendar.get("today"),
        },
    }


def analyze_holdings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for item in items:
        daily_rate = decimal(item.get("dailyRate"))
        quantity = decimal(item.get("quantity"))
        if abs(daily_rate) >= 0.03:
            verdict, reason = "진입 불가", "당일 변동성 과다"
        elif quantity > 0:
            verdict, reason = "추가 진입 보류", "기보유 포지션"
        else:
            verdict, reason = "분석 중", "전략 신호 대기"
        results.append(
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "verdict": verdict,
                "reason": reason,
                "dailyRate": daily_rate,
            }
        )
    return results


def market_schedule(env: dict[str, str]) -> tuple[Any, str]:
    if time.time() >= CALENDAR_CACHE["expiresAt"]:
        CALENDAR_CACHE["KR"] = toss_get("/api/v1/market-calendar/KR", env).get("result") or {}
        CALENDAR_CACHE["US"] = toss_get("/api/v1/market-calendar/US", env).get("result") or {}
        CALENDAR_CACHE["expiresAt"] = time.time() + 300

    now = datetime.now().astimezone()

    def is_open(session: dict[str, Any]) -> bool:
        try:
            start = datetime.fromisoformat(str(session["startTime"]))
            end = datetime.fromisoformat(str(session["endTime"]))
            return start <= now < end
        except (KeyError, TypeError, ValueError):
            return False

    kr_today = (CALENDAR_CACHE["KR"].get("today") or {}).get("integrated") or {}
    if is_open(kr_today.get("regularMarket") or {}):
        return "KR", "KR 정규장"

    us_today = CALENDAR_CACHE["US"].get("today") or {}
    us_sessions = (
        ("dayMarket", "US 데이마켓"),
        ("preMarket", "US 프리마켓"),
        ("regularMarket", "US 정규장"),
        ("afterMarket", "US 애프터마켓"),
    )
    for key, label in us_sessions:
        if is_open(us_today.get(key) or {}):
            return "US", label
    return None, "시장 휴장"


def new_paper_state() -> dict[str, Any]:
    return {
        "schemaVersion": PAPER_SCHEMA_VERSION,
        "startingCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
        "allocationMode": "unlimited-paper-experience",
        "currency": "KRW",
        "resetAt": now_kst().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "orders": [],
    }


def save_paper_state(state: dict[str, Any]) -> None:
    PAPER_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_paper_state() -> dict[str, Any]:
    if not PAPER_PATH.exists():
        state = new_paper_state()
        save_paper_state(state)
        return state
    try:
        state = json.loads(PAPER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict) or int(state.get("schemaVersion") or 0) != PAPER_SCHEMA_VERSION:
        state = new_paper_state()
        save_paper_state(state)
        save_journal_state({"notes": {}, "reviews": {}})
    state.setdefault("startingCapitalKrw", PAPER_STARTING_CAPITAL_KRW)
    state["allocationMode"] = "unlimited-paper-experience"
    state.setdefault("currency", "KRW")
    state.setdefault("orders", [])
    return state


def load_paper_orders() -> list[dict[str, Any]]:
    orders = load_paper_state().get("orders") or []
    return orders if isinstance(orders, list) else []


def save_paper_orders(orders: list[dict[str, Any]]) -> None:
    state = load_paper_state()
    state["orders"] = orders
    save_paper_state(state)


def parse_order_time(value: Any) -> datetime | None:
    raw = str(value or "")
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def paper_trading_day(value: Any = None) -> str:
    """Return the KR-open trading day (09:00 KST through the next US close)."""
    moment = value if isinstance(value, datetime) else parse_order_time(value)
    if moment is None:
        moment = now_kst()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=KST)
    shifted = moment.astimezone(KST) - timedelta(hours=9)
    return shifted.strftime("%Y-%m-%d")


def start_of_week(now: datetime) -> datetime:
    start_day = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    return start_day - timedelta(days=now.weekday())


def paper_trade_ledger(
    orders: list[dict[str, Any]], results_by_symbol: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build one P&L record per paper trade, pairing each BUY with its SELL."""
    buys_by_id: dict[str, dict[str, Any]] = {}
    open_by_symbol: dict[tuple[str, str], dict[str, Any]] = {}
    closed_entry_ids: set[str] = set()
    trades: list[dict[str, Any]] = []

    for order in sorted(orders, key=lambda item: str(item.get("createdAt") or "")):
        symbol = str(order.get("symbol") or "")
        if not symbol:
            continue
        side = str(order.get("side") or "").upper()
        market = str(order.get("market") or "")
        key = (market, symbol)
        if side == "BUY":
            order_id = str(order.get("id") or "")
            if order_id:
                buys_by_id[order_id] = order
            open_by_symbol[key] = order
            continue
        if side != "SELL":
            continue

        entry_id = str(order.get("entryOrderId") or "")
        entry = buys_by_id.get(entry_id) if entry_id else open_by_symbol.get(key)
        if not entry:
            continue
        resolved_entry_id = str(entry.get("id") or entry_id)
        if resolved_entry_id in closed_entry_ids:
            continue
        quantity = decimal(order.get("quantity") or entry.get("quantity") or 1)
        entry_price = decimal(order.get("entryPrice") or entry.get("price"))
        exit_price = decimal(order.get("price"))
        invested = entry_price * quantity
        profit = (
            decimal(order.get("profit"))
            if order.get("profit") is not None
            else (exit_price - entry_price) * quantity
        )
        return_rate = (
            decimal(order.get("returnRate"))
            if order.get("returnRate") is not None
            else (profit / invested if invested else 0.0)
        )
        trades.append(
            {
                "entryOrderId": resolved_entry_id,
                "exitOrderId": str(order.get("id") or ""),
                "market": market or entry.get("market"),
                "symbol": symbol,
                "openedAt": str(entry.get("createdAt") or ""),
                "closedAt": str(order.get("createdAt") or ""),
                "status": "CLOSED",
                "quantity": quantity,
                "entryPrice": entry_price,
                "lastPrice": exit_price,
                "invested": invested,
                "profit": profit,
                "returnRate": return_rate,
            }
        )
        closed_entry_ids.add(resolved_entry_id)
        if open_by_symbol.get(key) is entry:
            open_by_symbol.pop(key, None)

    for (market, symbol), entry in open_by_symbol.items():
        entry_id = str(entry.get("id") or "")
        if entry_id in closed_entry_ids:
            continue
        current = results_by_symbol.get(symbol) or {}
        quantity = decimal(entry.get("quantity") or 1)
        entry_price = decimal(entry.get("price"))
        last_price = decimal(current.get("lastPrice") or entry.get("lastPrice") or entry_price)
        invested = entry_price * quantity
        profit = (last_price - entry_price) * quantity if entry_price and last_price else 0.0
        trades.append(
            {
                "entryOrderId": entry_id,
                "exitOrderId": "",
                "market": market,
                "symbol": symbol,
                "openedAt": str(entry.get("createdAt") or ""),
                "closedAt": "",
                "status": "OPEN",
                "quantity": quantity,
                "entryPrice": entry_price,
                "lastPrice": last_price,
                "invested": invested,
                "profit": profit,
                "returnRate": profit / invested if invested else 0.0,
            }
        )
    return trades


def period_profit_summary(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = now_kst()
    trading_today = datetime.strptime(paper_trading_day(now), "%Y-%m-%d")
    starts = {
        "month": datetime(trading_today.year, trading_today.month, 1),
        "week": start_of_week(trading_today),
        "today": trading_today,
    }
    summary = {
        key: {"profitKrw": 0, "investedKrw": 0, "returnRate": 0.0, "positionCount": 0}
        for key in starts
    }

    for trade in trades:
        trading_day = paper_trading_day(trade.get("closedAt") or trade.get("openedAt"))
        try:
            created_at = datetime.strptime(trading_day, "%Y-%m-%d")
        except ValueError:
            continue
        invested = decimal(trade.get("invested"))
        profit = decimal(trade.get("profit"))
        for key, start in starts.items():
            if created_at >= start:
                summary[key]["profitKrw"] += profit
                summary[key]["investedKrw"] += invested
                summary[key]["positionCount"] += 1

    for item in summary.values():
        invested = item["investedKrw"]
        item["returnRate"] = item["profitKrw"] / invested if invested else 0.0
    return summary


def paper_capital_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    state = load_paper_state()
    starting = decimal(state.get("startingCapitalKrw") or PAPER_STARTING_CAPITAL_KRW)
    closed = [item for item in trades if item.get("status") == "CLOSED"]
    opened = [item for item in trades if item.get("status") == "OPEN"]
    realized = sum(decimal(item.get("profit")) for item in closed)
    unrealized = sum(decimal(item.get("profit")) for item in opened)
    open_invested = sum(decimal(item.get("invested")) for item in opened)
    cash = max(0.0, starting + realized - open_invested)
    working_capital = max(0.0, starting + realized)
    reference_capital = max(1.0, starting)
    equity = starting + realized + unrealized
    virtual_funding = max(0.0, open_invested - working_capital)
    utilization_rate = open_invested / reference_capital
    return {
        "startingCapitalKrw": starting,
        "referenceCapitalKrw": reference_capital,
        "workingCapitalKrw": working_capital,
        "cashKrw": cash,
        "openInvestedKrw": open_invested,
        "targetInvestedKrw": None,
        "remainingDeployableKrw": None,
        "cashReserveKrw": 0.0,
        "virtualFundingKrw": virtual_funding,
        "fundingLimit": "UNLIMITED",
        "referenceOnly": True,
        "realizedProfitKrw": realized,
        "unrealizedProfitKrw": unrealized,
        "equityKrw": equity,
        "returnRate": (equity - starting) / starting if starting else 0.0,
        "utilizationRate": utilization_rate,
        "targetUtilizationRate": None,
        "reserveRate": 0.0,
        "utilizationStatus": "무제한 경험 축적",
        "currency": "KRW",
        "allocationMode": "unlimited-paper-experience",
    }


def confidence_allocation_rate(score: Any) -> float:
    normalized = clamp(score, 80, 100, 80)
    return 0.15 + ((normalized - 80) / 20) * 0.45


def adaptive_allocation_plan(
    capital: dict[str, Any],
    score: Any,
    learning_scale: Any,
    open_positions: int,
    max_open_positions: int,
) -> dict[str, Any]:
    """Size each PAPER experience independently while preserving learned risk cuts."""
    working_capital = max(1.0, decimal(capital.get("referenceCapitalKrw")) or PAPER_STARTING_CAPITAL_KRW)
    invested = max(0.0, decimal(capital.get("openInvestedKrw")))
    confidence_rate = max(
        PAPER_MIN_EXPERIENCE_ENTRY_RATE,
        min(PAPER_MAX_SINGLE_POSITION_RATE, confidence_allocation_rate(score)),
    )
    confidence_budget = working_capital * confidence_rate
    base_budget = confidence_budget
    applied_learning_scale = clamp(learning_scale, 0.40, 1.0, 1.0)
    planned_budget = base_budget * applied_learning_scale
    return {
        "mode": "unlimited-paper-experience",
        "workingCapitalKrw": working_capital,
        "referenceCapitalKrw": working_capital,
        "targetInvestedKrw": None,
        "investedBeforeKrw": invested,
        "availableCashKrw": None,
        "remainingTargetKrw": None,
        "remainingSlots": None,
        "confidenceAllocationRate": confidence_rate,
        "balancedSlotBudgetKrw": 0.0,
        "baseBudgetKrw": base_budget,
        "baseAllocationRate": base_budget / working_capital if working_capital else 0.0,
        "learningScale": applied_learning_scale,
        "plannedBudgetKrw": planned_budget,
        "plannedAllocationRate": planned_budget / working_capital if working_capital else 0.0,
        "expectedUtilizationRate": (
            (invested + planned_budget) / working_capital if working_capital else 0.0
        ),
        "targetUtilizationRate": None,
        "reserveRate": 0.0,
        "fundingLimit": "UNLIMITED",
    }



def trading_decision(
    average_return: float,
    open_positions: int,
    today_orders: int,
    locked: bool,
    lock_reason: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    target_rate = decimal(config.get("targetRate"))
    stop_rate = decimal(config.get("stopRate"))
    max_open_positions = int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS)
    remaining_to_stop = average_return - stop_rate
    remaining_to_target = target_rate - average_return
    stop_progress = 0.0
    if stop_rate < 0:
        stop_progress = max(0.0, min(1.0, abs(min(average_return, 0.0)) / abs(stop_rate)))

    if (
        PAPER_LEARNING_SPRINT_MODE
        and not PAPER_UNLIMITED_OPEN_POSITIONS
        and open_positions >= max_open_positions
    ):
        mode = "학습 대기"
        tone = "caution"
        action = "청산 자리 발생 시 점수순 재진입"
        reason = "동시 포지션 3개를 모두 사용 중입니다."
    elif PAPER_LEARNING_SPRINT_MODE:
        mode = "경험 가속"
        tone = "safe" if average_return >= 0 else "caution"
        action = "좋은 후보는 자금·횟수·포지션 제한 없이 진입"
        reason = "점수와 개별 손절은 유지하고 성공·실패 표본을 빠르게 쌓습니다."
    elif locked and average_return >= target_rate:
        mode = "목표 달성"
        tone = "safe"
        action = "오늘 신규 진입 잠금, 수익 보존"
        reason = lock_reason or "일 목표 수익률을 달성했습니다."
    elif locked:
        mode = "거래 중지"
        tone = "danger"
        action = "신규 진입 금지, 보유 포지션 점검"
        reason = lock_reason or "손실 한도에 도달했습니다."
    elif average_return <= stop_rate * 0.8:
        mode = "방어 모드"
        tone = "danger"
        action = "신규 진입 제한, 손절 기준 확인"
        reason = "손실선에 근접했습니다."
    elif average_return < 0:
        mode = "주의 모드"
        tone = "caution"
        action = "추가 진입보다 기존 포지션 관찰"
        reason = "단타 평가손익이 마이너스 구간입니다."
    elif open_positions >= max_open_positions:
        mode = "관망 모드"
        tone = "caution"
        action = "포지션 과밀, 신규 진입은 신중하게"
        reason = "오늘 허용 포지션을 대부분 사용했습니다."
    elif average_return >= target_rate * 0.5:
        mode = "공격 가능"
        tone = "safe"
        action = "추세 확인 후 선별 진입"
        reason = "일 목표의 절반 이상을 달성 중입니다."
    else:
        mode = "균형 모드"
        tone = "neutral"
        action = "시장 강도 확인 후 소량 진입"
        reason = "손익과 리스크가 관리 가능한 범위입니다."

    return {
        "mode": mode,
        "tone": tone,
        "reason": reason,
        "action": action,
        "stopProgress": stop_progress,
        "remainingToStop": remaining_to_stop,
        "remainingToTarget": remaining_to_target,
        "targetRate": target_rate,
        "stopRate": stop_rate,
        "currentRate": average_return,
        "openPositionCount": open_positions,
        "todayOrderCount": today_orders,
    }


def technical_review(positions: dict[str, dict[str, Any]], results_by_symbol: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reviews: list[dict[str, Any]] = []
    for symbol, order in positions.items():
        current = results_by_symbol.get(symbol) or {}
        entry = decimal(order.get("price"))
        last = decimal(current.get("lastPrice"))
        rate = (last - entry) / entry if entry and last else decimal(current.get("dailyRate"))
        reviews.append(
            {
                "symbol": symbol,
                "name": order.get("name") or current.get("name") or symbol,
                "returnRate": rate,
                "verdict": current.get("verdict") or "관찰",
                "reason": current.get("reason") or order.get("reason") or "가격 흐름 확인",
            }
        )
    if not reviews:
        return {"winRate": 0.0, "best": None, "worst": None, "reviews": []}
    wins = [item for item in reviews if decimal(item.get("returnRate")) > 0]
    ranked = sorted(reviews, key=lambda item: decimal(item.get("returnRate")), reverse=True)
    return {
        "winRate": len(wins) / len(reviews),
        "best": ranked[0],
        "worst": ranked[-1],
        "reviews": ranked,
    }


def safety_rules(
    average_return: float,
    open_positions: int,
    today_order_count: int,
    position_returns: list[float],
    locked: bool,
    lock_reason: str | None,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    stop_rate = decimal(config.get("stopRate"))
    max_daily_orders = int(config.get("maxDailyOrders") or PAPER_MAX_DAILY_ORDERS)
    max_open_positions = int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS)
    max_losses = int(config.get("maxConsecutiveLosses") or PAPER_MAX_CONSECUTIVE_LOSSES)
    consecutive_losses = 0
    for value in reversed(position_returns):
        if value < 0:
            consecutive_losses += 1
        else:
            break
    learning_sprint = PAPER_LEARNING_SPRINT_MODE
    rules = [
        {
            "key": "dailyLoss",
            "label": "일 손익 관찰" if learning_sprint else "일 손실 한도",
            "status": "기록" if learning_sprint else ("잠금" if average_return <= stop_rate else "정상"),
            "tone": "safe" if learning_sprint else ("danger" if average_return <= stop_rate else "safe"),
            "detail": (
                f"현재 {percent(average_return)} · 신규 진입 잠금 없음"
                if learning_sprint
                else f"현재 {percent(average_return)} / 기준 {percent(stop_rate)}"
            ),
        },
        {
            "key": "dailyOrders",
            "label": "일 진입 횟수",
            "status": "무제한" if learning_sprint else ("상한" if today_order_count >= max_daily_orders else "여유"),
            "tone": "safe" if learning_sprint else ("danger" if today_order_count >= max_daily_orders else "safe"),
            "detail": (
                f"오늘 {today_order_count}건 · PAPER 오답 표본 축적"
                if learning_sprint
                else f"{today_order_count}/{max_daily_orders}건 사용"
            ),
        },
        {
            "key": "positionCap",
            "label": "포지션 수",
            "status": "무제한" if PAPER_UNLIMITED_OPEN_POSITIONS else ("과밀" if open_positions >= max_open_positions else "정상"),
            "tone": "safe" if PAPER_UNLIMITED_OPEN_POSITIONS else ("danger" if open_positions >= max_open_positions else "safe"),
            "detail": (
                f"현재 {open_positions}개 · 우수 후보 추가 진입 가능"
                if PAPER_UNLIMITED_OPEN_POSITIONS
                else f"{open_positions}/{max_open_positions}개 보유"
            ),
        },
        {
            "key": "lossStreak",
            "label": "연속 손실",
            "status": "학습" if learning_sprint else ("정지" if consecutive_losses >= max_losses else "정상"),
            "tone": "safe" if learning_sprint else ("danger" if consecutive_losses >= max_losses else "safe"),
            "detail": (
                f"최근 손실 {consecutive_losses}회 · 종목별 오답에 반영"
                if learning_sprint
                else f"최근 손실 {consecutive_losses}회 / 기준 {max_losses}회"
            ),
        },
        {
            "key": "paperMode",
            "label": "실주문 보호",
            "status": "PAPER",
            "tone": "safe",
            "detail": "실제 주문 전송 없음",
        },
    ]
    if locked and not learning_sprint:
        rules.insert(0, {"key": "lock", "label": "오늘 거래 잠금", "status": "ON", "tone": "danger", "detail": lock_reason or "운용 잠금"})
    return rules


def safety_gate(summary: dict[str, Any]) -> dict[str, Any]:
    rules = summary.get("safetyRules") or []
    if PAPER_LEARNING_SPRINT_MODE and PAPER_UNLIMITED_OPEN_POSITIONS:
        blocking_keys: set[str] = set()
    elif PAPER_LEARNING_SPRINT_MODE:
        blocking_keys = {"positionCap"}
    else:
        blocking_keys = {"lock", "dailyLoss", "positionCap", "lossStreak"}
    blockers = [rule for rule in rules if rule.get("tone") == "danger" and rule.get("key") in blocking_keys]
    return {
        "blocked": bool(blockers),
        "reason": str(blockers[0].get("detail") or blockers[0].get("label")) if blockers else "신규 진입 가능",
        "blockers": blockers,
    }


def paper_summary(orders: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    config = strategy_config()
    target_rate = decimal(config.get("targetRate"))
    stop_rate = decimal(config.get("stopRate"))
    today = paper_trading_day()
    today_orders = [
        item for item in orders
        if str(item.get("side") or "").upper() == "BUY"
        and paper_trading_day(item.get("createdAt")) == today
    ]
    positions: dict[str, dict[str, Any]] = {}
    for order in orders:
        symbol = str(order.get("symbol") or "")
        if not symbol:
            continue
        side = str(order.get("side") or "").upper()
        if side == "BUY":
            positions[symbol] = order
        elif side == "SELL":
            positions.pop(symbol, None)

    results_by_symbol = {str(item.get("symbol")): item for item in results}
    trade_ledger = paper_trade_ledger(orders, results_by_symbol)
    capital = paper_capital_summary(trade_ledger)
    position_returns = []
    for symbol, order in positions.items():
        current = results_by_symbol.get(symbol)
        entry = decimal(order.get("price"))
        last = decimal((current or {}).get("lastPrice"))
        if entry and last:
            position_returns.append((last - entry) / entry)

    average_return = (
        sum(position_returns) / len(position_returns) if position_returns else 0.0
    )
    tech_review = technical_review(positions, results_by_symbol)
    target_hit = average_return >= target_rate
    stop_hit = average_return <= stop_rate
    locked = (target_hit or stop_hit) and not PAPER_LEARNING_SPRINT_MODE
    lock_reason = None
    if target_hit and not PAPER_LEARNING_SPRINT_MODE:
        lock_reason = f"일 목표 {percent(target_rate)} 도달 · 신규 진입 잠금"
    elif stop_hit and not PAPER_LEARNING_SPRINT_MODE:
        lock_reason = f"손실폭 {percent(stop_rate)} 도달 · 신규 진입 중지"

    return {
        "targetRate": target_rate,
        "stopRate": stop_rate,
        "strategyConfig": config,
        "capital": capital,
        "learningCoverage": "ALL_NEW_ENTRIES",
        "capitalAllocationPolicy": {
            "mode": "unlimited-paper-experience",
            "referenceCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
            "fundingLimit": "UNLIMITED" if PAPER_UNLIMITED_VIRTUAL_CAPITAL else PAPER_STARTING_CAPITAL_KRW,
            "maxSinglePositionRate": PAPER_MAX_SINGLE_POSITION_RATE,
            "minExperienceEntryRate": PAPER_MIN_EXPERIENCE_ENTRY_RATE,
            "learningAppliedAfterSizing": True,
        },
        "paperLearningSprint": {
            "enabled": PAPER_LEARNING_SPRINT_MODE,
            "entryLimit": "UNLIMITED" if PAPER_LEARNING_SPRINT_MODE else int(config.get("maxDailyOrders") or PAPER_MAX_DAILY_ORDERS),
            "dailyProfitLock": False if PAPER_LEARNING_SPRINT_MODE else True,
            "lossStreakLock": False if PAPER_LEARNING_SPRINT_MODE else True,
            "scoreFilter": True,
            "symbolLearning": True,
            "individualStops": True,
            "maxOpenPositions": "UNLIMITED" if PAPER_UNLIMITED_OPEN_POSITIONS else int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS),
            "fundingLimit": "UNLIMITED" if PAPER_UNLIMITED_VIRTUAL_CAPITAL else PAPER_STARTING_CAPITAL_KRW,
        },
        "averageReturn": average_return,
        "periodReturns": period_profit_summary(trade_ledger),
        "technicalReview": tech_review,
        "safetyRules": safety_rules(average_return, len(positions), len(today_orders), position_returns, locked, lock_reason, config),
        "todayOrderCount": len(today_orders),
        "openPositionCount": len(positions),
        "locked": locked,
        "lockReason": lock_reason,
        "decision": trading_decision(
            average_return, len(positions), len(today_orders), locked, lock_reason, config
        ),
    }


def open_paper_positions(orders: list[dict[str, Any]], market: str | None = None) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for order in orders:
        symbol = str(order.get("symbol") or "")
        if not symbol:
            continue
        side = str(order.get("side") or "").upper()
        if market and order.get("market") != market:
            continue
        if side == "BUY":
            positions[symbol] = order
        elif side == "SELL":
            positions.pop(symbol, None)
    return positions


def extract_stock_price(stock: dict[str, Any]) -> Any:
    price = stock.get("price") if isinstance(stock.get("price"), dict) else {}
    return (
        stock.get("lastPrice")
        or price.get("lastPrice")
        or price.get("close")
        or price.get("tradePrice")
        or (stock.get("price") if not isinstance(stock.get("price"), dict) else None)
    )


def refresh_position_prices(
    env: dict[str, str],
    positions: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, float]:
    prices = {
        str(item.get("symbol")): decimal(item.get("lastPrice"))
        for item in results
        if item.get("symbol") and decimal(item.get("lastPrice"))
    }
    missing = [symbol for symbol in positions if not prices.get(symbol)]
    if missing:
        try:
            stocks = toss_get(
                f"/api/v1/stocks?{urllib.parse.urlencode({'symbols': ','.join(missing)})}",
                env,
            ).get("result") or []
            for stock in stocks:
                symbol = str(stock.get("symbol") or "")
                price = decimal(extract_stock_price(stock))
                if symbol and price:
                    order = positions.get(symbol) or {}
                    if str(order.get("sourceCurrency") or "") == "USD":
                        price *= decimal(order.get("fxRate")) or usd_krw_rate(env)
                    prices[symbol] = price
        except TossApiError:
            pass
    return prices


def close_paper_positions_if_needed(
    env: dict[str, str],
    orders: list[dict[str, Any]],
    results: list[dict[str, Any]],
    market: str,
    session: str,
) -> tuple[list[dict[str, Any]], bool]:
    config = strategy_config()
    target_rate = decimal(config.get("targetRate"))
    stop_rate = decimal(config.get("stopRate"))
    positions = open_paper_positions(orders, market)
    if not positions:
        return orders, False
    prices = refresh_position_prices(env, positions, results)
    changed = False
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for symbol, order in list(positions.items()):
        entry = decimal(order.get("price"))
        last = prices.get(symbol, 0.0)
        if not entry or not last:
            continue
        rate = (last - entry) / entry
        exit_kind = None
        reason = None
        if rate <= stop_rate:
            exit_kind = "손실선"
            reason = f"손실선 {percent(stop_rate)} 도달 · 즉시 모의청산"
        elif rate >= target_rate:
            exit_kind = "목표"
            reason = f"목표 {percent(target_rate)} 도달 · 즉시 모의청산"
        if not exit_kind:
            continue
        orders.append(
            {
                "id": f"PAPER-EXIT-{int(time.time())}-{symbol}",
                "market": order.get("market"),
                "session": session,
                "symbol": symbol,
                "name": order.get("name"),
                "side": "SELL",
                "quantity": decimal(order.get("quantity") or 1),
                "price": last,
                "entryPrice": entry,
                "entryOrderId": order.get("id"),
                "currency": order.get("currency"),
                "sourceCurrency": order.get("sourceCurrency"),
                "fxRate": order.get("fxRate") or 1,
                "status": "FILLED",
                "createdAt": now,
                "reason": reason,
                "exitKind": exit_kind,
                "stopRate": stop_rate,
                "targetRate": target_rate,
                "returnRate": rate,
                "profit": (last - entry) * decimal(order.get("quantity") or 1),
            }
        )
        changed = True
    if changed:
        save_paper_orders(orders)
    return orders, changed


def paper_trade(
    env: dict[str, str], results: list[dict[str, Any]], market: str, session: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    orders = load_paper_orders()
    orders, _ = close_paper_positions_if_needed(env, orders, results, market, session)
    results_by_symbol = {str(item.get("symbol") or ""): item for item in results if item.get("symbol")}
    learning_state = sync_learning_brain(orders, results_by_symbol)
    learning_brain = learning_brain_payload(learning_state)
    summary = paper_summary(orders, results)
    summary["learningBrain"] = learning_brain.get("summary")
    summary["learningCoverage"] = "ALL_NEW_ENTRIES"
    summary["capitalAllocationPolicy"] = {
        "mode": "unlimited-paper-experience",
        "referenceCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
        "fundingLimit": "UNLIMITED",
        "maxSinglePositionRate": PAPER_MAX_SINGLE_POSITION_RATE,
        "minExperienceEntryRate": PAPER_MIN_EXPERIENCE_ENTRY_RATE,
        "learningAppliedAfterSizing": True,
    }
    summary["learningDecisions"] = []
    gate = safety_gate(summary)
    if summary["locked"] or gate["blocked"]:
        return orders[-50:], summary

    today = paper_trading_day()
    todays_market_orders = [
        item
        for item in orders
        if str(item.get("side") or "").upper() == "BUY"
        and item.get("market") == market
        and paper_trading_day(item.get("createdAt")) == today
    ]
    config = strategy_config()
    if (
        not PAPER_LEARNING_SPRINT_MODE
        and len(todays_market_orders) >= int(config.get("maxDailyOrders") or PAPER_MAX_DAILY_ORDERS)
    ):
        return orders[-50:], summary
    existing = {(item.get("market"), item.get("symbol")) for item in open_paper_positions(orders).values()}
    capital = summary.get("capital") or {}
    max_open_positions = int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS)
    open_position_count = int(summary.get("openPositionCount") or 0)
    candidate = None
    candidate_policy = None
    candidate_capital_plan = None
    quantity = 0
    allocation_rate = 0.0
    allocated_krw = 0.0
    learning_decisions: list[dict[str, Any]] = []
    ranked_candidates = sorted(
        (
            item for item in results
            if item.get("verdict") == "정밀 분석"
            and (market, item.get("symbol")) not in existing
        ),
        key=lambda item: (decimal(item.get("score")), -decimal(item.get("rank") or 999)),
        reverse=True,
    )
    for item in ranked_candidates:
        symbol = str(item.get("symbol") or "")
        policy = learning_entry_policy(symbol, item.get("score"), learning_state)
        decision = dict(policy)
        decision["name"] = item.get("name") or symbol
        if not policy.get("allowed"):
            decision["capitalAllowed"] = False
            learning_decisions.append(decision)
            continue
        price_krw = decimal(item.get("lastPrice"))
        if price_krw <= 0:
            decision["capitalAllowed"] = False
            decision["reason"] = f"{policy.get('reason')} · 가격 확인 실패"
            learning_decisions.append(decision)
            continue
        capital_plan = adaptive_allocation_plan(
            capital,
            item.get("score"),
            policy.get("allocationScale"),
            open_position_count,
            max_open_positions,
        )
        budget = decimal(capital_plan.get("plannedBudgetKrw"))
        decision["capitalAllowed"] = budget > 0
        decision["capitalPlan"] = capital_plan
        if budget <= 0:
            decision["reason"] = f"{policy.get('reason')} · 배정액 계산 실패"
            learning_decisions.append(decision)
            continue
        shares = int(budget // price_krw)
        if shares < 1 and PAPER_UNLIMITED_VIRTUAL_CAPITAL:
            shares = 1
            decision["reason"] = f"{policy.get('reason')} · 고가 종목 최소 1주 경험 진입"
        elif shares < 1:
            decision["capitalAllowed"] = False
            decision["reason"] = f"{policy.get('reason')} · 배정액으로 1주 미만"
            learning_decisions.append(decision)
            continue
        learning_decisions.append(decision)
        candidate = item
        candidate_policy = policy
        candidate_capital_plan = capital_plan
        quantity = shares
        allocated_krw = price_krw * shares
        working_capital = decimal(capital_plan.get("workingCapitalKrw"))
        allocation_rate = allocated_krw / working_capital if working_capital else 0.0
        break
    if candidate:
        orders.append(
            {
                "id": f"PAPER-{int(time.time())}",
                "market": market,
                "session": session,
                "symbol": candidate.get("symbol"),
                "name": candidate.get("name"),
                "side": "BUY",
                "quantity": quantity,
                "price": candidate.get("lastPrice"),
                "currency": "KRW",
                "sourceCurrency": candidate.get("sourceCurrency") or candidate.get("currency"),
                "sourcePrice": candidate.get("sourcePrice"),
                "fxRate": candidate.get("fxRate") or 1,
                "allocationRate": allocation_rate,
                "plannedAllocationRate": candidate_capital_plan.get("plannedAllocationRate") if candidate_capital_plan else allocation_rate,
                "baseAllocationRate": candidate_capital_plan.get("baseAllocationRate") if candidate_capital_plan else confidence_allocation_rate(candidate.get("score")),
                "confidenceAllocationRate": candidate_capital_plan.get("confidenceAllocationRate") if candidate_capital_plan else confidence_allocation_rate(candidate.get("score")),
                "allocatedKrw": allocated_krw,
                "capitalPolicy": {
                    "mode": "unlimited-paper-experience",
                    "referenceCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
                    "fundingLimit": "UNLIMITED",
                    "investedBeforeKrw": candidate_capital_plan.get("investedBeforeKrw") if candidate_capital_plan else 0,
                    "targetInvestedKrw": None,
                    "remainingSlots": None,
                    "expectedUtilizationRate": (
                        (decimal(candidate_capital_plan.get("investedBeforeKrw")) + allocated_krw)
                        / decimal(candidate_capital_plan.get("workingCapitalKrw"))
                        if candidate_capital_plan and decimal(candidate_capital_plan.get("workingCapitalKrw"))
                        else allocation_rate
                    ),
                    "learningAppliedAfterSizing": True,
                },
                "status": "FILLED",
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "reason": candidate.get("reason"),
                "entryScore": candidate.get("score"),
                "learningPolicy": {
                    "requiredScore": candidate_policy.get("requiredScore") if candidate_policy else LEARNING_BASE_ENTRY_SCORE,
                    "candidateScore": candidate_policy.get("candidateScore") if candidate_policy else candidate.get("score"),
                    "allocationScale": candidate_policy.get("allocationScale") if candidate_policy else 1.0,
                    "status": candidate_policy.get("status") if candidate_policy else "신규 학습",
                    "traits": candidate_policy.get("traits") if candidate_policy else ["표본 수집"],
                    "reason": candidate_policy.get("reason") if candidate_policy else "신규 종목 기본 기준",
                    "appliedImmediately": True,
                    "appliedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                "strategyIds": [
                    "liquidity-momentum-filter",
                    "score-entry-80",
                    "adaptive-capital-utilization",
                    "paper-learning-sprint",
                    "unlimited-paper-experience",
                ],
            }
        )
        save_paper_orders(orders)
        summary = paper_summary(orders, results)
    summary["learningBrain"] = learning_brain.get("summary")
    summary["learningCoverage"] = "ALL_NEW_ENTRIES"
    summary["capitalAllocationPolicy"] = {
        "mode": "unlimited-paper-experience",
        "referenceCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
        "fundingLimit": "UNLIMITED",
        "maxSinglePositionRate": PAPER_MAX_SINGLE_POSITION_RATE,
        "minExperienceEntryRate": PAPER_MIN_EXPERIENCE_ENTRY_RATE,
        "learningAppliedAfterSizing": True,
    }
    summary["learningDecisions"] = learning_decisions[:10]
    return orders[-50:], summary


def usd_krw_rate(env: dict[str, str]) -> float:
    if time.time() < decimal(FX_CACHE.get("expiresAt")) and decimal(FX_CACHE.get("usdKrw")) > 0:
        return decimal(FX_CACHE.get("usdKrw"))
    exchange = toss_get(
        "/api/v1/exchange-rate?baseCurrency=USD&quoteCurrency=KRW", env
    ).get("result") or {}
    rate = decimal(exchange.get("midRate") or exchange.get("rate"))
    if rate <= 0:
        raise TossApiError(502, "exchange-rate-missing", "미국 종목 원화 환산 환율을 불러오지 못했습니다.")
    FX_CACHE["usdKrw"] = rate
    FX_CACHE["expiresAt"] = time.time() + 60
    return rate


def scan_market(env: dict[str, str], market: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "type": "MARKET_TRADING_AMOUNT",
            "marketCountry": market,
            "duration": "realtime",
            "excludeInvestmentCaution": "true",
            "count": "50",
        }
    )
    ranked = toss_get(f"/api/v1/rankings?{query}", env).get("result") or {}
    rows = ranked.get("rankings") or []
    symbols = [str(row.get("symbol")) for row in rows if row.get("symbol")]
    stocks = toss_get(
        f"/api/v1/stocks?{urllib.parse.urlencode({'symbols': ','.join(symbols)})}", env
    ).get("result") or []
    names = {str(stock.get("symbol")): stock.get("name") for stock in stocks}
    exchange_rate = usd_krw_rate(env) if market == "US" else 1.0
    results = []
    for row in rows:
        price = row.get("price") or {}
        source_currency = str(row.get("currency") or ("USD" if market == "US" else "KRW"))
        source_price = decimal(price.get("lastPrice"))
        normalized_price = source_price * exchange_rate if source_currency == "USD" else source_price
        rate = decimal(price.get("changeRate"))
        rank = int(row.get("rank") or 30)
        liquidity_score = max(0, 40 - ((rank - 1) * 1.2))
        if 0.02 <= rate < 0.12:
            momentum_score = 35
        elif 0 <= rate < 0.02:
            momentum_score = 20
        elif rate >= 0.12:
            momentum_score = 10
        else:
            momentum_score = 5
        stability_score = 25 if -0.03 < rate < 0.12 else 8
        score = round(min(100, liquidity_score + momentum_score + stability_score))
        if rate >= 0.12 or rate <= -0.08:
            verdict, reason = "진입 불가", f"급등락 추격 위험 · 평가 {score}점"
        elif score >= 80:
            verdict, reason = "정밀 분석", f"80점 이상 · 거래대금·상승 추세 평가 {score}점"
        elif score >= 60:
            verdict, reason = "관찰", f"방향성 확인 필요 · 평가 {score}점"
        else:
            verdict, reason = "진입 보류", f"전략 기준 미달 · 평가 {score}점"
        results.append(
            {
                "rank": row.get("rank"),
                "symbol": row.get("symbol"),
                "name": names.get(str(row.get("symbol"))) or row.get("symbol"),
                "marketCountry": market,
                "currency": "KRW",
                "sourceCurrency": source_currency,
                "sourcePrice": source_price,
                "fxRate": exchange_rate if source_currency == "USD" else 1.0,
                "lastPrice": normalized_price,
                "dailyRate": rate,
                "tradingAmount": row.get("tradingAmount"),
                "score": score,
                "verdict": verdict,
                "reason": reason,
            }
        )
    return results


def analysis_loop() -> None:
    while True:
        sleep_seconds = 10
        with ANALYSIS_LOCK:
            enabled = bool(ANALYSIS["enabled"])
        if enabled:
            try:
                env = load_env()
                sleep_seconds = analysis_interval_seconds(env)
                market, session = market_schedule(env)
                results = scan_market(env, market) if market else []
                if market:
                    orders, paper_stats = paper_trade(env, results, market, session)
                else:
                    orders = load_paper_orders()
                    paper_stats = paper_summary(orders, results)
                handle_paper_alert(env, market, paper_stats)
                report_state = load_report_state()
                previous_market = report_state.get("lastActiveMarket")
                current_market = market if market in ("KR", "US") else None
                reports, report_status = handle_market_close_report(
                    previous_market, current_market, env, orders, paper_stats
                )
                handle_operation_report(env, current_market, session, results, orders, paper_stats)
                with ANALYSIS_LOCK:
                    ANALYSIS["cycle"] += 1
                    ANALYSIS["lastRunAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    ANALYSIS["lastError"] = None
                    ANALYSIS["results"] = results
                    ANALYSIS["activeMarket"] = market or "CLOSED"
                    ANALYSIS["activeSession"] = session
                    ANALYSIS["paperOrders"] = orders
                    ANALYSIS["paperSummary"] = paper_stats
                    ANALYSIS["reports"] = reports[-5:]
                    ANALYSIS["reportStatus"] = report_status
            except Exception as exc:
                try:
                    handle_problem_alert(load_env(), exc)
                except Exception:
                    pass
                with ANALYSIS_LOCK:
                    ANALYSIS["lastError"] = str(exc)
        time.sleep(sleep_seconds)



def health_status() -> dict[str, Any]:
    env = load_env()
    with ANALYSIS_LOCK:
        analysis = dict(ANALYSIS)
    uptime = max(0, int(time.time() - STARTED_AT))
    release = app_release()
    return {
        "ok": True,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "version": release["version"],
        "release": release,
        "deploy": deploy_status(),
        "uptimeSec": uptime,
        "server": {"running": True, "port": int(os.environ.get("PORT", "4173"))},
        "toss": {
            "configured": bool(env.get("TOSS_CLIENT_ID") and env.get("TOSS_CLIENT_SECRET")),
            "connected": analysis.get("lastError") is None,
        },
        "kakao": {
            "configured": bool(env.get("KAKAO_REST_API_KEY") and env.get("KAKAO_REFRESH_TOKEN")),
            "enabled": kakao_enabled(env),
            "lastError": (analysis.get("reportStatus") or {}).get("lastError"),
        },
        "slack": slack_status(env),
        "analysis": {
            "enabled": bool(analysis.get("enabled")),
            "cycle": analysis.get("cycle", 0),
            "lastRunAt": analysis.get("lastRunAt"),
            "activeMarket": analysis.get("activeMarket"),
            "activeSession": analysis.get("activeSession"),
            "lastError": analysis.get("lastError"),
        },
    }


def git_output(args: list[str]) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=2,
    ).strip()


def app_release() -> dict[str, str]:
    try:
        return {
            "version": git_output(["rev-parse", "--short", "HEAD"]),
            "message": git_output(["log", "-1", "--pretty=%s"]),
            "committedAt": git_output(["log", "-1", "--pretty=%cI"]),
        }
    except Exception:
        version = str(int(Path(__file__).stat().st_mtime))
        return {
            "version": version,
            "message": "로컬 파일 변경사항",
            "committedAt": "",
        }


def deploy_status() -> dict[str, Any]:
    if not DEPLOY_STATE_PATH.exists():
        return {"available": False, "message": "배포 기록 없음"}
    try:
        data = json.loads(DEPLOY_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid deploy status")
        data["available"] = True
        return data
    except Exception:
        return {"available": False, "message": "배포 기록 읽기 실패"}


def load_journal_state() -> dict[str, Any]:
    if not JOURNAL_PATH.exists():
        return {"notes": {}, "reviews": {}}
    try:
        data = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
        return {
            "notes": data.get("notes") or {},
            "reviews": data.get("reviews") or {},
        }
    except (json.JSONDecodeError, OSError):
        return {"notes": {}, "reviews": {}}


def save_journal_state(state: dict[str, Any]) -> None:
    JOURNAL_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def journal_holding_time(opened_at: Any, closed_at: Any) -> str:
    opened = parse_order_time(opened_at)
    closed = parse_order_time(closed_at)
    if not opened or not closed:
        return "측정 중"
    elapsed = max(0, int((closed - opened).total_seconds()))
    if elapsed < 60:
        return f"{elapsed}초"
    minutes, seconds = divmod(elapsed, 60)
    if minutes < 60:
        return f"{minutes}분 {seconds}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분"


def automatic_journal_note(
    trade: dict[str, Any],
    entry_order: dict[str, Any],
    exit_order: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Create an evidence-based draft while preserving any note the user saved."""
    is_closed = trade.get("status") == "CLOSED"
    entry_reason = clean_text(
        entry_order.get("reason") or current.get("reason"),
        "자동 진입 당시 판단 근거가 기록되지 않았습니다.",
        240,
    )
    exit_reason = clean_text(
        exit_order.get("reason"),
        "아직 보유 중이라 청산 판단을 기다리고 있습니다.",
        240,
    )
    score = decimal(entry_order.get("entryScore"))
    allocation_rate = decimal(entry_order.get("allocationRate"))
    return_rate = decimal(trade.get("returnRate"))
    profit = decimal(trade.get("profit"))
    exit_kind = clean_text(exit_order.get("exitKind"), "", 40)
    stop_rate = decimal(strategy_config().get("stopRate") or PAPER_STOP_RATE)
    duration = journal_holding_time(trade.get("openedAt"), trade.get("closedAt"))

    entry_facts = []
    if score:
        entry_facts.append(f"평가 {score:.0f}점")
    if allocation_rate:
        entry_facts.append(f"모의자금 {allocation_rate * 100:.1f}% 배정")
    entry_context = f" ({', '.join(entry_facts)})" if entry_facts else ""
    learning_policy = entry_order.get("learningPolicy") if isinstance(entry_order.get("learningPolicy"), dict) else {}
    learning_line = ""
    if learning_policy:
        learning_line = (
            f"학습 적용: 최소 {int(learning_policy.get('requiredScore') or LEARNING_BASE_ENTRY_SCORE)}점 · "
            f"기본 비중의 {decimal(learning_policy.get('allocationScale') or 1) * 100:.0f}% · "
            f"{clean_text(learning_policy.get('reason'), '종목별 학습 규칙 통과', 140)}"
        )

    if not is_closed:
        review = "관찰 필요"
        evaluation = "포지션 보유 중입니다. 손익과 청산 조건이 확정된 뒤 최종 평가합니다."
        improvement = "손실선과 목표가를 유지하고, 진입 근거가 무너지면 지체 없이 청산합니다."
        result_line = f"현재: {percent(return_rate)} · 평가손익 {money(profit)}"
        tags = ["자동 작성", "보유중"]
    elif exit_kind == "손실선":
        stop_slippage = stop_rate - return_rate
        if stop_slippage <= 0.001:
            review = "손절 준수"
            evaluation = f"설정 손실선 {percent(stop_rate)} 부근에서 청산해 손실 제한 규칙을 지켰습니다."
            improvement = "같은 손실을 줄이려면 진입 직전 급등폭과 거래량 지속성을 더 엄격히 확인합니다."
        else:
            review = "규칙 위반"
            evaluation = (
                f"설정 손실선 {percent(stop_rate)}보다 {abs(stop_slippage) * 100:.2f}%p 불리하게 청산됐습니다."
            )
            improvement = "시세 수집과 청산 주기를 점검해 손실선 도달 즉시 주문 상태가 일치하도록 수정합니다."
        result_line = f"결과: {percent(return_rate)} · 확정손익 {money(profit)} · 보유 {duration}"
        tags = ["자동 작성", "손절"]
    elif return_rate > 0:
        review = "좋은 진입"
        evaluation = "진입 근거가 실제 수익으로 이어졌고 수익 구간에서 청산이 완료됐습니다."
        improvement = "거래량과 추세가 유지된 구간을 다시 확인해 같은 진입 조건의 재현성을 높입니다."
        result_line = f"결과: {percent(return_rate)} · 확정손익 {money(profit)} · 보유 {duration}"
        tags = ["자동 작성", "수익"]
    else:
        review = "성급한 진입"
        evaluation = "진입 뒤 기대한 추세가 이어지지 않아 손실 또는 무수익으로 종료됐습니다."
        improvement = "추격 진입을 피하고 거래대금·돌파 유지·호가 안정성이 함께 확인될 때만 진입합니다."
        result_line = f"결과: {percent(return_rate)} · 확정손익 {money(profit)} · 보유 {duration}"
        tags = ["자동 작성", "개선 필요"]

    memo_lines = [f"진입: {entry_reason}{entry_context}"]
    if learning_line:
        memo_lines.append(learning_line)
    memo_lines.extend(
        [
            f"청산: {exit_reason}",
            result_line,
            f"복기: {evaluation}",
            f"다음 개선: {improvement}",
        ]
    )
    memo = "\n".join(memo_lines)
    return {"memo": memo, "review": review, "tags": tags}


def looks_like_legacy_automatic_note(note: dict[str, Any]) -> bool:
    memo = str(note.get("memo") or "")
    if not all(marker in memo for marker in ("진입:", "청산:", "복기:", "다음 개선:")):
        return False
    automatic_phrases = (
        "포지션 보유 중입니다. 손익과 청산 조건이 확정된 뒤 최종 평가합니다.",
        "설정 손실선",
        "진입 근거가 실제 수익으로 이어졌고",
        "진입 뒤 기대한 추세가 이어지지 않아",
    )
    return any(phrase in memo for phrase in automatic_phrases)


def journal_rule_violation(entry: dict[str, Any], stop_rate: float) -> dict[str, Any] | None:
    """Classify a stop-loss execution miss without treating normal price noise as a breach."""
    if entry.get("status") != "청산" or entry.get("exitKind") != "손실선":
        return None
    return_rate = decimal(entry.get("returnRate"))
    limit_rate = decimal(entry.get("stopRateAtExit") or stop_rate)
    excess_rate = limit_rate - return_rate
    tolerance = 0.001  # 0.10%p까지는 호가/수집 오차로 보고 손절 준수로 처리
    if excess_rate <= tolerance:
        return None

    if excess_rate >= 0.01:
        severity, label = "critical", "심각"
        action = "손절 감지 주기와 가격 급변 구간을 우선 점검하고 같은 종목 재진입을 보류합니다."
    elif excess_rate >= 0.003:
        severity, label = "major", "주의"
        action = "청산 감지 간격을 확인하고 다음 진입 전 호가 변동성을 한 번 더 확인합니다."
    else:
        severity, label = "minor", "경미"
        action = "작은 체결 오차로 기록하되 같은 현상이 반복되는지 다음 거래에서 확인합니다."

    return {
        "id": entry.get("id"),
        "tradingDay": entry.get("tradingDay"),
        "createdAt": entry.get("createdAt"),
        "market": entry.get("market"),
        "symbol": entry.get("symbol"),
        "name": entry.get("name") or entry.get("symbol"),
        "severity": severity,
        "label": label,
        "limitRate": limit_rate,
        "returnRate": return_rate,
        "excessRate": excess_rate,
        "profit": decimal(entry.get("profit")),
        "observation": f"손절 감지 시점에 설정선보다 {excess_rate * 100:.2f}%p 불리한 가격으로 청산됐습니다.",
        "action": action,
    }


def build_daily_mistake_note(
    trading_day: str,
    entries: list[dict[str, Any]],
    stop_rate: float,
) -> dict[str, Any]:
    """Write a compact, evidence-based daily reflection from completed paper trades."""
    day_entries = [item for item in entries if item.get("tradingDay") == trading_day]
    closed = [item for item in day_entries if item.get("status") == "청산"]
    wins = [item for item in closed if decimal(item.get("returnRate")) > 0]
    losses = [item for item in closed if decimal(item.get("returnRate")) <= 0]
    violations = [item.get("ruleViolation") for item in closed if item.get("ruleViolation")]
    violations = sorted(violations, key=lambda item: decimal(item.get("excessRate")), reverse=True)
    invested = sum(decimal(item.get("invested")) for item in day_entries)
    profit = sum(decimal(item.get("profit")) for item in day_entries)
    return_rate = profit / invested if invested else 0.0
    stop_count = sum(1 for item in closed if item.get("exitKind") == "손실선")
    compliant_stops = max(0, stop_count - len(violations))

    if not closed:
        tone = "neutral"
        headline = "아직 확정할 오답이 없습니다"
        reflection = "보유 중인 포지션의 청산 결과가 나오기 전이라 오늘의 판단을 확정하지 않았습니다."
        lesson = "결과가 나오기 전에는 좋은 진입으로 단정하지 않고, 진입 근거와 손실선을 그대로 유지합니다."
        next_rule = "청산이 끝난 뒤 진입 근거·실행 오차·결과를 함께 평가합니다."
    elif violations:
        worst = violations[0]
        tone = "danger" if worst.get("severity") == "critical" else "warning"
        headline = f"손절 실행 오차 {len(violations)}건, 오늘의 최우선 오답"
        reflection = (
            f"오늘 {len(closed)}번 청산해 {len(wins)}번 수익을 냈지만, "
            f"{worst.get('name')}에서 손실선 {percent(stop_rate)}보다 "
            f"{decimal(worst.get('excessRate')) * 100:.2f}%p 더 밀렸습니다. "
            "수익 여부보다 손실 제한을 계획한 가격에 실행하는 정확도가 먼저입니다."
        )
        lesson = "진입 점수가 높아도 손절 규칙에는 예외가 없고, 급변 구간에서는 감지 간격 자체가 리스크가 됩니다."
        next_rule = "손절선 초과가 0.10%p를 넘은 종목은 즉시 오답으로 분류하고 재진입 전에 가격 갱신 상태를 확인합니다."
    elif profit < 0:
        tone = "warning"
        headline = "손절은 지켰지만 진입 품질이 아쉬웠다"
        reflection = (
            f"오늘 {len(closed)}번 청산 중 {len(losses)}번이 손실이었고, 손익은 {money(profit)}입니다. "
            f"손절 {compliant_stops}건은 계획 범위에서 끝냈지만 진입 뒤 추세 지속성이 부족했습니다."
        )
        lesson = "손절 준수는 방어에 성공한 것이지 진입 판단까지 옳았다는 뜻은 아닙니다."
        next_rule = "다음 거래에서는 점수뿐 아니라 직전 급등폭과 거래량 지속성을 함께 확인합니다."
    else:
        tone = "positive"
        headline = "오늘은 규칙과 수익이 함께 맞았다"
        reflection = (
            f"오늘 {len(closed)}번 청산해 {len(wins)}번 수익, {money(profit)}으로 마감했습니다. "
            f"손절 {compliant_stops}건도 계획 범위 안에서 처리돼 실행 규칙이 유지됐습니다."
        )
        lesson = "좋았던 결과보다 어떤 진입 조건과 청산 실행이 반복 가능했는지를 남기는 것이 중요합니다."
        next_rule = "수익 거래의 거래량·추세 조건을 다음 거래와 비교해 재현 가능한 패턴만 남깁니다."

    return {
        "tradingDay": trading_day,
        "author": "Orbit 자동 복기",
        "tone": tone,
        "headline": headline,
        "reflection": reflection,
        "lesson": lesson,
        "nextRule": next_rule,
        "stats": {
            "closedCount": len(closed),
            "winCount": len(wins),
            "lossCount": len(losses),
            "violationCount": len(violations),
            "compliantStopCount": compliant_stops,
            "profit": profit,
            "returnRate": return_rate,
        },
        "symbols": sorted({str(item.get("symbol") or "") for item in closed if item.get("symbol")}),
        "violations": violations,
    }


def default_learning_state() -> dict[str, Any]:
    return {
        "schemaVersion": LEARNING_SCHEMA_VERSION,
        "processedTrades": [],
        "symbols": {},
        "memories": [],
        "updatedAt": None,
    }


def load_learning_state_unlocked() -> dict[str, Any]:
    if not LEARNING_PATH.exists():
        return default_learning_state()
    try:
        raw = json.loads(LEARNING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_learning_state()
    if not isinstance(raw, dict):
        return default_learning_state()
    return {
        "schemaVersion": LEARNING_SCHEMA_VERSION,
        "processedTrades": list(raw.get("processedTrades") or []),
        "symbols": dict(raw.get("symbols") or {}),
        "memories": list(raw.get("memories") or []),
        "updatedAt": raw.get("updatedAt"),
    }


def save_learning_state_unlocked(state: dict[str, Any]) -> None:
    temporary = LEARNING_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(LEARNING_PATH)


def learning_severity_rank(value: Any) -> int:
    return {"minor": 1, "major": 2, "critical": 3}.get(str(value or ""), 0)


def learning_cooldown_minutes(severity: str) -> int:
    return {"minor": 10, "major": 30, "critical": 60}.get(severity, 0)


def learning_time_remaining(value: Any, now: datetime | None = None) -> int:
    moment = parse_order_time(value)
    if not moment:
        return 0
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    return max(0, int((moment.astimezone(KST) - now.astimezone(KST)).total_seconds()))


def refresh_symbol_learning(profile: dict[str, Any]) -> None:
    trades = max(0, int(profile.get("tradeCount") or 0))
    wins = max(0, int(profile.get("winCount") or 0))
    violations = profile.get("violationCounts") or {}
    critical = int(violations.get("critical") or 0)
    major = int(violations.get("major") or 0)
    minor = int(violations.get("minor") or 0)
    consecutive_losses = max(0, int(profile.get("consecutiveLosses") or 0))
    win_rate = wins / trades if trades else 0.0
    average_return = decimal(profile.get("returnSum")) / trades if trades else 0.0
    average_score = decimal(profile.get("scoreSum")) / trades if trades else 0.0
    average_holding = decimal(profile.get("holdingSecondsTotal")) / trades if trades else 0.0
    score_penalty = 0
    allocation_scale = 1.0

    if critical:
        score_penalty = max(score_penalty, 8)
        allocation_scale = min(allocation_scale, 0.50)
    elif major:
        score_penalty = max(score_penalty, 5)
        allocation_scale = min(allocation_scale, 0.65)
    elif minor:
        score_penalty = max(score_penalty, 2)
        allocation_scale = min(allocation_scale, 0.80)
    if consecutive_losses >= 2:
        score_penalty += 4
        allocation_scale = min(allocation_scale, 0.50)
    if trades >= 3 and win_rate < 0.40:
        score_penalty += 4
        allocation_scale = min(allocation_scale, 0.65)
    if trades >= 3 and average_return < -0.005:
        score_penalty += 3
        allocation_scale = min(allocation_scale, 0.60)

    required_score = min(99, LEARNING_BASE_ENTRY_SCORE + score_penalty)
    cooldown_remaining = learning_time_remaining(profile.get("cooldownUntil"))
    cooldown_active = cooldown_remaining > 0
    if cooldown_active:
        status = "재진입 대기"
    elif required_score > LEARNING_BASE_ENTRY_SCORE or allocation_scale < 1:
        status = "강화 적용"
    elif trades < 3:
        status = "표본 수집"
    else:
        status = "검증 유지"

    if critical:
        risk_level = "critical"
    elif major or consecutive_losses >= 2 or (trades >= 3 and win_rate < 0.40):
        risk_level = "caution"
    elif minor:
        risk_level = "watch"
    else:
        risk_level = "stable"

    traits: list[str] = []
    if average_holding and average_holding < 180:
        traits.append("초단기 반응")
    if average_score >= 95:
        traits.append("고득점 진입")
    if critical or major or minor:
        traits.append(f"손절오차 {critical + major + minor}회")
    if trades >= 2 and win_rate >= 0.60:
        traits.append("수익 재현")
    elif trades >= 2 and win_rate < 0.40:
        traits.append("진입 선별 필요")
    if not traits:
        traits.append("표본 수집")

    if cooldown_active:
        primary_rule = f"재진입 {max(1, (cooldown_remaining + 59) // 60)}분 대기"
    elif required_score > LEARNING_BASE_ENTRY_SCORE or allocation_scale < 1:
        primary_rule = f"{required_score}점 이상 · 기본 비중의 {allocation_scale * 100:.0f}%"
    else:
        primary_rule = f"{LEARNING_BASE_ENTRY_SCORE}점 기준 유지 · 추가 표본 수집"

    profile.update(
        {
            "winRate": win_rate,
            "averageReturn": average_return,
            "averageScore": average_score,
            "averageHoldingSec": average_holding,
            "requiredScore": required_score,
            "allocationScale": allocation_scale,
            "status": status,
            "riskLevel": risk_level,
            "traits": traits[:4],
            "primaryRule": primary_rule,
        }
    )


def learning_observation(
    name: str,
    return_rate: float,
    violation: dict[str, Any] | None,
    profile: dict[str, Any],
) -> str:
    if violation:
        return (
            f"{name}은 손절선보다 {decimal(violation.get('excessRate')) * 100:.2f}%p 불리하게 청산돼 "
            "진입 점수와 비중을 즉시 강화했습니다."
        )
    if return_rate > 0:
        return f"{name}의 수익 조건을 기억하되 기준을 자동 완화하지 않고 같은 조건의 재현성을 확인합니다."
    if int(profile.get("consecutiveLosses") or 0) >= 2:
        return f"{name}에서 연속 손실이 확인돼 재진입 대기와 비중 축소를 즉시 적용했습니다."
    return f"{name}의 손실은 계획 범위에서 끝났지만 다음 진입의 추세 지속성을 더 엄격히 확인합니다."


def sync_learning_brain(
    orders: list[dict[str, Any]],
    results_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Absorb every newly closed PAPER trade and persist symbol-specific risk memory."""
    results_by_symbol = results_by_symbol or {}
    ledger = paper_trade_ledger(orders, results_by_symbol)
    orders_by_id = {str(item.get("id") or ""): item for item in orders if item.get("id")}
    stop_rate = decimal(strategy_config().get("stopRate") or PAPER_STOP_RATE)
    with LEARNING_LOCK:
        state = load_learning_state_unlocked()
        processed = set(str(item) for item in state.get("processedTrades") or [])
        changed = False
        closed_trades = sorted(
            (item for item in ledger if item.get("status") == "CLOSED"),
            key=lambda item: str(item.get("closedAt") or ""),
        )
        for trade in closed_trades:
            entry_id = str(trade.get("entryOrderId") or "")
            exit_id = str(trade.get("exitOrderId") or "")
            trade_key = f"{entry_id}:{exit_id}"
            if not entry_id or trade_key in processed:
                continue
            entry_order = orders_by_id.get(entry_id) or {}
            exit_order = orders_by_id.get(exit_id) or {}
            symbol = str(trade.get("symbol") or entry_order.get("symbol") or "")
            if not symbol:
                continue
            name = str(entry_order.get("name") or exit_order.get("name") or symbol)
            market = str(trade.get("market") or entry_order.get("market") or "")
            return_rate = decimal(trade.get("returnRate"))
            profit = decimal(trade.get("profit"))
            invested = decimal(trade.get("invested"))
            entry_score = decimal(entry_order.get("entryScore"))
            opened = parse_order_time(trade.get("openedAt"))
            closed = parse_order_time(trade.get("closedAt"))
            holding_seconds = max(0, int((closed - opened).total_seconds())) if opened and closed else 0
            limit_rate = decimal(exit_order.get("stopRate") or stop_rate)
            violation = journal_rule_violation(
                {
                    "id": entry_id,
                    "tradingDay": paper_trading_day(trade.get("closedAt")),
                    "createdAt": trade.get("closedAt"),
                    "market": market,
                    "symbol": symbol,
                    "name": name,
                    "status": "청산",
                    "exitKind": exit_order.get("exitKind"),
                    "returnRate": return_rate,
                    "profit": profit,
                    "stopRateAtExit": limit_rate,
                },
                stop_rate,
            )
            profile = state.setdefault("symbols", {}).setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": name,
                    "market": market,
                    "tradeCount": 0,
                    "winCount": 0,
                    "lossCount": 0,
                    "consecutiveLosses": 0,
                    "totalProfit": 0.0,
                    "totalInvested": 0.0,
                    "returnSum": 0.0,
                    "scoreSum": 0.0,
                    "holdingSecondsTotal": 0,
                    "violationCounts": {"minor": 0, "major": 0, "critical": 0},
                    "worstViolationSeverity": None,
                    "cooldownUntil": None,
                    "recentResults": [],
                },
            )
            profile["name"] = name
            profile["market"] = market
            profile["tradeCount"] = int(profile.get("tradeCount") or 0) + 1
            profile["totalProfit"] = decimal(profile.get("totalProfit")) + profit
            profile["totalInvested"] = decimal(profile.get("totalInvested")) + invested
            profile["returnSum"] = decimal(profile.get("returnSum")) + return_rate
            profile["scoreSum"] = decimal(profile.get("scoreSum")) + entry_score
            profile["holdingSecondsTotal"] = int(profile.get("holdingSecondsTotal") or 0) + holding_seconds
            profile["lastTradeAt"] = str(trade.get("closedAt") or "")
            if return_rate > 0:
                profile["winCount"] = int(profile.get("winCount") or 0) + 1
                profile["consecutiveLosses"] = 0
            else:
                profile["lossCount"] = int(profile.get("lossCount") or 0) + 1
                profile["consecutiveLosses"] = int(profile.get("consecutiveLosses") or 0) + 1

            recent = list(profile.get("recentResults") or [])
            recent.append({"closedAt": trade.get("closedAt"), "returnRate": return_rate, "profit": profit})
            profile["recentResults"] = recent[-12:]
            cooldown_minutes = 0
            if violation:
                severity = str(violation.get("severity") or "minor")
                counts = profile.setdefault("violationCounts", {"minor": 0, "major": 0, "critical": 0})
                counts[severity] = int(counts.get(severity) or 0) + 1
                if learning_severity_rank(severity) >= learning_severity_rank(profile.get("worstViolationSeverity")):
                    profile["worstViolationSeverity"] = severity
                profile["lastViolationAt"] = str(trade.get("closedAt") or "")
                cooldown_minutes = learning_cooldown_minutes(severity)
            if int(profile.get("consecutiveLosses") or 0) >= 2:
                cooldown_minutes = max(cooldown_minutes, 20)
            if cooldown_minutes and closed:
                cooldown_until = closed.astimezone(KST) + timedelta(minutes=cooldown_minutes)
                profile["cooldownUntil"] = cooldown_until.strftime("%Y-%m-%dT%H:%M:%S%z")

            refresh_symbol_learning(profile)
            memory = {
                "id": hashlib.sha1(trade_key.encode("utf-8")).hexdigest()[:12],
                "tradeKey": trade_key,
                "createdAt": str(trade.get("closedAt") or ""),
                "tradingDay": paper_trading_day(trade.get("closedAt")),
                "market": market,
                "symbol": symbol,
                "name": name,
                "result": "규칙 오답" if violation else ("수익 재현" if return_rate > 0 else "손실 학습"),
                "returnRate": return_rate,
                "profit": profit,
                "observation": learning_observation(name, return_rate, violation, profile),
                "appliedRule": profile.get("primaryRule"),
                "requiredScore": profile.get("requiredScore"),
                "allocationScale": profile.get("allocationScale"),
                "appliedImmediately": True,
            }
            state.setdefault("memories", []).append(memory)
            state.setdefault("processedTrades", []).append(trade_key)
            processed.add(trade_key)
            changed = True

        for profile in state.get("symbols", {}).values():
            before = json.dumps(profile, ensure_ascii=False, sort_keys=True)
            refresh_symbol_learning(profile)
            if before != json.dumps(profile, ensure_ascii=False, sort_keys=True):
                changed = True
        if changed:
            state["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            save_learning_state_unlocked(state)
        return json.loads(json.dumps(state, ensure_ascii=False))


def learning_entry_policy(symbol: str, score: Any, state: dict[str, Any]) -> dict[str, Any]:
    profile = (state.get("symbols") or {}).get(symbol) or {}
    required_score = int(profile.get("requiredScore") or LEARNING_BASE_ENTRY_SCORE)
    allocation_scale = clamp(profile.get("allocationScale"), 0.40, 1.0, 1.0)
    cooldown_remaining = learning_time_remaining(profile.get("cooldownUntil"))
    candidate_score = decimal(score)
    allowed = cooldown_remaining <= 0 and candidate_score >= required_score
    if cooldown_remaining > 0:
        reason = f"종목 학습 규칙 · 재진입 {max(1, (cooldown_remaining + 59) // 60)}분 대기"
    elif candidate_score < required_score:
        reason = f"종목 학습 규칙 · {required_score}점 필요 (현재 {candidate_score:.0f}점)"
    elif profile:
        reason = f"종목 학습 통과 · {required_score}점 기준 · 비중 {allocation_scale * 100:.0f}% 적용"
    else:
        reason = f"신규 종목 · 기본 {LEARNING_BASE_ENTRY_SCORE}점 기준"
    return {
        "symbol": symbol,
        "allowed": allowed,
        "reason": reason,
        "requiredScore": required_score,
        "candidateScore": candidate_score,
        "allocationScale": allocation_scale,
        "cooldownRemainingSec": cooldown_remaining,
        "status": profile.get("status") or "신규 학습",
        "traits": profile.get("traits") or ["표본 수집"],
        "appliedImmediately": True,
    }


def learning_brain_payload(state: dict[str, Any]) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    active_rules = 0
    cooldown_count = 0
    for raw in (state.get("symbols") or {}).values():
        profile = dict(raw)
        remaining = learning_time_remaining(profile.get("cooldownUntil"))
        profile["cooldownActive"] = remaining > 0
        profile["cooldownRemainingSec"] = remaining
        if remaining > 0:
            cooldown_count += 1
        if (
            remaining > 0
            or int(profile.get("requiredScore") or LEARNING_BASE_ENTRY_SCORE) > LEARNING_BASE_ENTRY_SCORE
            or decimal(profile.get("allocationScale") or 1) < 1
        ):
            active_rules += 1
        symbols.append(profile)
    risk_order = {"critical": 3, "caution": 2, "watch": 1, "stable": 0}
    symbols.sort(
        key=lambda item: (risk_order.get(str(item.get("riskLevel")), 0), int(item.get("tradeCount") or 0)),
        reverse=True,
    )
    memories = list(reversed(state.get("memories") or []))
    return {
        "updatedAt": state.get("updatedAt"),
        "summary": {
            "learnedTradeCount": len(state.get("processedTrades") or []),
            "symbolCount": len(symbols),
            "memoryCount": len(memories),
            "activeRuleCount": active_rules,
            "cooldownCount": cooldown_count,
            "mode": "PAPER_ONLY",
            "immediateApply": True,
            "coverage": "ALL_NEW_ENTRIES",
        },
        "symbols": symbols,
        "memories": memories[:40],
    }


def apply_brain_to_mistake_note(note: dict[str, Any], brain: dict[str, Any]) -> None:
    profiles = {str(item.get("symbol") or ""): item for item in brain.get("symbols") or []}
    applied_rules = []
    for symbol in note.get("symbols") or []:
        profile = profiles.get(str(symbol))
        if not profile:
            continue
        if (
            int(profile.get("requiredScore") or LEARNING_BASE_ENTRY_SCORE) <= LEARNING_BASE_ENTRY_SCORE
            and decimal(profile.get("allocationScale") or 1) >= 1
            and not profile.get("cooldownActive")
        ):
            continue
        applied_rules.append(
            {
                "symbol": symbol,
                "name": profile.get("name") or symbol,
                "rule": profile.get("primaryRule"),
                "requiredScore": profile.get("requiredScore"),
                "allocationScale": profile.get("allocationScale"),
                "cooldownActive": profile.get("cooldownActive"),
            }
        )
    note["appliedRules"] = applied_rules
    note["appliedImmediately"] = bool(applied_rules)
    if applied_rules:
        note["nextRule"] = " / ".join(f"{item['name']}: {item['rule']}" for item in applied_rules[:3])


def build_trading_journal() -> dict[str, Any]:
    orders = load_paper_orders()
    with ANALYSIS_LOCK:
        analysis = dict(ANALYSIS)
    results = analysis.get("results") or []
    results_by_symbol = {str(item.get("symbol")): item for item in results}
    learning_state = sync_learning_brain(orders, results_by_symbol)
    learning_brain = learning_brain_payload(learning_state)
    state = load_journal_state()
    notes = state.get("notes") or {}
    trade_ledger = paper_trade_ledger(orders, results_by_symbol)
    orders_by_id = {str(item.get("id") or ""): item for item in orders if item.get("id")}
    entries: list[dict[str, Any]] = []
    total_invested = sum(decimal(item.get("invested")) for item in trade_ledger)
    total_profit = sum(decimal(item.get("profit")) for item in trade_ledger)
    closed_trades = [item for item in trade_ledger if item.get("status") == "CLOSED"]
    wins = sum(1 for item in closed_trades if decimal(item.get("returnRate")) > 0)
    closed_count = len(closed_trades)
    open_count = len(open_paper_positions(orders))
    stop_rate = decimal(strategy_config().get("stopRate") or PAPER_STOP_RATE)

    for trade in sorted(
        trade_ledger,
        key=lambda item: str(item.get("closedAt") or item.get("openedAt") or ""),
        reverse=True,
    ):
        order_id = str(trade.get("entryOrderId") or "")
        exit_order_id = str(trade.get("exitOrderId") or "")
        entry_order = orders_by_id.get(order_id) or {}
        exit_order = orders_by_id.get(exit_order_id) or {}
        symbol = str(trade.get("symbol") or entry_order.get("symbol") or "")
        is_closed = trade.get("status") == "CLOSED"
        side = "SELL" if is_closed else "BUY"
        current = results_by_symbol.get(symbol) or {}
        quantity = decimal(trade.get("quantity") or entry_order.get("quantity") or 1)
        entry_price = decimal(trade.get("entryPrice") or entry_order.get("price"))
        last_price = decimal(trade.get("lastPrice") or entry_price)
        invested = decimal(trade.get("invested"))
        profit = decimal(trade.get("profit"))
        return_rate = decimal(trade.get("returnRate"))
        verdict = current.get("verdict") or ("청산" if is_closed else "보유/관찰")
        reason = (
            exit_order.get("reason")
            or entry_order.get("reason")
            or current.get("reason")
            or "진입 사유 기록 없음"
        )
        note = notes.get(order_id) or notes.get(exit_order_id) or {}
        automatic_note = automatic_journal_note(trade, entry_order, exit_order, current)
        user_memo = clean_text(note.get("memo"), "", 1200)
        user_review = clean_text(note.get("review"), "", 400)
        user_tags = note.get("tags") if isinstance(note.get("tags"), list) else []
        auto_saved = (
            bool(note.get("autoGenerated"))
            if "autoGenerated" in note
            else looks_like_legacy_automatic_note(note)
        )
        use_automatic = auto_saved or not (user_memo or user_review or user_tags)
        note_source = "auto" if use_automatic else "user"
        entry = {
                "id": order_id,
                "createdAt": str(trade.get("closedAt") or trade.get("openedAt") or ""),
                "tradingDay": paper_trading_day(trade.get("closedAt") or trade.get("openedAt")),
                "market": trade.get("market"),
                "symbol": symbol,
                "name": entry_order.get("name") or exit_order.get("name") or symbol,
                "side": side,
                "sideLabel": "매수→매도" if is_closed else "매수",
                "status": "청산" if is_closed else "보유중",
                "quantity": quantity,
                "entryPrice": entry_price,
                "lastPrice": last_price,
                "currency": entry_order.get("currency") or exit_order.get("currency"),
                "invested": invested,
                "profit": profit,
                "returnRate": return_rate,
                "verdict": verdict,
                "reason": reason,
                "exitKind": exit_order.get("exitKind"),
                "stopRateAtExit": decimal(exit_order.get("stopRate") or stop_rate),
                "entryOrderId": order_id,
                "exitOrderId": exit_order_id,
                "holdingTime": journal_holding_time(trade.get("openedAt"), trade.get("closedAt")),
                "entryScore": decimal(entry_order.get("entryScore")),
                "allocationRate": decimal(entry_order.get("allocationRate")),
                "learningPolicy": entry_order.get("learningPolicy") if isinstance(entry_order.get("learningPolicy"), dict) else None,
                "memo": automatic_note["memo"] if use_automatic else user_memo or automatic_note["memo"],
                "review": automatic_note["review"] if use_automatic else user_review or automatic_note["review"],
                "tags": automatic_note["tags"] if use_automatic else user_tags or automatic_note["tags"],
                "noteSource": note_source,
                "updatedAt": note.get("updatedAt"),
            }
        entry["ruleViolation"] = journal_rule_violation(entry, stop_rate)
        entries.append(entry)

    daily: dict[str, dict[str, Any]] = {}
    for entry in entries:
        day = str(entry.get("tradingDay") or "")
        bucket = daily.setdefault(
            day,
            {
                "tradingDay": day,
                "count": 0,
                "openCount": 0,
                "closedCount": 0,
                "wins": 0,
                "totalInvested": 0.0,
                "totalProfit": 0.0,
            },
        )
        bucket["count"] += 1
        bucket["totalInvested"] += decimal(entry.get("invested"))
        bucket["totalProfit"] += decimal(entry.get("profit"))
        if entry.get("status") == "청산":
            bucket["closedCount"] += 1
            if decimal(entry.get("returnRate")) > 0:
                bucket["wins"] += 1
        else:
            bucket["openCount"] += 1

    days = []
    for bucket in daily.values():
        invested = decimal(bucket.get("totalInvested"))
        closed = int(bucket.get("closedCount") or 0)
        bucket["returnRate"] = decimal(bucket.get("totalProfit")) / invested if invested else 0.0
        bucket["winRate"] = int(bucket.get("wins") or 0) / closed if closed else 0.0
        bucket.pop("wins", None)
        days.append(bucket)
    days.sort(key=lambda item: str(item.get("tradingDay") or ""), reverse=True)
    active_trading_day = paper_trading_day()
    active_day = next(
        (item for item in days if item.get("tradingDay") == active_trading_day),
        {
            "tradingDay": active_trading_day,
            "count": 0,
            "openCount": 0,
            "closedCount": 0,
            "totalInvested": 0.0,
            "totalProfit": 0.0,
            "returnRate": 0.0,
            "winRate": 0.0,
        },
    )

    note_days = sorted(
        {str(item.get("tradingDay") or "") for item in entries if item.get("tradingDay")} | {active_trading_day},
        reverse=True,
    )
    mistake_notes = [build_daily_mistake_note(day, entries, stop_rate) for day in note_days]
    for note in mistake_notes:
        apply_brain_to_mistake_note(note, learning_brain)
    active_mistake_note = next(
        (item for item in mistake_notes if item.get("tradingDay") == active_trading_day),
        build_daily_mistake_note(active_trading_day, entries, stop_rate),
    )
    all_violations = [item.get("ruleViolation") for item in entries if item.get("ruleViolation")]

    count = len(trade_ledger)
    period_returns = period_profit_summary(trade_ledger)
    summary = {
        "count": count,
        "openCount": open_count,
        "closedCount": closed_count,
        "winRate": wins / closed_count if closed_count else 0.0,
        "totalInvested": total_invested,
        "totalProfit": total_profit,
        "averageReturn": total_profit / total_invested if total_invested else 0.0,
        "periodReturns": period_returns,
        "best": max(entries, key=lambda item: decimal(item.get("returnRate")), default=None),
        "worst": min(entries, key=lambda item: decimal(item.get("returnRate")), default=None),
        "activeTradingDay": active_trading_day,
        "activeDay": active_day,
        "days": days,
        "violationCount": len(all_violations),
    }
    return {
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": summary,
        "entries": entries,
        "coaching": {
            "active": active_mistake_note,
            "days": mistake_notes,
            "violations": all_violations,
        },
        "learning": learning_brain,
    }

def save_journal_note(payload: dict[str, Any]) -> dict[str, Any]:
    order_id = clean_text(payload.get("id"), "", 80)
    if not order_id:
        raise TossApiError(400, "journal-id-missing", "저장할 매매 기록 ID가 없습니다.")
    memo = clean_text(payload.get("memo"), "", 1200)
    review = clean_text(payload.get("review"), "", 400)
    tags_raw = payload.get("tags") or []
    if isinstance(tags_raw, str):
        tags = [clean_text(item, "", 24) for item in tags_raw.split(",")]
    else:
        tags = [clean_text(item, "", 24) for item in tags_raw]
    tags = [item for item in tags if item][:6]
    auto_generated = payload.get("autoGenerated") is True
    state = load_journal_state()
    notes = state.setdefault("notes", {})
    notes[order_id] = {
        "memo": memo,
        "review": review,
        "tags": tags,
        "autoGenerated": auto_generated,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    save_journal_state(state)
    return build_trading_journal()
def analysis_snapshot() -> dict[str, Any]:
    with ANALYSIS_LOCK:
        return dict(ANALYSIS)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Avoid logging query strings or accidental sensitive values.
        print(f"[dashboard] {self.command} {self.path.split('?', 1)[0]}")

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TossApiError(400, "invalid-json", "요청 형식이 올바르지 않습니다.") from exc
        if not isinstance(data, dict):
            raise TossApiError(400, "invalid-json", "설정 값은 객체 형태여야 합니다.")
        return data
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/dashboard":
            try:
                self.send_json(build_dashboard())
            except TossApiError as exc:
                self.send_json(
                    {"connected": False, "error": exc.message, "code": exc.code},
                    status=exc.status if 400 <= exc.status < 600 else 502,
                )
            except Exception:
                self.send_json(
                    {"connected": False, "error": "대시보드 데이터를 불러오지 못했습니다."},
                    status=500,
                )
            return
        if path == "/api/analysis/status":
            self.send_json(analysis_snapshot())
            return
        if path == "/api/health":
            self.send_json(health_status())
            return
        if path == "/api/strategy/config":
            self.send_json(strategy_payload())
            return
        if path == "/api/trading-journal":
            self.send_json(build_trading_journal())
            return
        if path == "/api/kakao/auth-url":
            try:
                self.send_json({"url": kakao_auth_url(load_env())})
            except TossApiError as exc:
                self.send_json({"error": exc.message, "code": exc.code}, status=exc.status)
            return
        if path == "/kakao/callback":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = (query.get("code") or [""])[0]
            if not code:
                content = kakao_callback_page("카카오 연결 실패", "카카오 인증 코드가 전달되지 않았습니다.", ok=False)
            else:
                try:
                    token = exchange_kakao_code(load_env(), code)
                    refresh_token = token.get("refresh_token")
                    if refresh_token:
                        update_env_values({"KAKAO_REFRESH_TOKEN": str(refresh_token), "KAKAO_REPORT_ENABLED": "true"})
                        content = kakao_callback_page("카카오 연결 완료", "refresh token을 .env에 저장했습니다. 이제 장마감 리포트가 카카오톡 나에게 보내기로 발송됩니다.")
                    else:
                        content = kakao_callback_page("카카오 연결 확인 필요", "카카오가 refresh token을 새로 내려주지 않았습니다. 동의 화면에서 다시 연결하거나 기존 token을 확인해주세요.", ok=False)
                except TossApiError as exc:
                    content = kakao_callback_page("카카오 연결 실패", exc.message, ok=False)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        file_path = (ROOT / relative).resolve()
        if ROOT not in file_path.parents and file_path != ROOT:
            self.send_error(403)
            return
        if not file_path.is_file() or file_path.name == ".env":
            self.send_error(404)
            return
        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/slack/test":
            try:
                payload = self.read_json_body()
                self.send_json(test_slack_channel(str(payload.get("channel", ""))))
            except TossApiError as exc:
                self.send_json({"error": exc.message, "code": exc.code}, status=exc.status)
            return
        if path == "/api/trading-journal/note":
            try:
                self.send_json(save_journal_note(self.read_json_body()))
            except TossApiError as exc:
                self.send_json({"error": exc.message, "code": exc.code}, status=exc.status)
            return
        if path == "/api/strategy/config":
            try:
                config = save_strategy_config(self.read_json_body())
                orders = load_paper_orders()
                with ANALYSIS_LOCK:
                    results = list(ANALYSIS.get("results") or [])
                    ANALYSIS["paperSummary"] = paper_summary(orders, results)
                payload = strategy_payload()
                payload["paperSummary"] = analysis_snapshot().get("paperSummary")
                self.send_json(payload)
            except TossApiError as exc:
                self.send_json({"error": exc.message, "code": exc.code}, status=exc.status)
            return
        if path not in ("/api/analysis/start", "/api/analysis/stop"):
            self.send_json({"error": "지원하지 않는 요청입니다."}, status=404)
            return
        with ANALYSIS_LOCK:
            ANALYSIS["enabled"] = path.endswith("/start")
        self.send_json(analysis_snapshot())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4173"))
    host = os.environ.get("HOST", "0.0.0.0")
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    threading.Thread(target=analysis_loop, daemon=True, name="analysis-loop").start()
    print(f"Orbit dashboard: http://{display_host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
