from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol


class VerificationState(StrEnum):
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"


TRANSITIONS = {
    VerificationState.NOT_RUN: {
        VerificationState.RUNNING,
        VerificationState.BLOCKED,
        VerificationState.INCONCLUSIVE,
    },
    VerificationState.BLOCKED: {VerificationState.RUNNING},
    VerificationState.INCONCLUSIVE: {VerificationState.RUNNING},
    VerificationState.FAILED: {VerificationState.RUNNING},
    VerificationState.TIMED_OUT: {VerificationState.RUNNING},
    VerificationState.RUNNING: {
        VerificationState.PASSED,
        VerificationState.FAILED,
        VerificationState.TIMED_OUT,
        VerificationState.BLOCKED,
        VerificationState.INCONCLUSIVE,
    },
}


def allowed_transition(current: VerificationState, target: VerificationState) -> bool:
    return target in TRANSITIONS.get(current, set())


class ExecutionWorker(Protocol):
    def run(self, job: dict[str, Any]) -> dict[str, Any]: ...


class DisabledExecutionWorker:
    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": VerificationState.BLOCKED.value,
            "command": None,
            "tested_commit": job.get("revision"),
            "baseline": None,
            "post_patch": None,
            "exit_code": None,
            "duration_seconds": None,
            "logs": "",
            "details": (
                "Verification is blocked: no isolated execution worker is configured. "
                "Repository code was not executed."
            ),
        }


class HttpExecutionWorker:
    """Client for the private, resource-limited runner container."""

    def __init__(self, base_url: str, timeout_seconds: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        import httpx

        source_files = job.get("source_files")
        if not isinstance(source_files, dict) or not source_files:
            return {
                "status": VerificationState.INCONCLUSIVE.value,
                "command": "python -m pytest -q", "tested_commit": job.get("revision"),
                "baseline": None, "post_patch": None, "exit_code": None,
                "duration_seconds": 0, "logs": "",
                "details": "No indexed source snapshot is available to the isolated runner.",
            }
        try:
            response = httpx.post(
                f"{self.base_url}/run",
                json={"revision": job.get("revision", "unknown"), "source_files": source_files},
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            result = response.json()
            terminal = {
                VerificationState.PASSED.value, VerificationState.FAILED.value,
                VerificationState.TIMED_OUT.value, VerificationState.BLOCKED.value,
                VerificationState.INCONCLUSIVE.value,
            }
            if not isinstance(result, dict) or result.get("status") not in terminal:
                raise ValueError("Runner returned an invalid response schema")
            return {
                "status": result["status"],
                "command": result.get("command"),
                "tested_commit": result.get("tested_commit", job.get("revision")),
                "baseline": None,
                "post_patch": None,
                "exit_code": result.get("exit_code"),
                "duration_seconds": result.get("duration_seconds"),
                "logs": str(result.get("logs", ""))[:12000],
                "details": str(result.get("details", "Runner completed."))[:1000],
            }
        except httpx.TimeoutException:
            return {
                "status": VerificationState.TIMED_OUT.value,
                "command": "python -m pytest -q", "tested_commit": job.get("revision"),
                "baseline": None, "post_patch": None, "exit_code": None,
                "duration_seconds": self.timeout_seconds, "logs": "",
                "details": "Timed out waiting for the isolated runner; the remote process may have stopped or may still be finishing.",
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {
                "status": VerificationState.BLOCKED.value,
                "command": None, "tested_commit": job.get("revision"),
                "baseline": None, "post_patch": None, "exit_code": None,
                "duration_seconds": None, "logs": "",
                "details": "Isolated runner is unavailable or returned an invalid response; no successful test result was recorded.",
            }
