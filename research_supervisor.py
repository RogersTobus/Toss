"""Small supervisor that records why the bounded research child stopped.

The supervisor deliberately does not import ``server``.  That keeps its own
memory footprint tiny enough to survive when systemd has to kill the larger
research child, so the dashboard still gets a useful exit signal and observed
memory peak.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "research_worker_state.json"
WORKER_PATH = ROOT / "research_worker.py"
POLL_SECONDS = 1.0
COMMON_SIGNAL_NAMES = {
    9: "SIGKILL",
    15: "SIGTERM",
}


def timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


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


def process_memory_mb(pid: int) -> float | None:
    """Return the larger of current RSS and kernel high-water RSS."""
    try:
        lines = Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    values: list[float] = []
    for line in lines:
        if line.startswith(("VmRSS:", "VmHWM:")):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    values.append(float(parts[1]) / 1024)
                except ValueError:
                    pass
    return round(max(values), 1) if values else None


def exit_description(return_code: int, peak_mb: float | None) -> str:
    peak = f" · 관측 최대 {peak_mb:.1f}MB" if peak_mb is not None else ""
    if return_code < 0:
        signal_number = -return_code
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = COMMON_SIGNAL_NAMES.get(signal_number, f"signal {signal_number}")
        return f"연구 프로세스가 {signal_name}({signal_number})로 강제 종료됐습니다{peak}."
    return f"연구 프로세스가 종료 코드 {return_code}로 끝났습니다{peak}."


def record_child_exit(
    return_code: int,
    peak_mb: float | None,
    *,
    state_path: Path | None = None,
) -> dict[str, Any]:
    global STATE_PATH
    original_path = STATE_PATH
    if state_path is not None:
        STATE_PATH = state_path
    try:
        state = read_state()
        state.update(
            {
                "supervisorPid": os.getpid(),
                "workerExitCode": return_code,
                "workerExitSignal": -return_code if return_code < 0 else None,
                "peakObservedMemoryMb": peak_mb,
            }
        )
        if state.get("status") in (None, "not_started", "running", "stale"):
            message = exit_description(return_code, peak_mb)
            errors = [str(item) for item in (state.get("errors") or [])]
            state.update(
                {
                    "status": "error",
                    "phase": "waiting",
                    "completedAt": timestamp(),
                    "heartbeatAt": timestamp(),
                    "lastError": message,
                    "errors": [*errors, message][-12:],
                    "message": "종료 원인을 기록했습니다. 다음 주기에 다시 시도합니다.",
                }
            )
        atomic_write_state(state)
        return state
    finally:
        STATE_PATH = original_path


def main() -> int:
    child = subprocess.Popen(
        [sys.executable, str(WORKER_PATH)],
        cwd=str(ROOT),
    )
    peak_mb: float | None = None

    def forward_stop(signum: int, _frame: Any) -> None:
        if child.poll() is None:
            child.send_signal(signum)

    if os.name != "nt":
        signal.signal(signal.SIGTERM, forward_stop)
        signal.signal(signal.SIGINT, forward_stop)

    while child.poll() is None:
        observed = process_memory_mb(child.pid)
        if observed is not None:
            peak_mb = max(peak_mb or 0.0, observed)
        time.sleep(POLL_SECONDS)

    return_code = int(child.returncode or 0)
    observed = process_memory_mb(child.pid)
    if observed is not None:
        peak_mb = max(peak_mb or 0.0, observed)
    record_child_exit(return_code, peak_mb)
    return 0 if return_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
