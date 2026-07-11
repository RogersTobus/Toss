"""Local dashboard server and Toss Securities Open API gateway.

Secrets stay on the server in .env. The browser only receives normalized
portfolio data and never receives the OAuth access token or account number.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PAPER_PATH = ROOT / "paper_state.json"
REPORT_PATH = ROOT / "report_state.json"
STRATEGY_CONFIG_PATH = ROOT / "strategy_config.json"
BASE_URL = "https://openapi.tossinvest.com"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_LOCK = threading.Lock()
TOKEN: dict[str, Any] = {"value": None, "expires_at": 0.0}
STARTED_AT = time.time()
PAPER_TARGET_RATE = 0.01
PAPER_STOP_RATE = -0.005
PAPER_MAX_DAILY_ORDERS = 3
PAPER_MAX_OPEN_POSITIONS = 3
PAPER_MAX_CONSECUTIVE_LOSSES = 2
DEFAULT_STRATEGY_CONFIG = {
    "targetRate": PAPER_TARGET_RATE,
    "stopRate": PAPER_STOP_RATE,
    "maxDailyOrders": PAPER_MAX_DAILY_ORDERS,
    "maxOpenPositions": PAPER_MAX_OPEN_POSITIONS,
    "maxConsecutiveLosses": PAPER_MAX_CONSECUTIVE_LOSSES,
}
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



def clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def strategy_config() -> dict[str, Any]:
    config = dict(DEFAULT_STRATEGY_CONFIG)
    if STRATEGY_CONFIG_PATH.exists():
        try:
            stored = json.loads(STRATEGY_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                config.update(stored)
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "targetRate": clamp(config.get("targetRate"), 0.001, 0.05, PAPER_TARGET_RATE),
        "stopRate": clamp(config.get("stopRate"), -0.05, -0.001, PAPER_STOP_RATE),
        "maxDailyOrders": int(clamp(config.get("maxDailyOrders"), 1, 20, PAPER_MAX_DAILY_ORDERS)),
        "maxOpenPositions": int(clamp(config.get("maxOpenPositions"), 1, 20, PAPER_MAX_OPEN_POSITIONS)),
        "maxConsecutiveLosses": int(clamp(config.get("maxConsecutiveLosses"), 1, 10, PAPER_MAX_CONSECUTIVE_LOSSES)),
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
    STRATEGY_CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current
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


def load_report_state() -> dict[str, Any]:
    if not REPORT_PATH.exists():
        return {"sentKeys": [], "reports": [], "lastActiveMarket": None}
    try:
        data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        return {
            "sentKeys": data.get("sentKeys") or [],
            "reports": data.get("reports") or [],
            "lastActiveMarket": data.get("lastActiveMarket"),
        }
    except (json.JSONDecodeError, OSError):
        return {"sentKeys": [], "reports": [], "lastActiveMarket": None}


def save_report_state(state: dict[str, Any]) -> None:
    state["sentKeys"] = (state.get("sentKeys") or [])[-80:]
    state["reports"] = (state.get("reports") or [])[-30:]
    REPORT_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def money(value: Any) -> str:
    amount = round(decimal(value))
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{abs(amount):,}원"


def percent(value: Any) -> str:
    rate = decimal(value) * 100
    sign = "+" if rate >= 0 else "-"
    return f"{sign}{abs(rate):.2f}%"


def build_market_close_report(market: str, orders: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now().astimezone()
    date_key = now.strftime("%Y-%m-%d")
    market_name = "한국장" if market == "KR" else "미국장" if market == "US" else market
    period = summary.get("periodReturns") or {}
    today = period.get("today") or {}
    week = period.get("week") or {}
    month = period.get("month") or {}
    today_orders = [item for item in orders if str(item.get("createdAt", "")).startswith(date_key) and item.get("market") == market]
    top_names = [str(item.get("name") or item.get("symbol") or "-") for item in today_orders[-5:]]
    lines = [
        "[Orbit 단타 장마감 리포트]",
        f"시장: {market_name}",
        f"일시: {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"오늘 단타 수익: {money(today.get('profitKrw'))} ({percent(today.get('returnRate'))})",
        f"이번주 단타 수익: {money(week.get('profitKrw'))} ({percent(week.get('returnRate'))})",
        f"이번달 단타 수익: {money(month.get('profitKrw'))} ({percent(month.get('returnRate'))})",
        "",
        f"오늘 모의 진입: {len(today_orders)}건",
        f"보유 포지션: {summary.get('openPositionCount', 0)}개",
    ]
    if top_names:
        lines.append("대표 종목: " + ", ".join(top_names[:3]))
    if summary.get("locked"):
        lines.append("상태: " + str(summary.get("lockReason") or "오늘 거래 잠금"))
    else:
        lines.append("상태: 장마감 리포트 생성 완료")
    return {
        "id": f"{date_key}-{market}",
        "market": market,
        "marketName": market_name,
        "createdAt": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "todayProfitKrw": today.get("profitKrw", 0),
        "todayReturnRate": today.get("returnRate", 0),
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
<html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>{title}</title>
<style>body{{font-family:Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f7f5ee;color:#171914;display:grid;place-items:center;min-height:100vh;margin:0}}main{{width:min(520px,92vw);background:#fff;border:1px solid #e6e0d0;border-radius:18px;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.08)}}h1{{font-size:22px;margin:0 0 12px;color:{color}}}p{{line-height:1.6}}button{{border:0;border-radius:10px;background:#171914;color:#fff;padding:12px 16px;font-weight:800;cursor:pointer}}</style></head>
<body><main><h1>{title}</h1><p>{body}</p><button onclick=\"location.href='/'\">Orbit으로 돌아가기</button></main></body></html>"""
    return html.encode("utf-8")

def kakao_enabled(env: dict[str, str]) -> bool:
    return str(env.get("KAKAO_REPORT_ENABLED", "")).lower() in ("1", "true", "yes", "on")


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
    status = {
        "enabled": kakao_enabled(env),
        "lastSentAt": None,
        "lastError": None,
    }
    if previous_market in ("KR", "US") and previous_market != current_market:
        report = build_market_close_report(previous_market, orders, summary)
        sent_keys = set(state.get("sentKeys") or [])
        if report["id"] not in sent_keys:
            try:
                if status["enabled"]:
                    send_kakao_memo(env, report["message"])
                    report["sent"] = True
                    status["lastSentAt"] = report["createdAt"]
                else:
                    status["lastError"] = "KAKAO_REPORT_ENABLED가 꺼져 있어 리포트만 저장했습니다."
            except TossApiError as exc:
                report["sent"] = False
                report["error"] = exc.message
                status["lastError"] = exc.message
            reports.append(report)
            sent_keys.add(report["id"])
            state["sentKeys"] = list(sent_keys)
    state["lastActiveMarket"] = current_market
    state["reports"] = reports[-30:]
    save_report_state(state)
    return state["reports"], status


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


def load_paper_orders() -> list[dict[str, Any]]:
    if not PAPER_PATH.exists():
        return []
    try:
        return (json.loads(PAPER_PATH.read_text(encoding="utf-8")).get("orders") or [])[-20:]
    except (OSError, json.JSONDecodeError):
        return []


def save_paper_orders(orders: list[dict[str, Any]]) -> None:
    PAPER_PATH.write_text(
        json.dumps({"orders": orders[-20:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def start_of_week(now: datetime) -> datetime:
    start_day = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    return start_day - timedelta(days=now.weekday())


def period_profit_summary(
    positions: dict[str, dict[str, Any]], results_by_symbol: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    now = datetime.now().astimezone()
    starts = {
        "month": datetime(now.year, now.month, 1, tzinfo=now.tzinfo),
        "week": start_of_week(now),
        "today": datetime(now.year, now.month, now.day, tzinfo=now.tzinfo),
    }
    summary = {
        key: {"profitKrw": 0, "investedKrw": 0, "returnRate": 0.0, "positionCount": 0}
        for key in starts
    }

    for symbol, order in positions.items():
        created_at = parse_order_time(order.get("createdAt"))
        if created_at is None:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=now.tzinfo)
        current = results_by_symbol.get(symbol)
        entry = decimal(order.get("price"))
        last = decimal((current or {}).get("lastPrice"))
        quantity = decimal(order.get("quantity") or 1)
        if not entry or not last:
            continue
        invested = entry * quantity
        profit = (last - entry) * quantity
        for key, start in starts.items():
            if created_at >= start:
                summary[key]["profitKrw"] += round(profit)
                summary[key]["investedKrw"] += round(invested)
                summary[key]["positionCount"] += 1

    for item in summary.values():
        invested = item["investedKrw"]
        item["returnRate"] = item["profitKrw"] / invested if invested else 0.0
    return summary



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

    if locked and average_return >= target_rate:
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
    rules = [
        {
            "key": "dailyLoss",
            "label": "일 손실 한도",
            "status": "잠금" if average_return <= stop_rate else "정상",
            "tone": "danger" if average_return <= stop_rate else "safe",
            "detail": f"현재 {percent(average_return)} / 기준 {percent(stop_rate)}",
        },
        {
            "key": "dailyOrders",
            "label": "일 진입 횟수",
            "status": "상한" if today_order_count >= max_daily_orders else "여유",
            "tone": "danger" if today_order_count >= max_daily_orders else "safe",
            "detail": f"{today_order_count}/{max_daily_orders}건 사용",
        },
        {
            "key": "positionCap",
            "label": "포지션 수",
            "status": "과밀" if open_positions >= max_open_positions else "정상",
            "tone": "danger" if open_positions >= max_open_positions else "safe",
            "detail": f"{open_positions}/{max_open_positions}개 보유",
        },
        {
            "key": "lossStreak",
            "label": "연속 손실",
            "status": "정지" if consecutive_losses >= max_losses else "정상",
            "tone": "danger" if consecutive_losses >= max_losses else "safe",
            "detail": f"최근 손실 {consecutive_losses}회 / 기준 {max_losses}회",
        },
        {
            "key": "paperMode",
            "label": "실주문 보호",
            "status": "PAPER",
            "tone": "safe",
            "detail": "실제 주문 전송 없음",
        },
    ]
    if locked:
        rules.insert(0, {"key": "lock", "label": "오늘 거래 잠금", "status": "ON", "tone": "danger", "detail": lock_reason or "운용 잠금"})
    return rules


def safety_gate(summary: dict[str, Any]) -> dict[str, Any]:
    rules = summary.get("safetyRules") or []
    blockers = [rule for rule in rules if rule.get("tone") == "danger" and rule.get("key") in {"lock", "dailyLoss", "dailyOrders", "positionCap", "lossStreak"}]
    return {
        "blocked": bool(blockers),
        "reason": str(blockers[0].get("detail") or blockers[0].get("label")) if blockers else "신규 진입 가능",
        "blockers": blockers,
    }


def paper_summary(orders: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    config = strategy_config()
    target_rate = decimal(config.get("targetRate"))
    stop_rate = decimal(config.get("stopRate"))
    today = time.strftime("%Y-%m-%d")
    today_orders = [
        item for item in orders if str(item.get("createdAt", "")).startswith(today)
    ]
    positions: dict[str, dict[str, Any]] = {}
    for order in orders:
        symbol = str(order.get("symbol") or "")
        if not symbol:
            continue
        if order.get("side") == "BUY":
            positions[symbol] = order
        elif order.get("side") == "SELL":
            positions.pop(symbol, None)

    results_by_symbol = {str(item.get("symbol")): item for item in results}
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
    locked = target_hit or stop_hit
    lock_reason = None
    if target_hit:
        lock_reason = f"일 목표 {percent(target_rate)} 도달 · 신규 진입 잠금"
    elif stop_hit:
        lock_reason = f"손실폭 {percent(stop_rate)} 도달 · 신규 진입 중지"

    return {
        "targetRate": target_rate,
        "stopRate": stop_rate,
        "strategyConfig": config,
        "averageReturn": average_return,
        "periodReturns": period_profit_summary(positions, results_by_symbol),
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

def paper_trade(
    results: list[dict[str, Any]], market: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    orders = load_paper_orders()
    summary = paper_summary(orders, results)
    gate = safety_gate(summary)
    if summary["locked"] or gate["blocked"]:
        return orders[-20:], summary

    today = time.strftime("%Y-%m-%d")
    todays_market_orders = [
        item
        for item in orders
        if item.get("market") == market
        and str(item.get("createdAt", "")).startswith(today)
    ]
    config = strategy_config()
    if len(todays_market_orders) >= int(config.get("maxDailyOrders") or PAPER_MAX_DAILY_ORDERS):
        return orders[-20:], summary
    existing = {(item.get("market"), item.get("symbol")) for item in orders[-10:]}
    candidate = next(
        (
            item
            for item in results
            if item.get("verdict") == "정밀 분석"
            and (market, item.get("symbol")) not in existing
        ),
        None,
    )
    if candidate:
        orders.append(
            {
                "id": f"PAPER-{int(time.time())}",
                "market": market,
                "symbol": candidate.get("symbol"),
                "name": candidate.get("name"),
                "side": "BUY",
                "quantity": 1,
                "price": candidate.get("lastPrice"),
                "currency": candidate.get("currency"),
                "status": "FILLED",
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "reason": candidate.get("reason"),
            }
        )
        save_paper_orders(orders)
        summary = paper_summary(orders, results)
    return orders[-20:], summary


def scan_market(env: dict[str, str], market: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "type": "MARKET_TRADING_AMOUNT",
            "marketCountry": market,
            "duration": "realtime",
            "excludeInvestmentCaution": "true",
            "count": "30",
        }
    )
    ranked = toss_get(f"/api/v1/rankings?{query}", env).get("result") or {}
    rows = ranked.get("rankings") or []
    symbols = [str(row.get("symbol")) for row in rows if row.get("symbol")]
    stocks = toss_get(
        f"/api/v1/stocks?{urllib.parse.urlencode({'symbols': ','.join(symbols)})}", env
    ).get("result") or []
    names = {str(stock.get("symbol")): stock.get("name") for stock in stocks}
    results = []
    for row in rows:
        price = row.get("price") or {}
        rate = decimal(price.get("changeRate"))
        if rate >= 0.12 or rate <= -0.08:
            verdict, reason = "진입 불가", "급등락 추격 위험"
        elif 0.02 <= rate < 0.12:
            verdict, reason = "정밀 분석", "거래대금·상승 추세"
        elif -0.03 < rate < 0.02:
            verdict, reason = "관찰", "방향성 확인 필요"
        else:
            verdict, reason = "진입 보류", "하락 추세"
        results.append(
            {
                "rank": row.get("rank"),
                "symbol": row.get("symbol"),
                "name": names.get(str(row.get("symbol"))) or row.get("symbol"),
                "currency": row.get("currency"),
                "lastPrice": price.get("lastPrice"),
                "dailyRate": rate,
                "tradingAmount": row.get("tradingAmount"),
                "verdict": verdict,
                "reason": reason,
            }
        )
    return results


def analysis_loop() -> None:
    while True:
        with ANALYSIS_LOCK:
            enabled = bool(ANALYSIS["enabled"])
        if enabled:
            try:
                env = load_env()
                market, session = market_schedule(env)
                results = scan_market(env, market) if market else []
                if market:
                    orders, paper_stats = paper_trade(results, market)
                else:
                    orders = load_paper_orders()
                    paper_stats = paper_summary(orders, results)
                report_state = load_report_state()
                previous_market = report_state.get("lastActiveMarket")
                current_market = market if market in ("KR", "US") else None
                reports, report_status = handle_market_close_report(
                    previous_market, current_market, env, orders, paper_stats
                )
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
                with ANALYSIS_LOCK:
                    ANALYSIS["lastError"] = str(exc)
        time.sleep(30)



def health_status() -> dict[str, Any]:
    env = load_env()
    with ANALYSIS_LOCK:
        analysis = dict(ANALYSIS)
    uptime = max(0, int(time.time() - STARTED_AT))
    return {
        "ok": True,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
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
        "analysis": {
            "enabled": bool(analysis.get("enabled")),
            "cycle": analysis.get("cycle", 0),
            "lastRunAt": analysis.get("lastRunAt"),
            "activeMarket": analysis.get("activeMarket"),
            "activeSession": analysis.get("activeSession"),
            "lastError": analysis.get("lastError"),
        },
    }

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
            self.send_json({"config": strategy_config()})
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
        if path == "/api/strategy/config":
            try:
                config = save_strategy_config(self.read_json_body())
                orders = load_paper_orders()
                with ANALYSIS_LOCK:
                    results = list(ANALYSIS.get("results") or [])
                    ANALYSIS["paperSummary"] = paper_summary(orders, results)
                self.send_json({"config": config, "paperSummary": analysis_snapshot().get("paperSummary")})
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












