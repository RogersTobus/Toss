"""Bounded, low-priority research worker for the small Lightsail instance.

The dashboard process never imports or starts this worker. systemd runs one
short-lived process at a time, and each process performs the two research
passes sequentially so their memory peaks cannot overlap.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import server


ROOT = Path(__file__).resolve().parent
STATE_PATH = server.RESEARCH_WORKER_STATE_PATH
PROCESS_LOCK_PATH = ROOT / ".research_worker.lock"
HEARTBEAT_SECONDS = 15
SCHEDULE_SECONDS = 600


def timestamp() -> str:
    return server.now_kst().strftime("%Y-%m-%dT%H:%M:%S%z")


def read_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_state(payload: dict[str, Any]) -> None:
    temporary = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(STATE_PATH)


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


@contextmanager
def exclusive_worker_lock():
    owner = {"pid": os.getpid(), "startedAt": timestamp()}
    acquired = False
    for _ in range(2):
        try:
            descriptor = os.open(
                PROCESS_LOCK_PATH,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(owner, handle, ensure_ascii=False)
            acquired = True
            break
        except FileExistsError:
            try:
                existing = json.loads(PROCESS_LOCK_PATH.read_text(encoding="utf-8"))
                existing_pid = int(existing.get("pid") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                existing_pid = 0
            if process_is_running(existing_pid):
                break
            try:
                PROCESS_LOCK_PATH.unlink()
            except OSError:
                break
    try:
        yield acquired
    finally:
        if not acquired:
            return
        try:
            current = json.loads(PROCESS_LOCK_PATH.read_text(encoding="utf-8"))
            if int(current.get("pid") or 0) == os.getpid():
                PROCESS_LOCK_PATH.unlink()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass


class WorkerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.payload = {
            **read_state(),
            "pid": os.getpid(),
            "separateProcess": True,
            "memoryHighMb": 384,
            "memoryLimitMb": 600,
            "scheduleSeconds": SCHEDULE_SECONDS,
        }
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def update(self, **values: Any) -> dict[str, Any]:
        with self.lock:
            self.payload.update(values)
            self.payload["heartbeatAt"] = timestamp()
            atomic_write_state(self.payload)
            return dict(self.payload)

    def start_heartbeat(self) -> None:
        def beat() -> None:
            while not self.stop_event.wait(HEARTBEAT_SECONDS):
                self.update()

        self.thread = threading.Thread(target=beat, daemon=True, name="research-heartbeat")
        self.thread.start()

    def stop_heartbeat(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)


@contextmanager
def research_storage():
    """Point research functions at their small sidecar state for this process."""
    original_path = server.LEARNING_PATH
    original_lock_path = server.LEARNING_FILE_LOCK_PATH
    server.LEARNING_PATH = server.RESEARCH_LEARNING_PATH
    server.LEARNING_FILE_LOCK_PATH = server.RESEARCH_LEARNING_FILE_LOCK_PATH
    try:
        yield
    finally:
        server.LEARNING_PATH = original_path
        server.LEARNING_FILE_LOCK_PATH = original_lock_path


def select_research_markets(sessions: list[tuple[str, str]]) -> tuple[str, ...]:
    """Keep heavy replay away from regular trading and use US day for KR review."""
    if server.regular_market_is_active(sessions):
        return ()
    if any(session == "US 데이마켓" for _, session in sessions):
        return ("KR",)
    return ("KR", "US")


def peak_memory_mb() -> float | None:
    try:
        import resource

        usage = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return round(usage / 1024, 1)
    except (ImportError, OSError, ValueError):
        return None


def _run_research_cycle() -> dict[str, Any]:
    state = WorkerState()
    started = server.now_kst()
    run_count = int(state.payload.get("runCount") or 0) + 1
    state.update(
        status="running",
        phase="market_check",
        startedAt=timestamp(),
        completedAt=None,
        runCount=run_count,
        lastError=None,
        errors=[],
        analyzedSymbolCount=0,
        intradayAnalyzedSymbolCount=0,
    )
    state.start_heartbeat()
    errors: list[str] = []
    analyzed_count = 0
    intraday_count = 0
    multi_result: dict[str, Any] = {}
    intraday_result: dict[str, Any] = {}
    try:
        env = server.load_env()
        sessions = server.active_market_sessions(env)
        markets = select_research_markets(sessions)
        state.update(
            activeSessions=[{"market": market, "session": session} for market, session in sessions],
            markets=list(markets),
        )
        if not markets:
            next_run = server.now_kst() + timedelta(seconds=SCHEDULE_SECONDS)
            return state.update(
                status="skipped",
                phase="regular_market",
                completedAt=timestamp(),
                nextRunAt=next_run.strftime("%Y-%m-%dT%H:%M:%S%z"),
                durationSeconds=round((server.now_kst() - started).total_seconds(), 1),
                message="정규장 보호를 위해 연구를 건너뛰었습니다.",
            )

        state.update(phase="intraday_replay", message="분봉 전략을 순차 검증하고 있습니다.")
        try:
            intraday_result = server.run_intraday_backtest_cycle(env, markets)
            intraday_count = len(intraday_result.get("analyzed") or [])
            errors.extend(str(item) for item in (intraday_result.get("errors") or []))
            if intraday_result.get("status") == "error" and not intraday_result.get("errors"):
                errors.append("분봉 검증: 분석 가능한 종목 결과가 없습니다.")
        except Exception as exc:
            errors.append(f"분봉 검증: {str(exc)[:300]}")
        state.update(
            intradayAnalyzedSymbolCount=intraday_count,
            lastIntradayResult={
                "status": intraday_result.get("status"),
                "tradeCount": int(intraday_result.get("tradeCount") or 0),
                "analyzedSymbolCount": intraday_count,
                "completedAt": intraday_result.get("completedAt"),
            },
        )

        state.update(phase="multi_timeframe", message="일·주·월봉을 한 종목씩 묶어 분석하고 있습니다.")
        try:
            multi_result = server.run_off_market_study(env, markets)
            analyzed_count = int(multi_result.get("analyzedSymbolCount") or 0)
            errors.extend(str(item) for item in (multi_result.get("errors") or []))
            if multi_result.get("status") == "error" and not multi_result.get("errors"):
                errors.append("일·주·월봉: 분석 가능한 종목 결과가 없습니다.")
        except Exception as exc:
            errors.append(f"일·주·월봉: {str(exc)[:300]}")

        previous_total = int(state.payload.get("totalAnalyzedSymbolCount") or 0)
        previous_intraday_total = int(state.payload.get("totalIntradayAnalyzedSymbolCount") or 0)
        next_run = server.now_kst() + timedelta(seconds=SCHEDULE_SECONDS)
        status = "completed" if not errors else ("partial" if analyzed_count or intraday_count else "error")
        return state.update(
            status=status,
            phase="waiting",
            completedAt=timestamp(),
            nextRunAt=next_run.strftime("%Y-%m-%dT%H:%M:%S%z"),
            durationSeconds=round((server.now_kst() - started).total_seconds(), 1),
            analyzedSymbolCount=analyzed_count,
            intradayAnalyzedSymbolCount=intraday_count,
            totalAnalyzedSymbolCount=previous_total + analyzed_count,
            totalIntradayAnalyzedSymbolCount=previous_intraday_total + intraday_count,
            successfulRunCount=int(state.payload.get("successfulRunCount") or 0)
            + (1 if status in ("completed", "partial") else 0),
            errors=errors[-12:],
            lastError=errors[-1] if errors else None,
            peakMemoryMb=peak_memory_mb(),
            message="연구 결과를 저장하고 다음 실행을 기다립니다.",
            lastMultiTimeframeResult={
                "status": multi_result.get("status"),
                "analyzedSymbolCount": analyzed_count,
                "patternObservationCount": int(
                    (multi_result.get("summary") or {}).get("patternObservationCount") or 0
                ),
                "completedAt": multi_result.get("completedAt"),
            },
        )
    except Exception as exc:
        next_run = server.now_kst() + timedelta(seconds=SCHEDULE_SECONDS)
        return state.update(
            status="error",
            phase="waiting",
            completedAt=timestamp(),
            nextRunAt=next_run.strftime("%Y-%m-%dT%H:%M:%S%z"),
            durationSeconds=round((server.now_kst() - started).total_seconds(), 1),
            lastError=str(exc)[:500],
            errors=[*errors, str(exc)[:500]][-12:],
            peakMemoryMb=peak_memory_mb(),
            message="연구 오류를 기록했으며 다음 주기에 다시 시도합니다.",
        )
    finally:
        state.stop_heartbeat()


def run_research_cycle() -> dict[str, Any]:
    with research_storage():
        return _run_research_cycle()


def main() -> int:
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    with exclusive_worker_lock() as acquired:
        if not acquired:
            print("Research worker skipped: another process is active.")
            return 0
        result = run_research_cycle()
        print(
            "Research worker "
            f"{result.get('status')} · run {result.get('runCount')} · "
            f"multi {result.get('analyzedSymbolCount', 0)} · "
            f"intraday {result.get('intradayAnalyzedSymbolCount', 0)}"
        )
        return 0 if result.get("status") in ("completed", "partial", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
