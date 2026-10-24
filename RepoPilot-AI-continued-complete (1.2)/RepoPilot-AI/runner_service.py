"""Private execution-runner HTTP service. Deploy only as a restricted container.

Only Python pytest suites are accepted. The service never installs project dependencies,
never follows symlinks (files arrive as text), and runs in a disposable directory.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import sys
import time
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

MAX_FILES = int(os.environ.get("RUNNER_MAX_FILES", "80"))
MAX_TOTAL_BYTES = int(os.environ.get("RUNNER_MAX_TOTAL_BYTES", str(3 * 1024 * 1024)))
MAX_FILE_BYTES = int(os.environ.get("RUNNER_MAX_FILE_BYTES", str(256 * 1024)))
MAX_RUNTIME_SECONDS = int(os.environ.get("RUNNER_TIMEOUT_SECONDS", "30"))
MAX_LOG_BYTES = int(os.environ.get("RUNNER_MAX_LOG_BYTES", "12000"))
RUN_SLOTS = threading.BoundedSemaphore(2)


def _response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
            or name.startswith("/") or any(part in {"", ".git", ".venv", "node_modules"} for part in path.parts)):
        raise ValueError("Unsafe file path")
    return path


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Kill the test process and any children it spawned, on POSIX and Windows alike."""
    if os.name == "nt":
        # Windows has no killpg; taskkill /T walks the spawned process tree instead.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True, check=False,
        )
        try:
            process.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except OSError:
            pass


def execute(payload: dict) -> dict:
    files = payload.get("source_files")
    revision = payload.get("revision")
    if not isinstance(files, dict) or not isinstance(revision, str):
        raise ValueError("Expected source_files and revision")
    if not 1 <= len(files) <= MAX_FILES:
        raise ValueError("File count is outside the allowed limit")

    normalized: list[tuple[PurePosixPath, str]] = []
    total = 0
    for name, content in files.items():
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValueError("File names and contents must be strings")
        path = _safe_path(name)
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds size limit: {name}")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Total source size exceeds limit")
        normalized.append((path, content))

    test_paths = [p for p, _ in normalized if (
        any(part.lower() in {"test", "tests"} for part in p.parts)
        and p.suffix == ".py"
    ) or (p.name.startswith("test_") and p.suffix == ".py") or (p.name.endswith("_test.py"))]
    if not test_paths:
        return {
            "status": "INCONCLUSIVE", "command": "python -m pytest -q",
            "tested_commit": revision, "exit_code": None, "duration_seconds": 0,
            "logs": "", "details": "No Python test files were included in the indexed repository snapshot; no tests were run."
        }

    # Keep a suite root per tests/ directory so imports such as `from src...` resolve
    # the same way they do when developers run pytest from the subproject directory.
    suite_roots: dict[str, list[PurePosixPath]] = {}
    for test_path in test_paths:
        if test_path.parent.name.lower() in {"tests", "test"}:
            suite_root = test_path.parent.parent
        else:
            suite_root = PurePosixPath(".")
        suite_roots.setdefault(suite_root.as_posix(), []).append(test_path)

    with tempfile.TemporaryDirectory(prefix="repopilot-run-") as directory:
        root = Path(directory).resolve()
        for path, content in normalized:
            destination = root.joinpath(*path.parts).resolve()
            if root not in destination.parents:
                raise ValueError("File path escaped the temporary workspace")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        env = {
            "PATH": os.pathsep.join([os.path.dirname(sys.executable), "/usr/local/bin", "/usr/bin", "/bin"]),
            "HOME": "/tmp",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        start = time.monotonic()
        logs: list[str] = []
        exit_codes: list[int] = []
        for suite_name, suite_tests in suite_roots.items():
            suite_root = (root / suite_name).resolve()
            if root != suite_root and root not in suite_root.parents:
                raise ValueError("Test suite root escaped the temporary workspace")
            relative_tests = [p.relative_to(PurePosixPath(suite_name)).as_posix() if suite_name != "." else p.as_posix() for p in suite_tests]
            env["PYTHONPATH"] = str(suite_root)
            remaining = MAX_RUNTIME_SECONDS - (time.monotonic() - start)
            if remaining <= 0:
                return {
                    "status": "TIMED_OUT", "command": "python -m pytest -q",
                    "tested_commit": revision, "exit_code": None,
                    "duration_seconds": round(time.monotonic() - start, 3),
                    "logs": "\n".join(logs)[-MAX_LOG_BYTES:],
                    "details": f"Test execution exceeded the {MAX_RUNTIME_SECONDS}-second time limit."
                }
            process = subprocess.Popen(
                [sys.executable, "-m", "pytest", "-q", *relative_tests],
                cwd=suite_root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                stdout, _ = process.communicate(timeout=remaining)
                exit_codes.append(process.returncode)
                logs.append(f"$ cd {suite_name} && python -m pytest -q\n" + stdout.decode("utf-8", errors="replace"))
            except subprocess.TimeoutExpired:
                # Kill the whole process tree so child processes cannot outlive the timeout.
                _terminate_process_tree(process)
                stdout = b""
                try:
                    stdout, _ = process.communicate(timeout=10)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
                logs.append(stdout.decode("utf-8", errors="replace")[-MAX_LOG_BYTES:])
                return {
                    "status": "TIMED_OUT", "command": "python -m pytest -q",
                    "tested_commit": revision, "exit_code": None,
                    "duration_seconds": round(time.monotonic() - start, 3),
                    "logs": "\n".join(logs)[-MAX_LOG_BYTES:],
                    "details": f"Test execution exceeded the {MAX_RUNTIME_SECONDS}-second time limit."
                }

        duration = round(time.monotonic() - start, 3)
        if all(code == 0 for code in exit_codes):
            status = "PASSED"
        elif all(code == 5 for code in exit_codes):
            status = "INCONCLUSIVE"
        else:
            status = "FAILED"
        return {
            "status": status, "command": "python -m pytest -q",
            "tested_commit": revision, "exit_code": 0 if status == "PASSED" else (exit_codes[-1] if exit_codes else None),
            "duration_seconds": duration, "logs": "\n".join(logs)[-MAX_LOG_BYTES:],
            "details": "Tests completed in the isolated runner." if status == "PASSED" else "Tests did not all pass or could not be collected; inspect bounded runner logs."
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "RepoPilotRunner"
    sys_version = ""

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path != "/health":
            _response(self, 404, {"detail": "Not found"})
            return
        _response(self, 200, {"status": "ok", "runner": "isolated-python-pytest", "network": "deployment-configured"})

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/run":
            _response(self, 404, {"detail": "Not found"})
            return
        if not RUN_SLOTS.acquire(blocking=False):
            _response(self, 429, {"detail": "Runner is at capacity; retry later."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_TOTAL_BYTES + 128 * 1024:
                _response(self, 413, {"detail": "Request size outside allowed limit"})
                return
            payload = json.loads(self.rfile.read(length))
            result = execute(payload)
            _response(self, 200, result)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            _response(self, 400, {"detail": str(exc)[:300]})
        except Exception:
            _response(self, 500, {"detail": "Runner failed unexpectedly"})
        finally:
            RUN_SLOTS.release()

    def log_message(self, fmt, *args):
        # Do not log request bodies, issue text, or source snippets.
        print("runner-request", self.client_address[0], self.command, self.path, flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("RUNNER_PORT", "8090"))), Handler).serve_forever()
