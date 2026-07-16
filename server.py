"""Local dashboard server and Toss Securities Open API gateway.

Secrets stay on the server in .env. The browser only receives normalized
portfolio data and never receives the OAuth access token or account number.
"""

from __future__ import annotations

import json
import hashlib
import math
import mimetypes
import os
import re
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
PAPER_LOCK = threading.RLock()
STRATEGY_LOCK = threading.RLock()
TOKEN: dict[str, Any] = {"value": None, "expires_at": 0.0}
STARTED_AT = time.time()
KST = ZoneInfo("Asia/Seoul")
PAPER_SCHEMA_VERSION = 2
PAPER_STARTING_CAPITAL_KRW = 1_000_000
PAPER_TARGET_RATE = 0.01
PAPER_STOP_RATE = -0.0045
PAPER_STOP_MONITOR_INTERVAL_SECONDS = 1.0
PAPER_STOP_REENTRY_COOLDOWN_SECONDS = 60
POST_EXIT_OBSERVATION_HORIZONS = (("5m", 300), ("10m", 600), ("30m", 1800))
POST_EXIT_OBSERVATION_TOLERANCE_SECONDS = 120
POST_EXIT_OBSERVATION_RETRY_SECONDS = 10
POST_EXIT_MEANINGFUL_MOVE_RATE = 0.001
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
LEARNING_SCHEMA_VERSION = 2
LEARNING_BASE_ENTRY_SCORE = 80
GLOBAL_SCORE_FEATURES = {
    "liquidity": {"label": "거래대금 순위", "maxPoints": 40.0},
    "momentum": {"label": "당일 추세", "maxPoints": 35.0},
    "stability": {"label": "급등락 안정성", "maxPoints": 25.0},
}
GLOBAL_SCORE_WEIGHT_MIN = 0.70
GLOBAL_SCORE_WEIGHT_MAX = 1.30
GLOBAL_SCORE_MAX_TRADE_STEP = 0.04
GLOBAL_SCORE_LEARNING_RATE = 0.06
OFF_MARKET_STUDY_UNIVERSE_PER_HORIZON = 10
OFF_MARKET_STUDY_CANDLE_PAGES = 3
OFF_MARKET_STUDY_POLL_SECONDS = 300
DEFAULT_STRATEGY_CONFIG = {
    "targetRate": PAPER_TARGET_RATE,
    "stopRate": PAPER_STOP_RATE,
    "maxDailyOrders": PAPER_MAX_DAILY_ORDERS,
    "maxOpenPositions": PAPER_MAX_OPEN_POSITIONS,
    "maxConsecutiveLosses": PAPER_MAX_CONSECUTIVE_LOSSES,
    "revision": 0,
    "savedAt": None,
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
        "title": "전역 학습형 100점 평가",
        "description": "모든 PAPER 청산 결과로 거래대금 순위, 당일 추세, 급등락 안정성의 가중치와 진입 기준을 함께 재평가하고 다음 모든 종목에 즉시 적용합니다.",
        "judge": "전체 거래가 다음 점수 기준을 수정",
        "enabled": True,
    },
    {
        "id": "adaptive-capital-utilization",
        "title": "무제한 가상자금·점수 비중 자동배분",
        "description": "100만 원은 성과 비교 기준금으로만 사용하고, 전역 학습 점수를 통과한 종목마다 기준금의 30~60%를 배정합니다.",
        "judge": "자금 한도 없음 · 학습 비중 우선",
        "enabled": True,
    },
    {
        "id": "paper-learning-sprint",
        "title": "PAPER 무제한 경험 축적",
        "description": "일일 횟수·가상자금·동시 포지션·일 손익·연속 손실에 따른 신규 진입 잠금을 해제하고, 전역 점수 기준을 통과한 모든 후보의 성공과 실패를 공용 오답 표본으로 쌓습니다.",
        "judge": "진입 제한 없음 · 개별 손절 유지",
        "enabled": True,
    },
    {
        "id": "unlimited-paper-experience",
        "title": "무제한 가상자금 경험 랩",
        "description": "100만 원은 성과 비교 기준으로만 유지합니다. 가상자금과 동시 포지션 수에는 상한을 두지 않고, 전역 학습 점수를 통과한 후보는 최소 1주 이상 경험 데이터로 기록합니다.",
        "judge": "자금·포지션 무제한 · 점수 필수",
        "enabled": True,
    },
    {
        "id": "hard-stop-loss",
        "title": "−0.45% 예약 보호매도",
        "description": "매수 체결과 동시에 평균 체결가 대비 −0.45%에 PAPER 보호매도를 예약하고, 별도 포지션 감시기가 발동가 도달 시 전량 청산합니다.",
        "judge": "매수 즉시 보호주문 등록",
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
        "title": "표본 균형 회전·재진입 대기",
        "description": "모든 청산 후 동일 종목은 10분간 재진입을 대기하고, 점수 기준을 통과한 후보 중 오늘 표본이 적은 종목을 우선해 학습 편중을 줄입니다.",
        "judge": "적은 표본 우선 · 동일 종목 10분 대기",
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
    "strategyRevision": 0,
    "strategyAppliedAt": None,
    "paperOrders": [],
    "paperSummary": {
        "targetRate": PAPER_TARGET_RATE,
        "stopRate": PAPER_STOP_RATE,
        "averageReturn": 0,
        "locked": False,
        "lockReason": None,
    },
    "riskMonitor": {
        "enabled": True,
        "intervalSec": PAPER_STOP_MONITOR_INTERVAL_SECONDS,
        "activeMarkets": [],
        "lastRunAt": None,
        "lastActionAt": None,
        "lastError": None,
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


def strategy_copy_text(strategy: dict[str, Any]) -> str:
    return " ".join(
        str(strategy.get(key) or "")
        for key in ("title", "description", "judge")
    ).replace("−", "-").replace("~", "-")


def strategy_percent_values(strategy: dict[str, Any]) -> list[float]:
    return [
        decimal(match) / 100
        for match in re.findall(r"([+-]?\d+(?:\.\d+)?)\s*%", strategy_copy_text(strategy))
    ]


def strategy_keyword_percent_values(
    strategy: dict[str, Any], keywords: tuple[str, ...]
) -> list[float]:
    text = strategy_copy_text(strategy)
    keyword_pattern = "|".join(re.escape(keyword) for keyword in keywords)
    value_pattern = r"(?<![\d.+-])([+-]?\d+(?:\.\d+)?)\s*%"
    matches = re.findall(
        rf"{value_pattern}[^.!?\n]{{0,18}}(?:{keyword_pattern})",
        text,
    )
    matches.extend(
        re.findall(
            rf"(?:{keyword_pattern})[^.!?\n]{{0,18}}{value_pattern}",
            text,
        )
    )
    return [decimal(value) / 100 for value in matches]


def strategy_runtime_parameters(config: dict[str, Any]) -> dict[str, Any]:
    """Translate editable strategy copy into bounded, executable parameters."""
    strategies = {
        str(item.get("id")): item
        for item in config.get("strategies") or []
        if isinstance(item, dict)
    }
    stop_rate = decimal(config.get("stopRate") or PAPER_STOP_RATE)
    target_rate = decimal(config.get("targetRate") or PAPER_TARGET_RATE)
    hard_stop = strategies.get("hard-stop-loss") or {}
    profit = strategies.get("profit-trailing") or {}
    time_exit = strategies.get("three-minute-exit") or {}
    score_entry = strategies.get("score-entry-80") or {}
    allocation = strategies.get("adaptive-capital-utilization") or {}
    daily_risk = strategies.get("daily-risk-kill-switch") or {}
    reentry = strategies.get("reentry-cooldown") or {}

    negative_stops = [
        value
        for value in strategy_keyword_percent_values(
            hard_stop, ("예약", "보호", "손절", "손실선")
        )
        if -0.05 <= value < 0
    ]
    positive_targets = [
        value
        for value in strategy_keyword_percent_values(
            profit, ("목표", "도달", "익절", "부터")
        )
        if 0 < value <= 0.05
    ]
    if negative_stops:
        stop_rate = clamp(negative_stops[-1], -0.05, -0.001, stop_rate)
    if positive_targets:
        target_rate = clamp(positive_targets[-1], 0.001, 0.05, target_rate)

    score_matches = [
        int(value)
        for value in re.findall(
            r"(\d{2,3})\s*점\s*(?:이상|기준|필요|통과)",
            strategy_copy_text(score_entry),
        )
    ]
    minute_matches = [int(value) for value in re.findall(r"(\d+)\s*분", strategy_copy_text(time_exit))]
    reentry_minutes = [int(value) for value in re.findall(r"(\d+)\s*분", strategy_copy_text(reentry))]
    loss_count_matches = [
        int(number)
        for groups in re.findall(r"(\d+)\s*회\s*연속\s*손절|연속\s*손절\s*(\d+)\s*회", strategy_copy_text(reentry))
        for number in groups
        if number
    ]
    allocation_rates = [value for value in strategy_percent_values(allocation) if value > 0]
    daily_rates = sorted(
        (
            value
            for value in strategy_keyword_percent_values(
                daily_risk, ("손실", "중단", "도달", "신규 진입")
            )
            if -0.05 <= value < 0
        ),
        reverse=True,
    )

    return {
        "targetRate": target_rate,
        "stopRate": stop_rate,
        "entryScoreFloor": int(clamp(score_matches[-1] if score_matches else LEARNING_BASE_ENTRY_SCORE, 50, 99, LEARNING_BASE_ENTRY_SCORE)),
        "timeExitSeconds": int(clamp((minute_matches[-1] if minute_matches else 3) * 60, 60, 3600, 180)),
        "timeExitMinimumReturn": 0.001,
        "reentryCooldownSeconds": int(clamp((reentry_minutes[-1] if reentry_minutes else 10) * 60, 60, 86400, 600)),
        "maxConsecutiveLosses": int(clamp(loss_count_matches[-1] if loss_count_matches else config.get("maxConsecutiveLosses"), 1, 10, PAPER_MAX_CONSECUTIVE_LOSSES)),
        "minAllocationRate": clamp(min(allocation_rates) if allocation_rates else PAPER_MIN_EXPERIENCE_ENTRY_RATE, 0.05, 1.0, PAPER_MIN_EXPERIENCE_ENTRY_RATE),
        "maxAllocationRate": clamp(max(allocation_rates) if allocation_rates else PAPER_MAX_SINGLE_POSITION_RATE, 0.05, 1.0, PAPER_MAX_SINGLE_POSITION_RATE),
        "dailyEntryLockRate": clamp(daily_rates[0] if daily_rates else -0.008, -0.05, -0.001, -0.008),
        "dailyLiquidationRate": clamp(daily_rates[-1] if len(daily_rates) > 1 else -0.01, -0.05, -0.001, -0.01),
    }


def strategy_execution_policy(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or strategy_config()
    enabled_ids = [
        str(item.get("id"))
        for item in config.get("strategies") or []
        if item.get("enabled") and item.get("id")
    ]
    enabled = set(enabled_ids)
    sprint = "paper-learning-sprint" in enabled
    unlimited = "unlimited-paper-experience" in enabled
    return {
        "revision": int(config.get("revision") or 0),
        "savedAt": config.get("savedAt"),
        "effectiveFrom": "NEXT_ENTRY",
        "enabledIds": enabled_ids,
        "liquidityFilter": "liquidity-momentum-filter" in enabled,
        "scoreFilter": "score-entry-80" in enabled,
        "adaptiveAllocation": "adaptive-capital-utilization" in enabled,
        "learningSprint": sprint,
        "unlimitedFunding": sprint and unlimited,
        "unlimitedPositions": sprint and unlimited,
        "hardStop": "hard-stop-loss" in enabled,
        "profitTarget": "profit-trailing" in enabled,
        "timeExit": "three-minute-exit" in enabled,
        "dailyRisk": "daily-risk-kill-switch" in enabled,
        "reentryCooldown": "reentry-cooldown" in enabled,
        "extendedSession": "overnight-extended-session" in enabled,
        "parameters": strategy_runtime_parameters(config),
    }


def strategy_config() -> dict[str, Any]:
    with STRATEGY_LOCK:
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
        normalized = {
            "targetRate": clamp(config.get("targetRate"), 0.001, 0.05, PAPER_TARGET_RATE),
            "stopRate": clamp(config.get("stopRate"), -0.05, -0.001, PAPER_STOP_RATE),
            "maxDailyOrders": int(clamp(config.get("maxDailyOrders"), 1, 20, PAPER_MAX_DAILY_ORDERS)),
            "maxOpenPositions": int(clamp(config.get("maxOpenPositions"), 1, 20, PAPER_MAX_OPEN_POSITIONS)),
            "maxConsecutiveLosses": int(clamp(config.get("maxConsecutiveLosses"), 1, 10, PAPER_MAX_CONSECUTIVE_LOSSES)),
            "revision": max(0, int(config.get("revision") or 0)),
            "savedAt": config.get("savedAt"),
            "strategies": normalize_strategies(stored_strategies),
        }
        normalized["paperLearningSprint"] = strategy_execution_policy(normalized)["learningSprint"]
        return normalized


def save_strategy_config(payload: dict[str, Any]) -> dict[str, Any]:
    with STRATEGY_LOCK:
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
        runtime = strategy_runtime_parameters(current)
        current["targetRate"] = runtime["targetRate"]
        current["stopRate"] = runtime["stopRate"]
        current["maxConsecutiveLosses"] = runtime["maxConsecutiveLosses"]
        current["revision"] = int(current.get("revision") or 0) + 1
        current["savedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        temporary = STRATEGY_CONFIG_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(STRATEGY_CONFIG_PATH)
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
    execution_policy = strategy_execution_policy(config)
    analysis = analysis_snapshot()
    strategies = []
    for item in config.get("strategies") or []:
        row = dict(item)
        row["aiAdvice"] = strategy_ai_advice(row, analysis)
        strategies.append(row)
    return {
        "config": config,
        "strategies": strategies,
        "executionPolicy": execution_policy,
        "overallAdvice": overall_ai_analysis(analysis, strategies),
    }
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
        good.append(f"{percent(stop_rate)} 예약 보호매도: 손실 청산 {len(stop_exits)}건 실행")
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
    trade_stats = summary.get("todayTradeStats") or {}
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
            (
                f"현재 승률: {decimal(trade_stats.get('winRate')) * 100:.1f}% "
                f"({int(trade_stats.get('winCount') or 0)}승 · "
                f"{int(trade_stats.get('lossCount') or 0)}패 · "
                f"{int(trade_stats.get('flatCount') or 0)}보합 / "
                f"{int(trade_stats.get('closedCount') or 0)}건)"
            ),
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


def active_market_sessions(
    env: dict[str, str], now: datetime | None = None
) -> list[tuple[str, str]]:
    if time.time() >= CALENDAR_CACHE["expiresAt"]:
        CALENDAR_CACHE["KR"] = toss_get("/api/v1/market-calendar/KR", env).get("result") or {}
        CALENDAR_CACHE["US"] = toss_get("/api/v1/market-calendar/US", env).get("result") or {}
        CALENDAR_CACHE["expiresAt"] = time.time() + 300

    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=KST)

    def is_open(session: dict[str, Any]) -> bool:
        try:
            start = datetime.fromisoformat(str(session["startTime"]))
            end = datetime.fromisoformat(str(session["endTime"]))
            return start <= moment < end
        except (KeyError, TypeError, ValueError):
            return False

    active: list[tuple[str, str]] = []
    kr_today = (CALENDAR_CACHE["KR"].get("today") or {}).get("integrated") or {}
    if is_open(kr_today.get("regularMarket") or {}):
        active.append(("KR", "KR 정규장"))

    us_today = CALENDAR_CACHE["US"].get("today") or {}
    us_sessions = (
        ("dayMarket", "US 데이마켓"),
        ("preMarket", "US 프리마켓"),
        ("regularMarket", "US 정규장"),
        ("afterMarket", "US 애프터마켓"),
    )
    for key, label in us_sessions:
        if is_open(us_today.get(key) or {}):
            active.append(("US", label))
            break
    return active


def market_schedule(env: dict[str, str]) -> tuple[Any, str]:
    active = active_market_sessions(env)
    if active:
        return active[0]
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
    temporary = PAPER_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(PAPER_PATH)


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


def paper_capital_summary(
    trades: list[dict[str, Any]], execution_policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    policy = execution_policy or strategy_execution_policy()
    unlimited_funding = bool(policy.get("unlimitedFunding"))
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
    remaining_deployable = max(0.0, working_capital - open_invested)
    return {
        "startingCapitalKrw": starting,
        "referenceCapitalKrw": reference_capital,
        "workingCapitalKrw": working_capital,
        "cashKrw": cash,
        "openInvestedKrw": open_invested,
        "targetInvestedKrw": None if unlimited_funding else working_capital,
        "remainingDeployableKrw": None if unlimited_funding else remaining_deployable,
        "cashReserveKrw": 0.0,
        "virtualFundingKrw": virtual_funding,
        "fundingLimit": "UNLIMITED" if unlimited_funding else working_capital,
        "referenceOnly": unlimited_funding,
        "realizedProfitKrw": realized,
        "unrealizedProfitKrw": unrealized,
        "equityKrw": equity,
        "returnRate": (equity - starting) / starting if starting else 0.0,
        "utilizationRate": utilization_rate,
        "targetUtilizationRate": None if unlimited_funding else 1.0,
        "reserveRate": 0.0,
        "utilizationStatus": (
            "무제한 경험 축적"
            if unlimited_funding
            else ("운용 자금 배정 완료" if remaining_deployable <= 0 else "추가 진입 가능")
        ),
        "currency": "KRW",
        "allocationMode": "unlimited-paper-experience" if unlimited_funding else "bounded-paper-capital",
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
    execution_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Size each PAPER experience independently while preserving learned risk cuts."""
    policy = execution_policy or strategy_execution_policy()
    parameters = policy.get("parameters") or {}
    working_capital = max(1.0, decimal(capital.get("referenceCapitalKrw")) or PAPER_STARTING_CAPITAL_KRW)
    invested = max(0.0, decimal(capital.get("openInvestedKrw")))
    min_rate = decimal(parameters.get("minAllocationRate") or PAPER_MIN_EXPERIENCE_ENTRY_RATE)
    max_rate = decimal(parameters.get("maxAllocationRate") or PAPER_MAX_SINGLE_POSITION_RATE)
    confidence_rate = max(
        min_rate,
        min(max_rate, confidence_allocation_rate(score)),
    )
    if not policy.get("adaptiveAllocation"):
        confidence_rate = min_rate
    confidence_budget = working_capital * confidence_rate
    base_budget = confidence_budget
    applied_learning_scale = clamp(learning_scale, 0.40, 1.0, 1.0)
    planned_budget = base_budget * applied_learning_scale
    if not policy.get("unlimitedFunding"):
        planned_budget = min(planned_budget, max(0.0, working_capital - invested))
    return {
        "mode": "unlimited-paper-experience" if policy.get("unlimitedFunding") else "bounded-paper-capital",
        "workingCapitalKrw": working_capital,
        "referenceCapitalKrw": working_capital,
        "targetInvestedKrw": None,
        "investedBeforeKrw": invested,
        "availableCashKrw": None if policy.get("unlimitedFunding") else max(0.0, working_capital - invested),
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
        "fundingLimit": "UNLIMITED" if policy.get("unlimitedFunding") else working_capital,
    }



def trading_decision(
    average_return: float,
    open_positions: int,
    today_orders: int,
    locked: bool,
    lock_reason: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = strategy_execution_policy(config)
    runtime = policy.get("parameters") or {}
    target_rate = decimal(runtime.get("targetRate") or config.get("targetRate"))
    stop_rate = decimal(runtime.get("dailyEntryLockRate") or config.get("stopRate"))
    max_open_positions = int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS)
    remaining_to_stop = average_return - stop_rate
    remaining_to_target = target_rate - average_return
    stop_progress = 0.0
    if stop_rate < 0:
        stop_progress = max(0.0, min(1.0, abs(min(average_return, 0.0)) / abs(stop_rate)))

    if (
        policy.get("learningSprint")
        and not policy.get("unlimitedPositions")
        and open_positions >= max_open_positions
    ):
        mode = "학습 대기"
        tone = "caution"
        action = "청산 자리 발생 시 점수순 재진입"
        reason = "동시 포지션 3개를 모두 사용 중입니다."
    elif policy.get("learningSprint"):
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
    policy = strategy_execution_policy(config)
    runtime = policy.get("parameters") or {}
    stop_rate = decimal(runtime.get("dailyEntryLockRate") or config.get("stopRate"))
    max_daily_orders = int(config.get("maxDailyOrders") or PAPER_MAX_DAILY_ORDERS)
    max_open_positions = int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS)
    max_losses = int(config.get("maxConsecutiveLosses") or PAPER_MAX_CONSECUTIVE_LOSSES)
    consecutive_losses = 0
    for value in reversed(position_returns):
        if value < 0:
            consecutive_losses += 1
        else:
            break
    learning_sprint = bool(policy.get("learningSprint"))
    unlimited_positions = bool(policy.get("unlimitedPositions"))
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
            "status": "무제한" if unlimited_positions else ("과밀" if open_positions >= max_open_positions else "정상"),
            "tone": "safe" if unlimited_positions else ("danger" if open_positions >= max_open_positions else "safe"),
            "detail": (
                f"현재 {open_positions}개 · 우수 후보 추가 진입 가능"
                if unlimited_positions
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
    policy = summary.get("executionPolicy") or strategy_execution_policy()
    if policy.get("learningSprint") and policy.get("unlimitedPositions"):
        blocking_keys: set[str] = set()
    elif policy.get("learningSprint"):
        blocking_keys = {"positionCap"}
    else:
        blocking_keys = {"lock", "dailyLoss", "positionCap", "lossStreak"}
    blockers = [rule for rule in rules if rule.get("tone") == "danger" and rule.get("key") in blocking_keys]
    return {
        "blocked": bool(blockers),
        "reason": str(blockers[0].get("detail") or blockers[0].get("label")) if blockers else "신규 진입 가능",
        "blockers": blockers,
    }


def trade_outcome_stats(
    trades: list[dict[str, Any]], trading_day: str | None = None
) -> dict[str, Any]:
    day = trading_day or paper_trading_day()
    closed = [
        trade
        for trade in trades
        if trade.get("status") == "CLOSED"
        and paper_trading_day(trade.get("closedAt") or trade.get("openedAt")) == day
    ]
    win_count = sum(1 for trade in closed if decimal(trade.get("returnRate")) > 0)
    loss_count = sum(1 for trade in closed if decimal(trade.get("returnRate")) < 0)
    flat_count = len(closed) - win_count - loss_count
    return {
        "tradingDay": day,
        "closedCount": len(closed),
        "winCount": win_count,
        "lossCount": loss_count,
        "flatCount": flat_count,
        "winRate": win_count / len(closed) if closed else 0.0,
    }


def paper_summary(orders: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    config = strategy_config()
    execution_policy = strategy_execution_policy(config)
    runtime = execution_policy.get("parameters") or {}
    target_rate = decimal(runtime.get("targetRate") or config.get("targetRate"))
    stop_rate = decimal(runtime.get("stopRate") or config.get("stopRate"))
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

    working_protective_stops = sum(
        1
        for order in positions.values()
        if isinstance(order.get("protectiveStopOrder"), dict)
        and (order.get("protectiveStopOrder") or {}).get("status") == "WORKING"
    )

    results_by_symbol = {str(item.get("symbol")): item for item in results}
    trade_ledger = paper_trade_ledger(orders, results_by_symbol)
    today_trade_stats = trade_outcome_stats(trade_ledger, today)
    capital = paper_capital_summary(trade_ledger, execution_policy)
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
    daily_lock_rate = decimal(runtime.get("dailyEntryLockRate") or stop_rate)
    stop_hit = average_return <= daily_lock_rate
    locked = bool(execution_policy.get("dailyRisk")) and (target_hit or stop_hit) and not execution_policy.get("learningSprint")
    lock_reason = None
    if target_hit and locked:
        lock_reason = f"일 목표 {percent(target_rate)} 도달 · 신규 진입 잠금"
    elif stop_hit and locked:
        lock_reason = f"통합 손실 예산 {percent(daily_lock_rate)} 도달 · 신규 진입 중지"

    return {
        "targetRate": target_rate,
        "stopRate": stop_rate,
        "strategyConfig": config,
        "executionPolicy": execution_policy,
        "capital": capital,
        "learningCoverage": "GLOBAL_ALL_SYMBOLS",
        "capitalAllocationPolicy": {
            "mode": "unlimited-paper-experience" if execution_policy.get("unlimitedFunding") else "bounded-paper-capital",
            "referenceCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
            "fundingLimit": "UNLIMITED" if execution_policy.get("unlimitedFunding") else PAPER_STARTING_CAPITAL_KRW,
            "maxSinglePositionRate": runtime.get("maxAllocationRate") or PAPER_MAX_SINGLE_POSITION_RATE,
            "minExperienceEntryRate": runtime.get("minAllocationRate") or PAPER_MIN_EXPERIENCE_ENTRY_RATE,
            "learningAppliedAfterSizing": True,
        },
        "paperLearningSprint": {
            "enabled": bool(execution_policy.get("learningSprint")),
            "entryLimit": "UNLIMITED" if execution_policy.get("learningSprint") else int(config.get("maxDailyOrders") or PAPER_MAX_DAILY_ORDERS),
            "dailyProfitLock": not execution_policy.get("learningSprint"),
            "lossStreakLock": not execution_policy.get("learningSprint"),
            "scoreFilter": bool(execution_policy.get("scoreFilter")),
            "symbolLearning": False,
            "globalLearning": True,
            "individualStops": bool(execution_policy.get("hardStop")),
            "maxOpenPositions": "UNLIMITED" if execution_policy.get("unlimitedPositions") else int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS),
            "fundingLimit": "UNLIMITED" if execution_policy.get("unlimitedFunding") else PAPER_STARTING_CAPITAL_KRW,
        },
        "averageReturn": average_return,
        "periodReturns": period_profit_summary(trade_ledger),
        "todayTradeStats": today_trade_stats,
        "technicalReview": tech_review,
        "timeExitFollowUp": post_exit_study_summary(orders),
        "safetyRules": safety_rules(average_return, len(positions), len(today_orders), position_returns, locked, lock_reason, config),
        "todayOrderCount": len(today_orders),
        "openPositionCount": len(positions),
        "protectiveStops": {
            "workingCount": working_protective_stops,
            "positionCount": len(positions),
            "coverageRate": working_protective_stops / len(positions) if positions else 1.0,
            "stopRate": stop_rate,
            "monitorIntervalSec": PAPER_STOP_MONITOR_INTERVAL_SECONDS,
        },
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


def build_protective_stop_order(
    entry_order: dict[str, Any], stop_rate: float
) -> dict[str, Any]:
    """Create the PAPER resting protection attached to a filled buy."""
    entry_price = decimal(entry_order.get("price"))
    normalized_rate = clamp(stop_rate, -0.05, -0.001, PAPER_STOP_RATE)
    trigger_price = entry_price * (1 + normalized_rate) if entry_price else 0.0
    entry_id = str(entry_order.get("id") or f"PAPER-{int(time.time())}")
    created_at = str(entry_order.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return {
        "id": f"PAPER-STOP-{entry_id}",
        "entryOrderId": entry_id,
        "side": "SELL",
        "orderType": "PAPER_PROTECTIVE_STOP",
        "status": "WORKING",
        "stopRate": normalized_rate,
        "triggerPrice": trigger_price,
        "quantity": decimal(entry_order.get("quantity") or 1),
        "createdAt": created_at,
        "fillPolicy": "TRIGGER_PRICE",
    }


def ensure_protective_stop_order(
    entry_order: dict[str, Any], stop_rate: float
) -> tuple[dict[str, Any], bool]:
    existing = entry_order.get("protectiveStopOrder")
    if isinstance(existing, dict) and existing.get("status") == "WORKING":
        normalized_rate = clamp(stop_rate, -0.05, -0.001, PAPER_STOP_RATE)
        entry_price = decimal(entry_order.get("price"))
        if abs(decimal(existing.get("stopRate")) - normalized_rate) > 0.0000001:
            existing.update(
                {
                    "stopRate": normalized_rate,
                    "triggerPrice": entry_price * (1 + normalized_rate) if entry_price else 0.0,
                    "replacedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
            return existing, True
        return existing, False
    if isinstance(existing, dict) and existing.get("status") in ("FILLED", "CANCELLED"):
        return existing, False
    protective = build_protective_stop_order(entry_order, stop_rate)
    entry_order["protectiveStopOrder"] = protective
    return protective, True


def stop_reentry_cooldown_symbols(
    orders: list[dict[str, Any]], market: str, now: datetime | None = None,
    cooldown_seconds: int = PAPER_STOP_REENTRY_COOLDOWN_SECONDS,
) -> set[str]:
    return recent_exit_cooldown_symbols(
        orders,
        market,
        now=now,
        cooldown_seconds=cooldown_seconds,
        exit_kinds={"손실선"},
    )


def recent_exit_cooldown_symbols(
    orders: list[dict[str, Any]], market: str, now: datetime | None = None,
    cooldown_seconds: int = PAPER_STOP_REENTRY_COOLDOWN_SECONDS,
    exit_kinds: set[str] | None = None,
) -> set[str]:
    """Return symbols that recently exited, optionally limited to exit kinds."""
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    blocked: set[str] = set()
    for order in reversed(orders):
        if str(order.get("side") or "").upper() != "SELL":
            continue
        if str(order.get("status") or "FILLED").upper() != "FILLED":
            continue
        if order.get("market") != market:
            continue
        if exit_kinds is not None and str(order.get("exitKind") or "") not in exit_kinds:
            continue
        moment = parse_order_time(order.get("createdAt"))
        if not moment:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=KST)
        age = (current.astimezone(KST) - moment.astimezone(KST)).total_seconds()
        if 0 <= age < max(0, cooldown_seconds):
            blocked.add(str(order.get("symbol") or ""))
    blocked.discard("")
    return blocked


def market_entry_sample_counts(
    orders: list[dict[str, Any]], market: str, trading_day: str | None = None
) -> dict[str, int]:
    day = trading_day or paper_trading_day()
    counts: dict[str, int] = {}
    for order in orders:
        if str(order.get("side") or "").upper() != "BUY":
            continue
        if order.get("market") != market or paper_trading_day(order.get("createdAt")) != day:
            continue
        symbol = str(order.get("symbol") or "")
        if symbol:
            counts[symbol] = counts.get(symbol, 0) + 1
    return counts


def rank_candidates_for_sample_diversity(
    candidates: list[dict[str, Any]], sample_counts: dict[str, int]
) -> list[dict[str, Any]]:
    """Prefer under-sampled qualifying symbols, then preserve score/rank quality."""
    return sorted(
        candidates,
        key=lambda item: (
            int(sample_counts.get(str(item.get("symbol") or ""), 0)),
            -decimal(item.get("score")),
            decimal(item.get("rank") or 999),
        ),
    )


def sample_diversity_summary(
    orders: list[dict[str, Any]], market: str, cooldown_seconds: int
) -> dict[str, Any]:
    counts = market_entry_sample_counts(orders, market)
    return {
        "market": market,
        "todayEntryCount": sum(counts.values()),
        "uniqueSymbolCount": len(counts),
        "maxSymbolEntryCount": max(counts.values(), default=0),
        "cooldownSeconds": cooldown_seconds,
        "selectionPriority": "LEAST_SAMPLED_THEN_SCORE",
        "symbolCounts": counts,
    }


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


def build_post_exit_study(
    exit_price: float,
    entry_price: float,
    closed_at: Any,
) -> dict[str, Any]:
    closed = parse_order_time(closed_at) or datetime.now().astimezone()
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=KST)
    horizons = {
        key: {
            "key": key,
            "seconds": seconds,
            "dueAt": (closed + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": "PENDING",
        }
        for key, seconds in POST_EXIT_OBSERVATION_HORIZONS
    }
    return {
        "status": "TRACKING",
        "startedAt": closed.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "exitPrice": decimal(exit_price),
        "entryPrice": decimal(entry_price),
        "observedCount": 0,
        "pendingCount": len(horizons),
        "horizons": horizons,
        "verdict": "추적 중",
        "recommendation": "5·10·30분 가격을 기다리는 중입니다.",
    }


def finalize_post_exit_study(study: dict[str, Any], completed_at: datetime) -> None:
    horizons = study.get("horizons") if isinstance(study.get("horizons"), dict) else {}
    observed = [
        item
        for item in horizons.values()
        if isinstance(item, dict) and item.get("status") == "OBSERVED"
    ]
    valid = [item for item in observed if item.get("quality") == "ON_TIME"]
    study["observedCount"] = len(observed)
    study["pendingCount"] = sum(
        1 for item in horizons.values() if isinstance(item, dict) and item.get("status") == "PENDING"
    )
    if study["pendingCount"]:
        study["status"] = "TRACKING"
        study["verdict"] = "추적 중"
        return
    study["status"] = "COMPLETE"
    study["completedAt"] = completed_at.strftime("%Y-%m-%dT%H:%M:%S%z")
    if not valid:
        study["verdict"] = "관측 부족"
        study["recommendation"] = "장 종료 등으로 정시 관측이 없어 시간청산 판단 학습에서 제외합니다."
        return
    latest = max(valid, key=lambda item: int(item.get("seconds") or 0))
    study["verdict"] = str(latest.get("outcome") or "적정 청산")
    study["latestValidHorizon"] = latest.get("key")
    study["latestFromExitRate"] = decimal(latest.get("fromExitRate"))
    if study["verdict"] == "너무 이른 청산":
        study["recommendation"] = "시간청산을 조금 늦췄을 때의 재현성을 추가 검증합니다."
    elif study["verdict"] == "손실 회피":
        study["recommendation"] = "현재 시간청산이 추가 하락을 피한 사례로 학습합니다."
    else:
        study["recommendation"] = "현재 시간청산 기준을 유지하고 표본을 더 축적합니다."


def update_post_exit_studies_if_due(
    env: dict[str, str],
    orders: list[dict[str, Any]],
    market: str,
    results: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    due_orders: dict[str, dict[str, Any]] = {}
    due_items: list[tuple[dict[str, Any], dict[str, Any], datetime]] = []
    for order in orders:
        if order.get("market") != market or order.get("exitKind") != "시간청산":
            continue
        study = order.get("postExitStudy") if isinstance(order.get("postExitStudy"), dict) else {}
        if study.get("status") not in ("TRACKING", "PENDING"):
            continue
        horizons = study.get("horizons") if isinstance(study.get("horizons"), dict) else {}
        for horizon in horizons.values():
            if not isinstance(horizon, dict) or horizon.get("status") != "PENDING":
                continue
            due_at = parse_order_time(horizon.get("dueAt"))
            if not due_at:
                continue
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=KST)
            if due_at <= current:
                last_attempt = parse_order_time(horizon.get("lastAttemptAt"))
                if last_attempt and last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=KST)
                if last_attempt and (current - last_attempt).total_seconds() < POST_EXIT_OBSERVATION_RETRY_SECONDS:
                    continue
                symbol = str(order.get("symbol") or "")
                if symbol:
                    horizon["lastAttemptAt"] = current.strftime("%Y-%m-%dT%H:%M:%S%z")
                    due_orders[symbol] = order
                    due_items.append((order, horizon, due_at))
    if not due_items:
        return orders, False

    prices = refresh_position_prices(env, due_orders, results or [])
    changed = True  # Persist attempt timestamps even when the quote is temporarily unavailable.
    touched_studies: dict[int, dict[str, Any]] = {}
    observed_at = current.strftime("%Y-%m-%dT%H:%M:%S%z")
    for order, horizon, due_at in due_items:
        symbol = str(order.get("symbol") or "")
        price = decimal(prices.get(symbol))
        study = order.get("postExitStudy") or {}
        exit_price = decimal(study.get("exitPrice") or order.get("price"))
        entry_price = decimal(study.get("entryPrice") or order.get("entryPrice"))
        if not price or not exit_price or not entry_price:
            continue
        delayed_seconds = max(0, int((current - due_at).total_seconds()))
        quality = (
            "ON_TIME"
            if delayed_seconds <= POST_EXIT_OBSERVATION_TOLERANCE_SECONDS
            else "LATE"
        )
        from_exit_rate = (price - exit_price) / exit_price
        hypothetical_return_rate = (price - entry_price) / entry_price
        if quality != "ON_TIME":
            outcome = "관측 지연"
        elif from_exit_rate >= POST_EXIT_MEANINGFUL_MOVE_RATE:
            outcome = "너무 이른 청산"
        elif from_exit_rate <= -POST_EXIT_MEANINGFUL_MOVE_RATE:
            outcome = "손실 회피"
        else:
            outcome = "적정 청산"
        horizon.update(
            {
                "status": "OBSERVED",
                "observedAt": observed_at,
                "observedPrice": price,
                "fromExitRate": from_exit_rate,
                "hypotheticalReturnRate": hypothetical_return_rate,
                "delayedSeconds": delayed_seconds,
                "quality": quality,
                "outcome": outcome,
            }
        )
        touched_studies[id(study)] = study
        changed = True
    for study in touched_studies.values():
        finalize_post_exit_study(study, current)
    if changed:
        save_paper_orders(orders)
    return orders, changed


def post_exit_study_summary(orders: list[dict[str, Any]]) -> dict[str, Any]:
    studies = [
        order.get("postExitStudy")
        for order in orders
        if order.get("exitKind") == "시간청산"
        and isinstance(order.get("postExitStudy"), dict)
    ]
    verdicts: dict[str, int] = {}
    for study in studies:
        verdict = str(study.get("verdict") or "추적 중")
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
    return {
        "trackingCount": sum(1 for study in studies if study.get("status") == "TRACKING"),
        "completedCount": sum(1 for study in studies if study.get("status") == "COMPLETE"),
        "totalCount": len(studies),
        "horizons": [key for key, _ in POST_EXIT_OBSERVATION_HORIZONS],
        "verdicts": verdicts,
    }


def close_paper_positions_if_needed(
    env: dict[str, str],
    orders: list[dict[str, Any]],
    results: list[dict[str, Any]],
    market: str,
    session: str,
    stop_only: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    config = strategy_config()
    current_policy = strategy_execution_policy(config)
    current_parameters = current_policy.get("parameters") or {}
    positions = open_paper_positions(orders, market)
    if not positions:
        return orders, False
    prices = refresh_position_prices(env, positions, results)
    changed = False
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for symbol, order in list(positions.items()):
        entry = decimal(order.get("price"))
        if not entry:
            continue
        entry_policy = order.get("strategyExecution") if isinstance(order.get("strategyExecution"), dict) else {}
        fallback_ids = current_policy.get("enabledIds") or [
            str(item.get("id")) for item in DEFAULT_STRATEGIES if item.get("enabled")
        ]
        if entry_policy and isinstance(order.get("strategyIds"), list):
            entry_ids = set(order.get("strategyIds") or [])
        elif order.get("strategyRevision") is not None and isinstance(order.get("strategyIds"), list):
            entry_ids = set(order.get("strategyIds") or [])
        else:
            entry_ids = set(entry_policy.get("enabledIds") or fallback_ids)
        entry_parameters = entry_policy.get("parameters") if isinstance(entry_policy.get("parameters"), dict) else current_parameters
        target_rate = decimal(entry_parameters.get("targetRate") or config.get("targetRate"))
        stop_rate = decimal(entry_parameters.get("stopRate") or config.get("stopRate"))
        hard_stop_enabled = "hard-stop-loss" in entry_ids or isinstance(order.get("protectiveStopOrder"), dict)
        profit_target_enabled = "profit-trailing" in entry_ids
        time_exit_enabled = "three-minute-exit" in entry_ids
        protective: dict[str, Any] = {}
        created = False
        if hard_stop_enabled:
            protective, created = ensure_protective_stop_order(order, stop_rate)
            changed = changed or created
        trigger_price = decimal(protective.get("triggerPrice"))
        protective_rate = decimal(protective.get("stopRate") or stop_rate)
        last = prices.get(symbol, 0.0)
        if not last:
            continue
        observed_rate = (last - entry) / entry
        fill_price = last
        rate = observed_rate
        exit_kind = None
        reason = None
        if hard_stop_enabled and protective.get("status") == "WORKING" and trigger_price and last <= trigger_price:
            exit_kind = "손실선"
            fill_price = trigger_price
            rate = (fill_price - entry) / entry
            reason = (
                f"예약 보호매도 {percent(protective_rate)} 체결"
                f" · 감시 관측가 {percent(observed_rate)}"
            )
            protective.update(
                {
                    "status": "FILLED",
                    "filledAt": now,
                    "fillPrice": fill_price,
                    "observedPrice": last,
                    "observedReturnRate": observed_rate,
                    "slippageFromTriggerRate": observed_rate - protective_rate,
                }
            )
        elif not stop_only and profit_target_enabled and observed_rate >= target_rate:
            exit_kind = "목표"
            reason = f"목표 {percent(target_rate)} 도달 · 즉시 모의청산"
            if protective.get("status") == "WORKING":
                protective.update(
                    {
                        "status": "CANCELLED",
                        "cancelledAt": now,
                        "cancelReason": "목표 청산 완료",
                    }
                )
        elif not stop_only and time_exit_enabled:
            opened_at = parse_order_time(order.get("createdAt"))
            hold_seconds = max(0, int((datetime.now().astimezone() - opened_at).total_seconds())) if opened_at else 0
            time_limit = int(entry_parameters.get("timeExitSeconds") or 180)
            minimum_return = decimal(entry_parameters.get("timeExitMinimumReturn") or 0.001)
            if hold_seconds >= time_limit and observed_rate < minimum_return:
                exit_kind = "시간청산"
                reason = f"{max(1, time_limit // 60)}분 내 의미 있는 상승 미달 · 즉시 모의청산"
                if protective.get("status") == "WORKING":
                    protective.update(
                        {
                            "status": "CANCELLED",
                            "cancelledAt": now,
                            "cancelReason": "시간청산 완료",
                        }
                    )
        if not exit_kind:
            continue
        exit_order = {
                "id": f"PAPER-EXIT-{int(time.time())}-{symbol}",
                "market": order.get("market"),
                "session": session,
                "symbol": symbol,
                "name": order.get("name"),
                "side": "SELL",
                "quantity": decimal(order.get("quantity") or 1),
                "price": fill_price,
                "observedPrice": last,
                "entryPrice": entry,
                "entryOrderId": order.get("id"),
                "protectiveStopOrderId": protective.get("id"),
                "currency": order.get("currency"),
                "sourceCurrency": order.get("sourceCurrency"),
                "fxRate": order.get("fxRate") or 1,
                "status": "FILLED",
                "createdAt": now,
                "reason": reason,
                "exitKind": exit_kind,
                "stopRate": protective_rate,
                "stopTriggerPrice": trigger_price,
                "targetRate": target_rate,
                "returnRate": rate,
                "observedReturnRate": observed_rate,
                "profit": (fill_price - entry) * decimal(order.get("quantity") or 1),
                "fillPolicy": protective.get("fillPolicy") or "LAST_OBSERVED_PRICE",
                "strategyRevision": entry_policy.get("revision") or order.get("strategyRevision"),
            }
        if exit_kind == "시간청산":
            exit_order["postExitStudy"] = build_post_exit_study(
                fill_price, entry, now
            )
        orders.append(exit_order)
        changed = True
    if changed:
        save_paper_orders(orders)
    return orders, changed


def paper_trade(
    env: dict[str, str], results: list[dict[str, Any]], market: str, session: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with PAPER_LOCK:
        return paper_trade_locked(env, results, market, session)


def paper_trade_locked(
    env: dict[str, str], results: list[dict[str, Any]], market: str, session: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = strategy_config()
    execution_policy = strategy_execution_policy(config)
    runtime = execution_policy.get("parameters") or {}
    orders = load_paper_orders()
    orders, _ = close_paper_positions_if_needed(env, orders, results, market, session)
    results_by_symbol = {str(item.get("symbol") or ""): item for item in results if item.get("symbol")}
    learning_state = sync_learning_brain(orders, results_by_symbol)
    apply_global_scores(results, learning_state)
    learning_brain = learning_brain_payload(learning_state)
    summary = paper_summary(orders, results)
    summary["learningBrain"] = learning_brain.get("summary")
    summary["learningCoverage"] = "GLOBAL_ALL_SYMBOLS"
    summary["capitalAllocationPolicy"] = {
        "mode": "unlimited-paper-experience" if execution_policy.get("unlimitedFunding") else "bounded-paper-capital",
        "referenceCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
        "fundingLimit": "UNLIMITED" if execution_policy.get("unlimitedFunding") else PAPER_STARTING_CAPITAL_KRW,
        "maxSinglePositionRate": runtime.get("maxAllocationRate") or PAPER_MAX_SINGLE_POSITION_RATE,
        "minExperienceEntryRate": runtime.get("minAllocationRate") or PAPER_MIN_EXPERIENCE_ENTRY_RATE,
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
    if (
        not execution_policy.get("learningSprint")
        and len(todays_market_orders) >= int(config.get("maxDailyOrders") or PAPER_MAX_DAILY_ORDERS)
    ):
        return orders[-50:], summary
    existing = {(item.get("market"), item.get("symbol")) for item in open_paper_positions(orders).values()}
    capital = summary.get("capital") or {}
    max_open_positions = int(config.get("maxOpenPositions") or PAPER_MAX_OPEN_POSITIONS)
    open_position_count = int(summary.get("openPositionCount") or 0)
    if not execution_policy.get("unlimitedPositions") and open_position_count >= max_open_positions:
        return orders[-50:], summary
    cooldown_seconds = int(
        runtime.get("reentryCooldownSeconds") or PAPER_STOP_REENTRY_COOLDOWN_SECONDS
    )
    sample_counts = market_entry_sample_counts(orders, market, today)
    summary["sampleDiversity"] = sample_diversity_summary(
        orders, market, cooldown_seconds
    )
    candidate = None
    candidate_policy = None
    candidate_capital_plan = None
    candidate_sample_count = 0
    quantity = 0
    allocation_rate = 0.0
    allocated_krw = 0.0
    learning_decisions: list[dict[str, Any]] = []
    exit_cooldown = (
        recent_exit_cooldown_symbols(
            orders,
            market,
            cooldown_seconds=cooldown_seconds,
        )
        if execution_policy.get("reentryCooldown")
        else set()
    )
    ranked_candidates = rank_candidates_for_sample_diversity(
        [
            item for item in results
            if (
                item.get("verdict") == "정밀 분석"
                if execution_policy.get("liquidityFilter")
                else item.get("verdict") != "진입 불가" and decimal(item.get("lastPrice")) > 0
            )
            and (market, item.get("symbol")) not in existing
        ],
        sample_counts,
    )
    for item in ranked_candidates:
        symbol = str(item.get("symbol") or "")
        symbol_sample_count = int(sample_counts.get(symbol, 0))
        if symbol in exit_cooldown:
            learning_decisions.append(
                {
                    "symbol": symbol,
                    "name": item.get("name") or symbol,
                    "allowed": False,
                    "capitalAllowed": False,
                    "reason": f"최근 청산 후 {cooldown_seconds}초 표본 균형 대기",
                    "scope": "SAMPLE_DIVERSITY",
                    "todaySymbolSamples": symbol_sample_count,
                }
            )
            continue
        policy = learning_entry_policy(symbol, item.get("score"), learning_state)
        if execution_policy.get("scoreFilter"):
            required_floor = int(runtime.get("entryScoreFloor") or LEARNING_BASE_ENTRY_SCORE)
            policy["requiredScore"] = max(int(policy.get("requiredScore") or 0), required_floor)
            policy["allowed"] = decimal(item.get("score")) >= policy["requiredScore"]
            if not policy["allowed"]:
                policy["reason"] = f"실행 전략 기준 · {policy['requiredScore']}점 필요 (현재 {decimal(item.get('score')):.1f}점)"
        else:
            policy["allowed"] = True
            policy["requiredScore"] = 0
            policy["reason"] = "점수 진입 전략 비활성 · 후보 필터만 적용"
        decision = dict(policy)
        decision["name"] = item.get("name") or symbol
        decision["todaySymbolSamples"] = symbol_sample_count
        decision["selectionPriority"] = "LEAST_SAMPLED_THEN_SCORE"
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
            execution_policy,
        )
        budget = decimal(capital_plan.get("plannedBudgetKrw"))
        decision["capitalAllowed"] = budget > 0
        decision["capitalPlan"] = capital_plan
        if budget <= 0:
            decision["reason"] = f"{policy.get('reason')} · 배정액 계산 실패"
            learning_decisions.append(decision)
            continue
        shares = int(budget // price_krw)
        if shares < 1 and execution_policy.get("unlimitedFunding"):
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
        candidate_sample_count = symbol_sample_count
        quantity = shares
        allocated_krw = price_krw * shares
        working_capital = decimal(capital_plan.get("workingCapitalKrw"))
        allocation_rate = allocated_krw / working_capital if working_capital else 0.0
        break
    if candidate:
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        buy_order = {
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
                "sampleDiversity": {
                    "todaySymbolEntriesBefore": candidate_sample_count,
                    "marketUniqueSymbolsBefore": len(sample_counts),
                    "cooldownSeconds": cooldown_seconds,
                    "selectionPriority": "LEAST_SAMPLED_THEN_SCORE",
                },
                "capitalPolicy": {
                    "mode": candidate_capital_plan.get("mode") if candidate_capital_plan else (
                        "unlimited-paper-experience" if execution_policy.get("unlimitedFunding") else "bounded-paper-capital"
                    ),
                    "referenceCapitalKrw": candidate_capital_plan.get("referenceCapitalKrw") if candidate_capital_plan else PAPER_STARTING_CAPITAL_KRW,
                    "fundingLimit": candidate_capital_plan.get("fundingLimit") if candidate_capital_plan else (
                        "UNLIMITED" if execution_policy.get("unlimitedFunding") else PAPER_STARTING_CAPITAL_KRW
                    ),
                    "investedBeforeKrw": candidate_capital_plan.get("investedBeforeKrw") if candidate_capital_plan else 0,
                    "targetInvestedKrw": candidate_capital_plan.get("targetInvestedKrw") if candidate_capital_plan else None,
                    "remainingSlots": candidate_capital_plan.get("remainingSlots") if candidate_capital_plan else None,
                    "expectedUtilizationRate": (
                        (decimal(candidate_capital_plan.get("investedBeforeKrw")) + allocated_krw)
                        / decimal(candidate_capital_plan.get("workingCapitalKrw"))
                        if candidate_capital_plan and decimal(candidate_capital_plan.get("workingCapitalKrw"))
                        else allocation_rate
                    ),
                    "learningAppliedAfterSizing": True,
                },
                "status": "FILLED",
                "createdAt": created_at,
                "reason": candidate.get("reason"),
                "entryScore": candidate.get("score"),
                "baseEntryScore": candidate.get("baseScore"),
                "scoreFeatures": candidate.get("scoreFeatures"),
                "scoreAudit": candidate.get("scoreAudit"),
                "learningPolicy": {
                    "requiredScore": candidate_policy.get("requiredScore") if candidate_policy else LEARNING_BASE_ENTRY_SCORE,
                    "candidateScore": candidate_policy.get("candidateScore") if candidate_policy else candidate.get("score"),
                    "allocationScale": candidate_policy.get("allocationScale") if candidate_policy else 1.0,
                    "status": candidate_policy.get("status") if candidate_policy else "신규 학습",
                    "traits": candidate_policy.get("traits") if candidate_policy else ["표본 수집"],
                    "reason": candidate_policy.get("reason") if candidate_policy else "신규 종목 기본 기준",
                    "scope": "GLOBAL_ALL_SYMBOLS",
                    "globalSampleCount": candidate_policy.get("globalSampleCount") if candidate_policy else 0,
                    "appliedImmediately": True,
                    "appliedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                "strategyIds": list(execution_policy.get("enabledIds") or []),
                "strategyRevision": execution_policy.get("revision"),
                "strategySavedAt": execution_policy.get("savedAt"),
                "strategyExecution": execution_policy,
            }
        if execution_policy.get("hardStop"):
            buy_order["protectiveStopOrder"] = build_protective_stop_order(
                buy_order, decimal(runtime.get("stopRate") or config.get("stopRate") or PAPER_STOP_RATE)
            )
        orders.append(buy_order)
        save_paper_orders(orders)
        summary = paper_summary(orders, results)
    summary["learningBrain"] = learning_brain.get("summary")
    summary["learningCoverage"] = "GLOBAL_ALL_SYMBOLS"
    summary["capitalAllocationPolicy"] = {
        "mode": "unlimited-paper-experience" if execution_policy.get("unlimitedFunding") else "bounded-paper-capital",
        "referenceCapitalKrw": PAPER_STARTING_CAPITAL_KRW,
        "fundingLimit": "UNLIMITED" if execution_policy.get("unlimitedFunding") else PAPER_STARTING_CAPITAL_KRW,
        "maxSinglePositionRate": runtime.get("maxAllocationRate") or PAPER_MAX_SINGLE_POSITION_RATE,
        "minExperienceEntryRate": runtime.get("minAllocationRate") or PAPER_MIN_EXPERIENCE_ENTRY_RATE,
        "learningAppliedAfterSizing": True,
    }
    summary["sampleDiversity"] = sample_diversity_summary(
        orders, market, cooldown_seconds
    )
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
    with LEARNING_LOCK:
        score_model = normalize_global_score_model(load_learning_state_unlocked().get("globalScoreModel"))
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
        result = {
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
                "scoreComponents": {
                    "liquidity": liquidity_score,
                    "momentum": momentum_score,
                    "stability": stability_score,
                },
            }
        results.append(apply_global_score_to_candidate(result, score_model))
    return results


def study_universe(env: dict[str, str], market: str) -> list[dict[str, Any]]:
    universe: dict[str, dict[str, Any]] = {}
    for duration in ("1d", "1w", "1mo"):
        query = urllib.parse.urlencode(
            {
                "type": "MARKET_TRADING_AMOUNT",
                "marketCountry": market,
                "duration": duration,
                "excludeInvestmentCaution": "true",
                "count": str(OFF_MARKET_STUDY_UNIVERSE_PER_HORIZON),
            }
        )
        rows = (toss_get(f"/api/v1/rankings?{query}", env).get("result") or {}).get("rankings") or []
        for row in rows:
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            item = universe.setdefault(
                symbol,
                {"symbol": symbol, "name": symbol, "market": market, "sourceHorizons": []},
            )
            item["sourceHorizons"].append(duration)
        time.sleep(0.22)
    symbols = list(universe)
    if symbols:
        stocks = toss_get(
            f"/api/v1/stocks?{urllib.parse.urlencode({'symbols': ','.join(symbols)})}", env
        ).get("result") or []
        names = {str(item.get("symbol") or ""): item.get("name") for item in stocks}
        for symbol, item in universe.items():
            item["name"] = names.get(symbol) or symbol
    return list(universe.values())


def study_daily_candles(env: dict[str, str], symbol: str) -> list[dict[str, Any]]:
    by_timestamp: dict[str, dict[str, Any]] = {}
    before = None
    for _ in range(OFF_MARKET_STUDY_CANDLE_PAGES):
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": "1d",
            "count": 200,
            "adjusted": "true",
        }
        if before:
            params["before"] = before
        result = toss_get(f"/api/v1/candles?{urllib.parse.urlencode(params)}", env).get("result") or {}
        candles = result.get("candles") or []
        for raw in candles:
            timestamp = str(raw.get("timestamp") or "")
            if not timestamp:
                continue
            by_timestamp[timestamp] = {
                "timestamp": timestamp,
                "open": decimal(raw.get("openPrice")),
                "high": decimal(raw.get("highPrice")),
                "low": decimal(raw.get("lowPrice")),
                "close": decimal(raw.get("closePrice")),
                "volume": decimal(raw.get("volume")),
            }
        next_before = result.get("nextBefore")
        if not next_before or next_before == before or not candles:
            break
        before = str(next_before)
        time.sleep(0.23)
    return sorted(by_timestamp.values(), key=lambda item: str(item.get("timestamp") or ""))


def aggregate_study_candles(candles: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    if timeframe == "1d":
        return list(candles)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candle in candles:
        moment = parse_order_time(candle.get("timestamp"))
        if not moment:
            continue
        if timeframe == "1w":
            iso_year, iso_week, _ = moment.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        else:
            key = moment.strftime("%Y-%m")
        grouped.setdefault(key, []).append(candle)
    aggregated = []
    for bucket in grouped.values():
        bucket.sort(key=lambda item: str(item.get("timestamp") or ""))
        aggregated.append(
            {
                "timestamp": bucket[-1].get("timestamp"),
                "open": decimal(bucket[0].get("open")),
                "high": max(decimal(item.get("high")) for item in bucket),
                "low": min(decimal(item.get("low")) for item in bucket),
                "close": decimal(bucket[-1].get("close")),
                "volume": sum(decimal(item.get("volume")) for item in bucket),
            }
        )
    return sorted(aggregated, key=lambda item: str(item.get("timestamp") or ""))


def study_technical_snapshot(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [decimal(item.get("close")) for item in candles if decimal(item.get("close")) > 0]
    volumes = [decimal(item.get("volume")) for item in candles]
    if len(closes) < 5:
        return {"status": "insufficient", "barCount": len(closes)}
    latest = closes[-1]
    sma5 = sum(closes[-5:]) / min(5, len(closes))
    long_window = closes[-min(20, len(closes)):]
    sma20 = sum(long_window) / len(long_window)
    returns = [
        (closes[index] / closes[index - 1]) - 1
        for index in range(max(1, len(closes) - 20), len(closes))
        if closes[index - 1]
    ]
    average_return = sum(returns) / len(returns) if returns else 0.0
    variance = sum((value - average_return) ** 2 for value in returns) / len(returns) if returns else 0.0
    gains = [max(0.0, value) for value in returns[-14:]]
    losses = [max(0.0, -value) for value in returns[-14:]]
    average_gain = sum(gains) / len(gains) if gains else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    rsi = 100.0 if average_loss <= 0 else 100 - (100 / (1 + (average_gain / average_loss)))
    volume_window = [value for value in volumes[-20:] if value >= 0]
    volume_average = sum(volume_window) / len(volume_window) if volume_window else 0.0
    peak = closes[max(0, len(closes) - 60)]
    max_drawdown = 0.0
    for value in closes[-60:]:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, (value / peak) - 1)
    trend = "상승" if latest > sma20 and sma5 > sma20 else ("하락" if latest < sma20 and sma5 < sma20 else "혼조")
    return {
        "status": "ready",
        "barCount": len(closes),
        "lastClose": latest,
        "sma5": sma5,
        "sma20": sma20,
        "return5": (latest / closes[-6]) - 1 if len(closes) >= 6 and closes[-6] else 0.0,
        "return20": (latest / closes[-21]) - 1 if len(closes) >= 21 and closes[-21] else 0.0,
        "volatility20": math.sqrt(max(0.0, variance)),
        "volumeRatio": (volumes[-1] / volume_average) if volume_average and volumes else 0.0,
        "rsi14": rsi,
        "maxDrawdown60": max_drawdown,
        "trend": trend,
    }


def study_pattern_observations(candles: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    if len(candles) < 26:
        return []
    buckets: dict[str, dict[str, Any]] = {}
    target_rate = {"1d": 0.03, "1w": 0.07, "1mo": 0.15}[timeframe]
    stop_rate = {"1d": -0.02, "1w": -0.04, "1mo": -0.08}[timeframe]
    for index in range(20, len(candles) - 5):
        window = candles[index - 19: index + 1]
        closes = [decimal(item.get("close")) for item in window]
        volumes = [decimal(item.get("volume")) for item in window]
        current = candles[index]
        close = decimal(current.get("close"))
        if close <= 0 or any(value <= 0 for value in closes):
            continue
        sma5 = sum(closes[-5:]) / 5
        sma20 = sum(closes) / 20
        return5 = (close / closes[-6]) - 1
        returns14 = [(closes[i] / closes[i - 1]) - 1 for i in range(6, 20)]
        gains = [max(0.0, value) for value in returns14]
        losses = [max(0.0, -value) for value in returns14]
        avg_gain = sum(gains) / len(gains) if gains else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        rsi = 100.0 if avg_loss <= 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
        average_volume = sum(volumes) / len(volumes) if volumes else 0.0
        volume_ratio = decimal(current.get("volume")) / average_volume if average_volume else 1.0
        ranges = [
            (decimal(item.get("high")) - decimal(item.get("low"))) / decimal(item.get("close"))
            for item in window
            if decimal(item.get("close")) > 0
        ]
        current_range = ranges[-1] if ranges else 0.0
        average_range = sum(ranges) / len(ranges) if ranges else 0.0
        range_ratio = current_range / average_range if average_range else 1.0
        candle_range = decimal(current.get("high")) - decimal(current.get("low"))
        close_position = (
            (close - decimal(current.get("low"))) / candle_range if candle_range > 0 else 0.5
        )
        trend = "상승정렬" if close > sma20 and sma5 > sma20 else ("하락정렬" if close < sma20 and sma5 < sma20 else "혼조")
        momentum = "강한상승" if return5 >= 0.05 else ("상승" if return5 > 0 else ("강한하락" if return5 <= -0.05 else "하락"))
        volume = "거래량급증" if volume_ratio >= 1.50 else ("거래량고갈" if volume_ratio <= 0.65 else "거래량보통")
        volatility = "변동성확대" if range_ratio >= 1.40 else ("변동성축소" if range_ratio <= 0.70 else "변동성보통")
        rsi_zone = "과매수" if rsi >= 70 else ("과매도" if rsi <= 30 else "RSI중립")
        close_zone = "고가마감" if close_position >= 0.75 else ("저가마감" if close_position <= 0.25 else "중간마감")
        key = "|".join((trend, momentum, volume, volatility, rsi_zone, close_zone))
        label = " · ".join((trend, momentum, volume, volatility, rsi_zone, close_zone))
        future = candles[index + 1: index + 6]
        future_returns = {
            "return1": (decimal(future[0].get("close")) / close) - 1,
            "return3": (decimal(future[2].get("close")) / close) - 1,
            "return5": (decimal(future[4].get("close")) / close) - 1,
        }
        target_hit = any(decimal(item.get("high")) >= close * (1 + target_rate) for item in future)
        stop_hit = any(decimal(item.get("low")) <= close * (1 + stop_rate) for item in future)
        bucket = buckets.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "timeframe": timeframe,
                "count": 0,
                "win1": 0,
                "win3": 0,
                "win5": 0,
                "return1Sum": 0.0,
                "return3Sum": 0.0,
                "return5Sum": 0.0,
                "targetHitCount": 0,
                "stopHitCount": 0,
            },
        )
        bucket["count"] += 1
        for horizon in (1, 3, 5):
            value = future_returns[f"return{horizon}"]
            bucket[f"return{horizon}Sum"] += value
            if value > 0:
                bucket[f"win{horizon}"] += 1
        if target_hit:
            bucket["targetHitCount"] += 1
        if stop_hit:
            bucket["stopHitCount"] += 1
    return list(buckets.values())


def summarize_study_patterns(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    combined: dict[str, dict[str, Any]] = {}
    observation_count = 0
    for analysis in analyses:
        for raw in analysis.get("patterns") or []:
            timeframe = str(raw.get("timeframe") or analysis.get("timeframe") or "")
            key = f"{timeframe}:{raw.get('key')}"
            bucket = combined.setdefault(
                key,
                {
                    "key": raw.get("key"),
                    "label": raw.get("label"),
                    "timeframe": timeframe,
                    "count": 0,
                    "win1": 0,
                    "win3": 0,
                    "win5": 0,
                    "return1Sum": 0.0,
                    "return3Sum": 0.0,
                    "return5Sum": 0.0,
                    "targetHitCount": 0,
                    "stopHitCount": 0,
                    "symbolCount": 0,
                },
            )
            count = int(raw.get("count") or 0)
            observation_count += count
            bucket["count"] += count
            bucket["symbolCount"] += 1
            for field in (
                "win1",
                "win3",
                "win5",
                "targetHitCount",
                "stopHitCount",
            ):
                bucket[field] += int(raw.get(field) or 0)
            for field in ("return1Sum", "return3Sum", "return5Sum"):
                bucket[field] += decimal(raw.get(field))
    catalog = []
    for bucket in combined.values():
        count = int(bucket.get("count") or 0)
        if not count:
            continue
        bucket["winRate1"] = int(bucket.get("win1") or 0) / count
        bucket["winRate3"] = int(bucket.get("win3") or 0) / count
        bucket["winRate5"] = int(bucket.get("win5") or 0) / count
        bucket["averageReturn1"] = decimal(bucket.get("return1Sum")) / count
        bucket["averageReturn3"] = decimal(bucket.get("return3Sum")) / count
        bucket["averageReturn5"] = decimal(bucket.get("return5Sum")) / count
        bucket["targetHitRate"] = int(bucket.get("targetHitCount") or 0) / count
        bucket["stopHitRate"] = int(bucket.get("stopHitCount") or 0) / count
        bucket["confidenceScore"] = min(1.0, count / 50) * abs(decimal(bucket.get("averageReturn5")))
        catalog.append(bucket)
    reliable = [item for item in catalog if int(item.get("count") or 0) >= 20 and int(item.get("symbolCount") or 0) >= 3]
    positive = sorted(
        (item for item in reliable if decimal(item.get("averageReturn5")) > 0),
        key=lambda item: (decimal(item.get("averageReturn5")) * math.sqrt(int(item.get("count") or 0)), decimal(item.get("winRate5"))),
        reverse=True,
    )[:12]
    negative = sorted(
        (item for item in reliable if decimal(item.get("averageReturn5")) < 0),
        key=lambda item: (decimal(item.get("averageReturn5")) * math.sqrt(int(item.get("count") or 0)), -decimal(item.get("winRate5"))),
    )[:12]
    journal = []
    for kind, rows in (("재현 후보", positive), ("실패 가설", negative)):
        for item in rows[:6]:
            journal.append(
                {
                    "kind": kind,
                    "timeframe": item.get("timeframe"),
                    "pattern": item.get("label"),
                    "observationCount": item.get("count"),
                    "symbolCount": item.get("symbolCount"),
                    "winRate5": item.get("winRate5"),
                    "averageReturn5": item.get("averageReturn5"),
                    "targetHitRate": item.get("targetHitRate"),
                    "stopHitRate": item.get("stopHitRate"),
                    "note": (
                        f"{item.get('timeframe')} {item.get('label')} 이후 5봉 평균 "
                        f"{decimal(item.get('averageReturn5')) * 100:+.2f}% · 승률 {decimal(item.get('winRate5')) * 100:.1f}% "
                        f"({int(item.get('count') or 0)}회, {int(item.get('symbolCount') or 0)}종목)"
                    ),
                }
            )
    return {
        "observationCount": observation_count,
        "uniquePatternCount": len(catalog),
        "reliablePatternCount": len(reliable),
        "positive": positive,
        "negative": negative,
        "journal": journal,
    }


def study_backtest(
    candles: list[dict[str, Any]], timeframe: str, research_pass: int = 1
) -> dict[str, Any]:
    profiles = (
        {
            "name": "보수형",
            "settings": {
                "1d": {"target": 0.02, "stop": -0.01, "hold": 3},
                "1w": {"target": 0.05, "stop": -0.03, "hold": 3},
                "1mo": {"target": 0.10, "stop": -0.06, "hold": 2},
            },
        },
        {
            "name": "균형형",
            "settings": {
                "1d": {"target": 0.03, "stop": -0.02, "hold": 5},
                "1w": {"target": 0.07, "stop": -0.04, "hold": 4},
                "1mo": {"target": 0.15, "stop": -0.08, "hold": 3},
            },
        },
        {
            "name": "공격형",
            "settings": {
                "1d": {"target": 0.05, "stop": -0.03, "hold": 10},
                "1w": {"target": 0.12, "stop": -0.06, "hold": 6},
                "1mo": {"target": 0.25, "stop": -0.10, "hold": 5},
            },
        },
    )
    profile = profiles[max(0, min(2, int(research_pass)))]
    settings = profile["settings"][timeframe]
    if len(candles) < 12:
        return {"tradeCount": 0, "winCount": 0, "winRate": 0.0, "averageReturn": 0.0, "returnSum": 0.0}
    returns: list[float] = []
    index = 10
    while index < len(candles) - 1:
        closes = [decimal(item.get("close")) for item in candles[: index + 1]]
        entry = closes[-1]
        short = sum(closes[-5:]) / min(5, len(closes))
        long_values = closes[-min(20, len(closes)):]
        long = sum(long_values) / len(long_values)
        momentum = (entry / closes[-6]) - 1 if len(closes) >= 6 and closes[-6] else 0.0
        volume_values = [decimal(item.get("volume")) for item in candles[max(0, index - 19): index + 1]]
        average_volume = sum(volume_values) / len(volume_values) if volume_values else 0.0
        volume_ratio = decimal(candles[index].get("volume")) / average_volume if average_volume else 1.0
        signal = entry > long and short > long and 0 < momentum < 0.20 and volume_ratio >= 0.80
        if not signal or entry <= 0:
            index += 1
            continue
        exit_index = min(len(candles) - 1, index + int(settings["hold"]))
        result = None
        for future_index in range(index + 1, exit_index + 1):
            future = candles[future_index]
            if decimal(future.get("low")) <= entry * (1 + decimal(settings["stop"])):
                result = decimal(settings["stop"])
                exit_index = future_index
                break
            if decimal(future.get("high")) >= entry * (1 + decimal(settings["target"])):
                result = decimal(settings["target"])
                exit_index = future_index
                break
        if result is None:
            result = (decimal(candles[exit_index].get("close")) / entry) - 1
        returns.append(result)
        index = exit_index + 1
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak) - 1 if peak else 0.0)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "tradeCount": len(returns),
        "winCount": len(wins),
        "winRate": len(wins) / len(returns) if returns else 0.0,
        "averageReturn": sum(returns) / len(returns) if returns else 0.0,
        "returnSum": sum(returns),
        "profitFactor": gross_profit / gross_loss if gross_loss else (None if not gross_profit else 99.0),
        "maxDrawdown": max_drawdown,
        "targetRate": settings["target"],
        "stopRate": settings["stop"],
        "holdingBars": settings["hold"],
        "researchPass": profile["name"],
    }


def summarize_off_market_backtests(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    timeframes: dict[str, dict[str, Any]] = {}
    total_trades = 0
    total_wins = 0
    total_return = 0.0
    for analysis in analyses:
        timeframe = str(analysis.get("timeframe") or "")
        backtest = analysis.get("backtest") or {}
        trades = int(backtest.get("tradeCount") or 0)
        wins = int(backtest.get("winCount") or 0)
        return_sum = decimal(backtest.get("returnSum"))
        bucket = timeframes.setdefault(timeframe, {"tradeCount": 0, "winCount": 0, "returnSum": 0.0, "analysisCount": 0})
        bucket["tradeCount"] += trades
        bucket["winCount"] += wins
        bucket["returnSum"] += return_sum
        bucket["analysisCount"] += 1
        total_trades += trades
        total_wins += wins
        total_return += return_sum
    for bucket in timeframes.values():
        trades = int(bucket.get("tradeCount") or 0)
        bucket["winRate"] = int(bucket.get("winCount") or 0) / trades if trades else 0.0
        bucket["averageReturn"] = decimal(bucket.get("returnSum")) / trades if trades else 0.0
    return {
        "analysisCount": len(analyses),
        "tradeCount": total_trades,
        "winCount": total_wins,
        "winRate": total_wins / total_trades if total_trades else 0.0,
        "averageReturn": total_return / total_trades if total_trades else 0.0,
        "timeframes": timeframes,
    }


def apply_off_market_backtest_influence(model: dict[str, Any], summary: dict[str, Any], study_id: str) -> dict[str, Any]:
    refreshed = normalize_global_score_model(model)
    total_trades = int(summary.get("tradeCount") or 0)
    timeframe_results = summary.get("timeframes") or {}
    positive = sum(
        1 for item in timeframe_results.values()
        if int(item.get("tradeCount") or 0) >= 5
        and decimal(item.get("winRate")) >= 0.55
        and decimal(item.get("averageReturn")) > 0
    )
    negative = sum(
        1 for item in timeframe_results.values()
        if int(item.get("tradeCount") or 0) >= 5
        and (decimal(item.get("winRate")) < 0.45 or decimal(item.get("averageReturn")) < 0)
    )
    momentum_step = 0.0
    if total_trades >= 20 and positive >= 2:
        momentum_step = 0.01
    elif total_trades >= 20 and negative >= 2:
        momentum_step = -0.01
    before = decimal((refreshed.get("effectiveWeights") or {}).get("momentum") or 1.0)
    if momentum_step:
        refreshed.setdefault("weights", {})["momentum"] = clamp(
            decimal((refreshed.get("weights") or {}).get("momentum") or 1.0) + momentum_step,
            GLOBAL_SCORE_WEIGHT_MIN,
            GLOBAL_SCORE_WEIGHT_MAX,
            1.0,
        )
        refresh_global_score_model(refreshed)
    after = decimal((refreshed.get("effectiveWeights") or {}).get("momentum") or 1.0)
    influence = {
        "applied": bool(momentum_step),
        "eligible": total_trades >= 20,
        "tradeCount": total_trades,
        "positiveTimeframes": positive,
        "negativeTimeframes": negative,
        "feature": "momentum",
        "label": GLOBAL_SCORE_FEATURES["momentum"]["label"],
        "before": before,
        "after": after,
        "delta": after - before,
        "capPerStudy": 0.01,
        "reason": "일·주·월봉 중 두 개 이상에서 같은 방향이 반복될 때만 저강도로 반영",
    }
    if momentum_step:
        revision = {
            "id": hashlib.sha1(f"offline:{study_id}".encode("utf-8")).hexdigest()[:12],
            "tradeKey": study_id,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "result": "휴장 백테스트",
            "summary": f"한국·미국 일·주·월봉 {total_trades}회 검증: 당일 추세 기준 {'강화' if momentum_step > 0 else '약화'} ({before:.3f}→{after:.3f})",
            "changes": [{"key": "momentum", "label": GLOBAL_SCORE_FEATURES["momentum"]["label"], "before": before, "after": after, "delta": after - before, "direction": "강화" if momentum_step > 0 else "약화"}],
            "scope": "OFF_MARKET_BACKTEST",
            "sampleCount": int(refreshed.get("sampleCount") or 0),
        }
        refreshed["revisionCount"] = int(refreshed.get("revisionCount") or 0) + 1
        refreshed["offlineRevisionCount"] = int(refreshed.get("offlineRevisionCount") or 0) + 1
        refreshed.setdefault("revisions", []).append(revision)
        refreshed["revisions"] = refreshed.get("revisions", [])[-50:]
        refreshed["lastChange"] = revision
        refreshed["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    model.clear()
    model.update(refreshed)
    return influence


def build_symbol_study_catalog(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group every technical result as symbol -> daily/weekly/monthly evidence."""
    grouped: dict[str, dict[str, Any]] = {}
    timeframe_order = {"1d": 0, "1w": 1, "1mo": 2}
    timeframe_labels = {"1d": "일봉", "1w": "주봉", "1mo": "월봉"}
    for analysis in analyses:
        market = str(analysis.get("market") or "")
        symbol = str(analysis.get("symbol") or "")
        timeframe = str(analysis.get("timeframe") or "")
        if not symbol or timeframe not in timeframe_order:
            continue
        key = f"{market}:{symbol}"
        study = grouped.setdefault(
            key,
            {
                "market": market,
                "symbol": symbol,
                "name": analysis.get("name") or symbol,
                "sourceHorizons": list(analysis.get("sourceHorizons") or []),
                "timeframes": {},
            },
        )
        technical = dict(analysis.get("technical") or {})
        backtest = dict(analysis.get("backtest") or {})
        patterns = list(analysis.get("patterns") or [])
        study["timeframes"][timeframe] = {
            "timeframe": timeframe,
            "label": timeframe_labels[timeframe],
            "technical": technical,
            "backtest": backtest,
            "patternObservationCount": int(analysis.get("patternObservationCount") or 0),
            "topPatterns": patterns[:5],
        }

    catalog: list[dict[str, Any]] = []
    for study in grouped.values():
        timeframes = study.get("timeframes") or {}
        ordered = [timeframes[key] for key in sorted(timeframes, key=lambda item: timeframe_order.get(item, 99))]
        ready = [item for item in ordered if (item.get("technical") or {}).get("status") == "ready"]
        up_count = sum(1 for item in ready if (item.get("technical") or {}).get("trend") == "상승")
        down_count = sum(1 for item in ready if (item.get("technical") or {}).get("trend") == "하락")
        if up_count == 3:
            verdict, tone = "일·주·월 상승 정렬", "positive"
        elif down_count == 3:
            verdict, tone = "일·주·월 하락 정렬", "negative"
        elif up_count >= 2:
            verdict, tone = "중장기 상승 우세", "positive"
        elif down_count >= 2:
            verdict, tone = "중장기 하락 우세", "negative"
        else:
            verdict, tone = "시간대별 혼조", "neutral"
        study["timeframes"] = ordered
        study["completeTimeframeCount"] = len(ready)
        study["complete"] = len(ready) == 3
        study["verdict"] = verdict
        study["tone"] = tone
        study["patternObservationCount"] = sum(
            int(item.get("patternObservationCount") or 0) for item in ordered
        )
        study["backtestTradeCount"] = sum(
            int((item.get("backtest") or {}).get("tradeCount") or 0) for item in ordered
        )
        catalog.append(study)
    catalog.sort(
        key=lambda item: (
            0 if item.get("complete") else 1,
            str(item.get("market") or ""),
            str(item.get("name") or item.get("symbol") or ""),
        )
    )
    return catalog


def run_off_market_study(env: dict[str, str]) -> dict[str, Any]:
    started = now_kst()
    study_id = f"OFFLINE-{started.strftime('%Y-%m-%d')}"
    research_pass = started.toordinal() % 3
    research_pass_name = ("보수형", "균형형", "공격형")[research_pass]
    analyses: list[dict[str, Any]] = []
    errors: list[str] = []
    universe_count = 0
    for market in ("KR", "US"):
        try:
            universe = study_universe(env, market)
        except Exception as exc:
            errors.append(f"{market} 유니버스: {str(exc)[:180]}")
            continue
        universe_count += len(universe)
        for stock in universe:
            try:
                daily = study_daily_candles(env, str(stock.get("symbol") or ""))
                for timeframe in ("1d", "1w", "1mo"):
                    candles = aggregate_study_candles(daily, timeframe)
                    analyses.append(
                        {
                            "market": market,
                            "symbol": stock.get("symbol"),
                            "name": stock.get("name"),
                            "sourceHorizons": stock.get("sourceHorizons"),
                            "timeframe": timeframe,
                            "technical": study_technical_snapshot(candles),
                            "backtest": study_backtest(candles, timeframe, research_pass),
                            "patterns": study_pattern_observations(candles, timeframe),
                        }
                    )
            except Exception as exc:
                errors.append(f"{market} {stock.get('symbol')}: {str(exc)[:180]}")
            time.sleep(0.23)
    summary = summarize_off_market_backtests(analyses)
    pattern_summary = summarize_study_patterns(analyses)
    summary["patternObservationCount"] = int(pattern_summary.get("observationCount") or 0)
    summary["reliablePatternCount"] = int(pattern_summary.get("reliablePatternCount") or 0)
    for analysis in analyses:
        patterns = sorted(
            analysis.get("patterns") or [],
            key=lambda item: int(item.get("count") or 0),
            reverse=True,
        )
        analysis["patternObservationCount"] = sum(int(item.get("count") or 0) for item in patterns)
        analysis["patterns"] = patterns[:8]
    symbol_studies = build_symbol_study_catalog(analyses)
    summary["completeSymbolCount"] = sum(1 for item in symbol_studies if item.get("complete"))
    with LEARNING_LOCK:
        state = load_learning_state_unlocked()
        model = state.setdefault("globalScoreModel", default_global_score_model())
        influence = apply_off_market_backtest_influence(model, summary, study_id)
        completed = now_kst()
        study = {
            "id": study_id,
            "status": "completed" if analyses else "error",
            "lastRunDate": started.strftime("%Y-%m-%d"),
            "startedAt": started.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "completedAt": completed.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "markets": ["KR", "US"],
            "timeframes": ["1d", "1w", "1mo"],
            "researchPass": research_pass_name,
            "universeCount": universe_count,
            "summary": summary,
            "patternResearch": pattern_summary,
            "journal": pattern_summary.get("journal") or [],
            "symbolStudies": symbol_studies,
            "influence": influence,
            "analyses": analyses[-240:],
            "errors": errors[-12:],
            "nextRun": "다음 한국·미국 동시 휴장 구간의 새 거래일",
        }
        state["globalScoreModel"] = model
        state["offlineStudy"] = study
        history = list(state.get("offlineStudyHistory") or [])
        history.append(
            {
                "id": study.get("id"),
                "status": study.get("status"),
                "completedAt": study.get("completedAt"),
                "researchPass": study.get("researchPass"),
                "universeCount": study.get("universeCount"),
                "summary": study.get("summary"),
                "journal": (study.get("journal") or [])[:6],
                "influence": study.get("influence"),
            }
        )
        state["offlineStudyHistory"] = history[-30:]
        state["updatedAt"] = completed.strftime("%Y-%m-%dT%H:%M:%S%z")
        save_learning_state_unlocked(state)
    return study


def off_market_study_loop() -> None:
    time.sleep(15)
    while True:
        try:
            env = load_env()
            active_market, _ = market_schedule(env)
            today = now_kst().strftime("%Y-%m-%d")
            with LEARNING_LOCK:
                state = load_learning_state_unlocked()
                last_run_date = str((state.get("offlineStudy") or {}).get("lastRunDate") or "")
            if active_market is None and last_run_date != today:
                run_off_market_study(env)
        except Exception as exc:
            try:
                with LEARNING_LOCK:
                    state = load_learning_state_unlocked()
                    study = dict(state.get("offlineStudy") or {})
                    study.update(
                        {
                            "status": "error",
                            "lastAttemptAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "lastError": str(exc)[:500],
                        }
                    )
                    state["offlineStudy"] = study
                    save_learning_state_unlocked(state)
            except OSError:
                pass
        time.sleep(OFF_MARKET_STUDY_POLL_SECONDS)


def monitor_active_position_risks(
    env: dict[str, str], sessions: list[tuple[str, str]] | None = None
) -> tuple[list[dict[str, Any]], bool, list[tuple[str, str]]]:
    active = active_market_sessions(env) if sessions is None else list(sessions)
    changed = False
    with PAPER_LOCK:
        orders = load_paper_orders()
        for market, session in active:
            orders, market_changed = close_paper_positions_if_needed(
                env, orders, [], market, session, stop_only=True
            )
            orders, study_changed = update_post_exit_studies_if_due(
                env, orders, market
            )
            changed = changed or market_changed or study_changed
    return orders, changed, active


def position_risk_loop() -> None:
    """Watch every concurrently open PAPER market independently from candidate scans."""
    while True:
        try:
            env = load_env()
            orders, changed, active = monitor_active_position_risks(env)
            with ANALYSIS_LOCK:
                current_results = list(ANALYSIS.get("results") or [])
            paper_stats = paper_summary(orders, current_results) if changed else None
            with ANALYSIS_LOCK:
                monitor = ANALYSIS.setdefault("riskMonitor", {})
                monitor.update(
                    {
                        "enabled": True,
                        "intervalSec": PAPER_STOP_MONITOR_INTERVAL_SECONDS,
                        "activeMarkets": [
                            {"market": market, "session": session}
                            for market, session in active
                        ],
                        "lastRunAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "lastError": None,
                    }
                )
                if changed:
                    monitor["lastActionAt"] = monitor["lastRunAt"]
                    ANALYSIS["paperOrders"] = orders[-50:]
                    ANALYSIS["paperSummary"] = paper_stats
        except Exception as exc:
            with ANALYSIS_LOCK:
                monitor = ANALYSIS.setdefault("riskMonitor", {})
                monitor.update(
                    {
                        "enabled": True,
                        "intervalSec": PAPER_STOP_MONITOR_INTERVAL_SECONDS,
                        "lastRunAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "lastError": str(exc)[:300],
                    }
                )
        time.sleep(PAPER_STOP_MONITOR_INTERVAL_SECONDS)


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
        "riskMonitor": dict(analysis.get("riskMonitor") or {}),
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


def post_exit_study_memo_lines(study: dict[str, Any]) -> list[str]:
    if not isinstance(study, dict) or not study:
        return []
    horizons = study.get("horizons") if isinstance(study.get("horizons"), dict) else {}
    lines = [
        f"시간청산 사후추적: {int(study.get('observedCount') or 0)}/{len(POST_EXIT_OBSERVATION_HORIZONS)} 완료"
    ]
    for key, _ in POST_EXIT_OBSERVATION_HORIZONS:
        item = horizons.get(key) if isinstance(horizons.get(key), dict) else {}
        if item.get("status") != "OBSERVED":
            lines.append(f"사후 {key}: 관측 대기")
            continue
        lines.append(
            f"사후 {key}: 청산가 대비 {percent(item.get('fromExitRate'))} · "
            f"진입가 대비 {percent(item.get('hypotheticalReturnRate'))} · "
            f"{item.get('outcome') or '판정 대기'}"
        )
    if study.get("status") == "COMPLETE":
        lines.append(
            f"사후판정: {study.get('verdict') or '관측 부족'} · "
            f"{study.get('recommendation') or '표본을 추가 축적합니다.'}"
        )
    return lines


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
    post_exit_study = exit_order.get("postExitStudy") if isinstance(exit_order.get("postExitStudy"), dict) else {}
    protective = entry_order.get("protectiveStopOrder") if isinstance(entry_order.get("protectiveStopOrder"), dict) else {}
    stop_rate = decimal(
        exit_order.get("stopRate")
        or protective.get("stopRate")
        or strategy_config().get("stopRate")
        or PAPER_STOP_RATE
    )
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
            f"{clean_text(learning_policy.get('reason'), '전체 종목 공용 학습 기준 통과', 140)}"
        )

    if not is_closed:
        review = "관찰 필요"
        evaluation = "포지션 보유 중입니다. 손익과 청산 조건이 확정된 뒤 최종 평가합니다."
        improvement = "손실선과 목표가를 유지하고, 진입 근거가 무너지면 지체 없이 청산합니다."
        result_line = f"현재: {percent(return_rate)} · 평가손익 {money(profit)}"
        tags = ["자동 작성", "보유중"]
    elif exit_kind == "손실선":
        review = "손절 준수"
        evaluation = f"예약 보호매도가 실행됐습니다. 실제 청산 결과 {percent(return_rate)}는 매매 이력에 그대로 남깁니다."
        improvement = "같은 손실을 줄이려면 진입 직전 급등폭과 거래량 지속성을 더 엄격히 확인합니다."
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
    if protective:
        memo_lines.append(
            f"보호주문: {percent(protective.get('stopRate'))} · "
            f"발동가 {round(decimal(protective.get('triggerPrice'))):,}원 · "
            f"상태 {protective.get('status') or 'WORKING'}"
        )
    if learning_line:
        memo_lines.append(learning_line)
    memo_lines.extend(
        [
            f"청산: {exit_reason}",
            result_line,
        ]
    )
    memo_lines.extend(post_exit_study_memo_lines(post_exit_study))
    memo_lines.extend([f"복기: {evaluation}", f"다음 개선: {improvement}"])
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
    invested = sum(decimal(item.get("invested")) for item in day_entries)
    profit = sum(decimal(item.get("profit")) for item in day_entries)
    return_rate = profit / invested if invested else 0.0
    stop_count = sum(1 for item in closed if item.get("exitKind") == "손실선")
    compliant_stops = stop_count

    if not closed:
        tone = "neutral"
        headline = "아직 확정할 오답이 없습니다"
        reflection = "보유 중인 포지션의 청산 결과가 나오기 전이라 오늘의 판단을 확정하지 않았습니다."
        lesson = "결과가 나오기 전에는 좋은 진입으로 단정하지 않고, 진입 근거와 손실선을 그대로 유지합니다."
        next_rule = "청산이 끝난 뒤 진입 근거와 실제 결과를 함께 평가합니다."
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
        headline = "오늘은 전략과 수익이 함께 맞았다"
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


def default_global_score_model() -> dict[str, Any]:
    return {
        "version": 1,
        "scope": "GLOBAL_ALL_SYMBOLS",
        "sampleCount": 0,
        "winCount": 0,
        "lossCount": 0,
        "returnSum": 0.0,
        "entryThreshold": LEARNING_BASE_ENTRY_SCORE,
        "weights": {key: 1.0 for key in GLOBAL_SCORE_FEATURES},
        "effectiveWeights": {key: 1.0 for key in GLOBAL_SCORE_FEATURES},
        "featureStats": {
            key: {
                "count": 0,
                "winCount": 0,
                "lossCount": 0,
                "winnerValueSum": 0.0,
                "loserValueSum": 0.0,
                "outcomeWeightedSum": 0.0,
            }
            for key in GLOBAL_SCORE_FEATURES
        },
        "revisionCount": 0,
        "revisions": [],
        "lastChange": None,
        "updatedAt": None,
    }


def refresh_global_score_model(model: dict[str, Any]) -> dict[str, Any]:
    samples = max(0, int(model.get("sampleCount") or 0))
    wins = max(0, int(model.get("winCount") or 0))
    raw_weights = model.setdefault("weights", {})
    stats_by_feature = model.setdefault("featureStats", {})
    confidence = 0.0 if samples <= 0 else min(1.0, max(0.25, samples / 8))
    effective_weights: dict[str, float] = {}
    feature_view: list[dict[str, Any]] = []
    for key, config in GLOBAL_SCORE_FEATURES.items():
        weight = clamp(
            raw_weights.get(key),
            GLOBAL_SCORE_WEIGHT_MIN,
            GLOBAL_SCORE_WEIGHT_MAX,
            1.0,
        )
        raw_weights[key] = weight
        effective = 1.0 + ((weight - 1.0) * confidence)
        effective_weights[key] = effective
        stats = stats_by_feature.setdefault(
            key,
            {
                "count": 0,
                "winCount": 0,
                "lossCount": 0,
                "winnerValueSum": 0.0,
                "loserValueSum": 0.0,
                "outcomeWeightedSum": 0.0,
            },
        )
        feature_wins = max(0, int(stats.get("winCount") or 0))
        feature_losses = max(0, int(stats.get("lossCount") or 0))
        winner_average = decimal(stats.get("winnerValueSum")) / feature_wins if feature_wins else 0.0
        loser_average = decimal(stats.get("loserValueSum")) / feature_losses if feature_losses else 0.0
        stats["winnerAverage"] = winner_average
        stats["loserAverage"] = loser_average
        stats["edge"] = winner_average - loser_average if feature_wins and feature_losses else 0.0
        feature_view.append(
            {
                "key": key,
                "label": config["label"],
                "maxPoints": config["maxPoints"],
                "weight": weight,
                "effectiveWeight": effective,
                "winnerAverage": winner_average,
                "loserAverage": loser_average,
                "edge": stats["edge"],
                "sampleCount": int(stats.get("count") or 0),
            }
        )
    model["effectiveWeights"] = effective_weights
    win_rate = wins / samples if samples else 0.0
    average_return = decimal(model.get("returnSum")) / samples if samples else 0.0
    if samples < 4:
        threshold = LEARNING_BASE_ENTRY_SCORE
        phase = "초기 관찰"
    elif win_rate < 0.35 or average_return < -0.004:
        threshold = 83
        phase = "전역 기준 강화"
    elif win_rate < 0.50 or average_return < 0:
        threshold = 82
        phase = "손실 조건 재검증"
    elif win_rate >= 0.65 and average_return >= 0.002:
        threshold = 78
        phase = "우세 조건 확장 검증"
    else:
        threshold = LEARNING_BASE_ENTRY_SCORE
        phase = "균형 검증"
    feature_view.sort(key=lambda item: decimal(item.get("effectiveWeight")), reverse=True)
    model.update(
        {
            "scope": "GLOBAL_ALL_SYMBOLS",
            "sampleCount": samples,
            "winCount": wins,
            "lossCount": max(0, int(model.get("lossCount") or 0)),
            "winRate": win_rate,
            "averageReturn": average_return,
            "confidence": confidence,
            "entryThreshold": threshold,
            "phase": phase,
            "features": feature_view,
            "strongestFeature": feature_view[0] if feature_view else None,
            "weakestFeature": feature_view[-1] if feature_view else None,
            "globalRule": f"모든 종목에 전역 가중치 적용 · {threshold}점 이상 진입",
        }
    )
    return model


def normalize_global_score_model(raw: Any) -> dict[str, Any]:
    base = default_global_score_model()
    if isinstance(raw, dict):
        for key in (
            "version",
            "scope",
            "sampleCount",
            "winCount",
            "lossCount",
            "returnSum",
            "entryThreshold",
            "revisionCount",
            "offlineRevisionCount",
            "lastChange",
            "updatedAt",
        ):
            if key in raw:
                base[key] = raw.get(key)
        if isinstance(raw.get("weights"), dict):
            base["weights"].update(raw.get("weights") or {})
        if isinstance(raw.get("featureStats"), dict):
            for key, stats in (raw.get("featureStats") or {}).items():
                if key in base["featureStats"] and isinstance(stats, dict):
                    base["featureStats"][key].update(stats)
        base["revisions"] = list(raw.get("revisions") or [])[-50:]
    return refresh_global_score_model(base)


def global_score_audit(
    components: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    refreshed = refresh_global_score_model(model)
    effective = refreshed.get("effectiveWeights") or {}
    numerator = 0.0
    denominator = 0.0
    feature_values: dict[str, float] = {}
    component_points: dict[str, float] = {}
    for key, config in GLOBAL_SCORE_FEATURES.items():
        max_points = decimal(config.get("maxPoints"))
        points = clamp(components.get(key), 0, max_points, 0)
        weight = clamp(effective.get(key), 0.5, 1.5, 1.0)
        numerator += points * weight
        denominator += max_points * weight
        component_points[key] = points
        feature_values[key] = points / max_points if max_points else 0.0
    base_score = round(sum(component_points.values()), 1)
    adaptive_score = round(100 * numerator / denominator, 1) if denominator else base_score
    return {
        "scope": "GLOBAL_ALL_SYMBOLS",
        "baseScore": base_score,
        "adaptiveScore": adaptive_score,
        "delta": round(adaptive_score - base_score, 1),
        "entryThreshold": int(refreshed.get("entryThreshold") or LEARNING_BASE_ENTRY_SCORE),
        "sampleCount": int(refreshed.get("sampleCount") or 0),
        "confidence": decimal(refreshed.get("confidence")),
        "components": component_points,
        "features": feature_values,
        "weights": dict(effective),
        "phase": refreshed.get("phase"),
    }


def apply_global_score_to_candidate(item: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    components = item.get("scoreComponents") if isinstance(item.get("scoreComponents"), dict) else {}
    audit = global_score_audit(components, model)
    score = decimal(audit.get("adaptiveScore"))
    threshold = int(audit.get("entryThreshold") or LEARNING_BASE_ENTRY_SCORE)
    rate = decimal(item.get("dailyRate"))
    if rate >= 0.12 or rate <= -0.08:
        verdict, reason = "진입 불가", f"급등락 추격 위험 · 전역 학습 평가 {score:.1f}점"
    elif score >= threshold:
        verdict, reason = "정밀 분석", f"전역 학습 {threshold}점 통과 · 기본 {audit['baseScore']:.1f} → {score:.1f}점"
    elif score >= max(60, threshold - 20):
        verdict, reason = "관찰", f"전역 기준 {threshold}점 미달 · 기본 {audit['baseScore']:.1f} → {score:.1f}점"
    else:
        verdict, reason = "진입 보류", f"전역 전략 기준 미달 · 기본 {audit['baseScore']:.1f} → {score:.1f}점"
    item.update(
        {
            "baseScore": audit.get("baseScore"),
            "score": score,
            "scoreFeatures": audit.get("features"),
            "scoreAudit": audit,
            "verdict": verdict,
            "reason": reason,
        }
    )
    return item


def apply_global_scores(results: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    model = normalize_global_score_model(state.get("globalScoreModel"))
    for item in results:
        apply_global_score_to_candidate(item, model)
    return results


def update_global_score_model(
    model: dict[str, Any],
    entry_order: dict[str, Any],
    return_rate: float,
    violation: dict[str, Any] | None,
    trade_key: str,
) -> dict[str, Any] | None:
    features = entry_order.get("scoreFeatures")
    if not isinstance(features, dict) or not any(key in features for key in GLOBAL_SCORE_FEATURES):
        return None
    refreshed = normalize_global_score_model(model)
    before_effective = dict(refreshed.get("effectiveWeights") or {})
    before_threshold = int(refreshed.get("entryThreshold") or LEARNING_BASE_ENTRY_SCORE)
    outcome = 1.0 if return_rate > 0 else -1.0
    if violation:
        outcome = -1.0
    strength = clamp(abs(return_rate) / max(PAPER_TARGET_RATE, 0.0001), 0.5, 1.25, 0.5)
    weights = refreshed.setdefault("weights", {})
    stats_by_feature = refreshed.setdefault("featureStats", {})
    for key in GLOBAL_SCORE_FEATURES:
        if key not in features:
            continue
        value = clamp(features.get(key), 0, 1, 0.5)
        step = clamp(
            GLOBAL_SCORE_LEARNING_RATE * outcome * strength * (value - 0.5),
            -GLOBAL_SCORE_MAX_TRADE_STEP,
            GLOBAL_SCORE_MAX_TRADE_STEP,
            0,
        )
        weights[key] = clamp(
            decimal(weights.get(key) or 1.0) + step,
            GLOBAL_SCORE_WEIGHT_MIN,
            GLOBAL_SCORE_WEIGHT_MAX,
            1.0,
        )
        stats = stats_by_feature.setdefault(key, {})
        stats["count"] = int(stats.get("count") or 0) + 1
        stats["outcomeWeightedSum"] = decimal(stats.get("outcomeWeightedSum")) + (outcome * value)
        if outcome > 0:
            stats["winCount"] = int(stats.get("winCount") or 0) + 1
            stats["winnerValueSum"] = decimal(stats.get("winnerValueSum")) + value
        else:
            stats["lossCount"] = int(stats.get("lossCount") or 0) + 1
            stats["loserValueSum"] = decimal(stats.get("loserValueSum")) + value

    refreshed["sampleCount"] = int(refreshed.get("sampleCount") or 0) + 1
    if outcome > 0:
        refreshed["winCount"] = int(refreshed.get("winCount") or 0) + 1
    else:
        refreshed["lossCount"] = int(refreshed.get("lossCount") or 0) + 1
    refreshed["returnSum"] = decimal(refreshed.get("returnSum")) + return_rate
    refresh_global_score_model(refreshed)
    after_effective = refreshed.get("effectiveWeights") or {}
    changes = []
    for key, config in GLOBAL_SCORE_FEATURES.items():
        before = decimal(before_effective.get(key) or 1.0)
        after = decimal(after_effective.get(key) or 1.0)
        changes.append(
            {
                "key": key,
                "label": config.get("label"),
                "before": before,
                "after": after,
                "delta": after - before,
                "direction": "강화" if after > before else ("약화" if after < before else "유지"),
            }
        )
    changes.sort(key=lambda item: abs(decimal(item.get("delta"))), reverse=True)
    meaningful = [item for item in changes if abs(decimal(item.get("delta"))) >= 0.0005]
    main_change = meaningful[0] if meaningful else changes[0]
    symbol_name = str(entry_order.get("name") or entry_order.get("symbol") or "거래")
    result_label = "수익 재현" if outcome > 0 else ("규칙 오답" if violation else "손실 학습")
    summary = (
        f"{symbol_name} {result_label}: {main_change['label']} 기준을 {main_change['direction']} "
        f"({main_change['before']:.3f}→{main_change['after']:.3f})"
    )
    revision = {
        "id": hashlib.sha1(f"global:{trade_key}".encode("utf-8")).hexdigest()[:12],
        "tradeKey": trade_key,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "market": entry_order.get("market"),
        "symbol": entry_order.get("symbol"),
        "name": symbol_name,
        "result": result_label,
        "returnRate": return_rate,
        "summary": summary,
        "changes": changes,
        "thresholdBefore": before_threshold,
        "thresholdAfter": int(refreshed.get("entryThreshold") or LEARNING_BASE_ENTRY_SCORE),
        "sampleCount": int(refreshed.get("sampleCount") or 0),
        "scope": "GLOBAL_ALL_SYMBOLS",
    }
    refreshed["revisionCount"] = int(refreshed.get("revisionCount") or 0) + 1
    refreshed.setdefault("revisions", []).append(revision)
    refreshed["revisions"] = refreshed.get("revisions", [])[-50:]
    refreshed["lastChange"] = revision
    refreshed["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    model.clear()
    model.update(refreshed)
    return revision


def default_learning_state() -> dict[str, Any]:
    return {
        "schemaVersion": LEARNING_SCHEMA_VERSION,
        "processedTrades": [],
        "scoreModelProcessedTrades": [],
        "globalScoreModel": default_global_score_model(),
        "offlineStudy": {},
        "offlineStudyHistory": [],
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
        "scoreModelProcessedTrades": list(raw.get("scoreModelProcessedTrades") or []),
        "globalScoreModel": normalize_global_score_model(raw.get("globalScoreModel")),
        "offlineStudy": dict(raw.get("offlineStudy") or {}),
        "offlineStudyHistory": list(raw.get("offlineStudyHistory") or [])[-30:],
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
            "전체 종목 공용 점수에서 과신한 조건을 낮추는 오답 표본으로 반영했습니다."
        )
    if return_rate > 0:
        return f"{name}의 수익 조건을 일반화해 다른 종목에서도 같은 차트·거래량 조건이 재현되는지 확인합니다."
    if int(profile.get("consecutiveLosses") or 0) >= 2:
        return f"{name}의 연속 손실을 종목 제한이 아닌 전체 진입 관점의 재검증 표본으로 저장했습니다."
    return f"{name}의 손실 조건을 일반화해 다음 모든 종목에서 같은 추세·거래량 조합을 더 의심합니다."


def sync_learning_brain(
    orders: list[dict[str, Any]],
    results_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Absorb every closed PAPER trade into both the global brain and case archive."""
    results_by_symbol = results_by_symbol or {}
    ledger = paper_trade_ledger(orders, results_by_symbol)
    orders_by_id = {str(item.get("id") or ""): item for item in orders if item.get("id")}
    stop_rate = decimal(strategy_config().get("stopRate") or PAPER_STOP_RATE)
    with LEARNING_LOCK:
        state = load_learning_state_unlocked()
        processed = set(str(item) for item in state.get("processedTrades") or [])
        score_processed = set(str(item) for item in state.get("scoreModelProcessedTrades") or [])
        global_model = state.setdefault("globalScoreModel", default_global_score_model())
        changed = False
        closed_trades = sorted(
            (item for item in ledger if item.get("status") == "CLOSED"),
            key=lambda item: str(item.get("closedAt") or ""),
        )
        for trade in closed_trades:
            entry_id = str(trade.get("entryOrderId") or "")
            exit_id = str(trade.get("exitOrderId") or "")
            trade_key = f"{entry_id}:{exit_id}"
            if not entry_id or (trade_key in processed and trade_key in score_processed):
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
            if trade_key not in score_processed:
                update_global_score_model(global_model, entry_order, return_rate, violation, trade_key)
                state.setdefault("scoreModelProcessedTrades", []).append(trade_key)
                score_processed.add(trade_key)
                changed = True
            if trade_key in processed:
                continue
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
                "appliedRule": "전체 종목 공용 점수 모델의 근거 사례로 반영",
                "scope": "CASE_ARCHIVE",
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
        state["globalScoreModel"] = normalize_global_score_model(global_model)
        if changed:
            state["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            save_learning_state_unlocked(state)
        return json.loads(json.dumps(state, ensure_ascii=False))


def learning_entry_policy(symbol: str, score: Any, state: dict[str, Any]) -> dict[str, Any]:
    model = normalize_global_score_model(state.get("globalScoreModel"))
    required_score = int(model.get("entryThreshold") or LEARNING_BASE_ENTRY_SCORE)
    allocation_scale = 1.0
    cooldown_remaining = 0
    candidate_score = decimal(score)
    allowed = candidate_score >= required_score
    if candidate_score < required_score:
        reason = f"전역 학습 기준 · {required_score}점 필요 (현재 {candidate_score:.1f}점)"
    else:
        reason = f"전체 거래 공용 뇌 통과 · {required_score}점 기준 · 모든 종목 동일 적용"
    strongest = model.get("strongestFeature") or {}
    weakest = model.get("weakestFeature") or {}
    traits = [
        f"강화 {strongest.get('label') or '표본 수집'}",
        f"재검증 {weakest.get('label') or '표본 수집'}",
    ]
    return {
        "symbol": symbol,
        "allowed": allowed,
        "reason": reason,
        "requiredScore": required_score,
        "candidateScore": candidate_score,
        "allocationScale": allocation_scale,
        "cooldownRemainingSec": cooldown_remaining,
        "status": model.get("phase") or "초기 관찰",
        "traits": traits,
        "scope": "GLOBAL_ALL_SYMBOLS",
        "globalSampleCount": int(model.get("sampleCount") or 0),
        "appliedImmediately": True,
    }


def learning_brain_payload(state: dict[str, Any]) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    for raw in (state.get("symbols") or {}).values():
        profile = dict(raw)
        remaining = learning_time_remaining(profile.get("cooldownUntil"))
        profile["cooldownActive"] = remaining > 0
        profile["cooldownRemainingSec"] = remaining
        symbols.append(profile)
    risk_order = {"critical": 3, "caution": 2, "watch": 1, "stable": 0}
    symbols.sort(
        key=lambda item: (risk_order.get(str(item.get("riskLevel")), 0), int(item.get("tradeCount") or 0)),
        reverse=True,
    )
    memories = list(reversed(state.get("memories") or []))
    global_model = normalize_global_score_model(state.get("globalScoreModel"))
    global_view = json.loads(json.dumps(global_model, ensure_ascii=False))
    global_view["revisions"] = list(reversed(global_model.get("revisions") or []))[:40]
    offline_study = state.get("offlineStudy") if isinstance(state.get("offlineStudy"), dict) else {}
    return {
        "updatedAt": state.get("updatedAt"),
        "summary": {
            "learnedTradeCount": len(state.get("processedTrades") or []),
            "scoreSampleCount": int(global_model.get("sampleCount") or 0),
            "symbolCount": len(symbols),
            "memoryCount": len(memories),
            "activeRuleCount": int(global_model.get("revisionCount") or 0),
            "cooldownCount": 0,
            "mode": "PAPER_ONLY",
            "immediateApply": True,
            "coverage": "GLOBAL_ALL_SYMBOLS",
            "scope": "GLOBAL_ALL_SYMBOLS",
        },
        "global": global_view,
        "offlineStudy": offline_study,
        "offlineStudyHistory": list(reversed(state.get("offlineStudyHistory") or []))[:30],
        "symbols": symbols,
        "memories": memories[:40],
    }


def apply_brain_to_mistake_note(note: dict[str, Any], brain: dict[str, Any]) -> None:
    model = brain.get("global") or {}
    last_change = model.get("lastChange") or {}
    applied_rules = []
    if int(model.get("sampleCount") or 0) > 0:
        applied_rules.append(
            {
                "scope": "GLOBAL_ALL_SYMBOLS",
                "name": "전체 투자 공용 뇌",
                "rule": model.get("globalRule"),
                "requiredScore": model.get("entryThreshold"),
                "sampleCount": model.get("sampleCount"),
                "lastChange": last_change.get("summary"),
            }
        )
    note["appliedRules"] = applied_rules
    note["appliedImmediately"] = bool(applied_rules)
    if applied_rules:
        note["nextRule"] = (
            f"전체 종목 공용: {model.get('globalRule')}"
            + (f" · 최근 수정 {last_change.get('summary')}" if last_change.get("summary") else "")
        )


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
                "openedAt": str(trade.get("openedAt") or ""),
                "closedAt": str(trade.get("closedAt") or ""),
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
                "stopTriggerPrice": decimal(
                    exit_order.get("stopTriggerPrice")
                    or (entry_order.get("protectiveStopOrder") or {}).get("triggerPrice")
                ),
                "observedExitPrice": decimal(exit_order.get("observedPrice")),
                "observedExitReturnRate": decimal(exit_order.get("observedReturnRate")),
                "postExitStudy": (
                    dict(exit_order.get("postExitStudy") or {})
                    if isinstance(exit_order.get("postExitStudy"), dict)
                    else None
                ),
                "protectiveStopOrder": (
                    dict(entry_order.get("protectiveStopOrder") or {})
                    if isinstance(entry_order.get("protectiveStopOrder"), dict)
                    else None
                ),
                "entryOrderId": order_id,
                "exitOrderId": exit_order_id,
                "holdingTime": journal_holding_time(trade.get("openedAt"), trade.get("closedAt")),
                "entryScore": decimal(entry_order.get("entryScore")),
                "baseEntryScore": decimal(entry_order.get("baseEntryScore")),
                "scoreFeatures": entry_order.get("scoreFeatures") if isinstance(entry_order.get("scoreFeatures"), dict) else None,
                "scoreAudit": entry_order.get("scoreAudit") if isinstance(entry_order.get("scoreAudit"), dict) else None,
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
                    ANALYSIS["strategyRevision"] = int(config.get("revision") or 0)
                    ANALYSIS["strategyAppliedAt"] = config.get("savedAt")
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
    threading.Thread(target=position_risk_loop, daemon=True, name="position-risk-loop").start()
    threading.Thread(target=off_market_study_loop, daemon=True, name="off-market-study-loop").start()
    print(f"Orbit dashboard: http://{display_host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
