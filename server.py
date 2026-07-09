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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PAPER_PATH = ROOT / "paper_state.json"
BASE_URL = "https://openapi.tossinvest.com"
TOKEN_LOCK = threading.Lock()
TOKEN: dict[str, Any] = {"value": None, "expires_at": 0.0}
PAPER_TARGET_RATE = 0.01
PAPER_STOP_RATE = -0.005
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
}
CALENDAR_CACHE: dict[str, Any] = {"expiresAt": 0.0, "KR": {}, "US": {}}


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


def paper_summary(orders: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    today = time.strftime("%Y-%m-%d")
    today_orders = [
        item for item in orders if str(item.get("createdAt", "")).startswith(today)
    ]
    positions: dict[str, dict[str, Any]] = {}
    for order in today_orders:
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
    target_hit = average_return >= PAPER_TARGET_RATE
    stop_hit = average_return <= PAPER_STOP_RATE
    locked = target_hit or stop_hit
    lock_reason = None
    if target_hit:
        lock_reason = "일 목표 +1% 도달 · 신규 진입 잠금"
    elif stop_hit:
        lock_reason = "일 손실선 -0.5% 도달 · 신규 진입 중지"

    return {
        "targetRate": PAPER_TARGET_RATE,
        "stopRate": PAPER_STOP_RATE,
        "averageReturn": average_return,
        "todayOrderCount": len(today_orders),
        "openPositionCount": len(positions),
        "locked": locked,
        "lockReason": lock_reason,
    }


def paper_trade(
    results: list[dict[str, Any]], market: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    orders = load_paper_orders()
    summary = paper_summary(orders, results)
    if summary["locked"]:
        return orders[-20:], summary

    today = time.strftime("%Y-%m-%d")
    todays_market_orders = [
        item
        for item in orders
        if item.get("market") == market
        and str(item.get("createdAt", "")).startswith(today)
    ]
    if len(todays_market_orders) >= 3:
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
                with ANALYSIS_LOCK:
                    ANALYSIS["cycle"] += 1
                    ANALYSIS["lastRunAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    ANALYSIS["lastError"] = None
                    ANALYSIS["results"] = results
                    ANALYSIS["activeMarket"] = market or "CLOSED"
                    ANALYSIS["activeSession"] = session
                    ANALYSIS["paperOrders"] = orders
                    ANALYSIS["paperSummary"] = paper_stats
            except Exception as exc:
                with ANALYSIS_LOCK:
                    ANALYSIS["lastError"] = str(exc)
        time.sleep(30)


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
        if path not in ("/api/analysis/start", "/api/analysis/stop"):
            self.send_json({"error": "지원하지 않는 요청입니다."}, status=404)
            return
        with ANALYSIS_LOCK:
            ANALYSIS["enabled"] = path.endswith("/start")
        self.send_json(analysis_snapshot())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4173"))
    threading.Thread(target=analysis_loop, daemon=True, name="analysis-loop").start()
    print(f"Orbit dashboard: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
