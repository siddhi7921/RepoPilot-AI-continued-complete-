import httpx

from verification import HttpExecutionWorker, VerificationState


def test_http_worker_sends_snapshot_and_returns_bounded_result(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "PASSED", "command": "python -m pytest -q",
                "tested_commit": "abc", "exit_code": 0, "duration_seconds": 1.2,
                "logs": "ok", "details": "Tests completed.",
            }

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs["json"]
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    worker = HttpExecutionWorker("http://runner:8090")
    result = worker.run({"revision": "abc", "source_files": {"tests/test_a.py": "def test_a(): pass"}})
    assert seen["url"] == "http://runner:8090/run"
    assert seen["json"]["revision"] == "abc"
    assert result["status"] == VerificationState.PASSED.value
    assert result["exit_code"] == 0


def test_http_worker_fails_closed_when_runner_is_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(httpx, "post", fail)
    result = HttpExecutionWorker("http://runner:8090").run({
        "revision": "abc", "source_files": {"tests/test_a.py": "def test_a(): pass"}
    })
    assert result["status"] == VerificationState.BLOCKED.value
    assert result["exit_code"] is None
