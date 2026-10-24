import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "runner_service.py"
spec = importlib.util.spec_from_file_location("runner_service", MODULE_PATH)
runner_service = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(runner_service)


def test_runner_executes_included_python_tests_and_reports_commit():
    result = runner_service.execute({
        "revision": "abc123",
        "source_files": {
            "src/calc.py": "def add(a, b):\n    return a + b\n",
            "tests/test_calc.py": "from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
    })
    assert result["status"] == "PASSED"
    assert result["tested_commit"] == "abc123"
    assert result["exit_code"] == 0
    assert "1 passed" in result["logs"]


def test_runner_reports_failed_test_without_claiming_success():
    result = runner_service.execute({
        "revision": "def456",
        "source_files": {"tests/test_failure.py": "def test_failure():\n    assert False\n"},
    })
    assert result["status"] == "FAILED"
    assert result["exit_code"] != 0


def test_runner_is_inconclusive_when_snapshot_has_no_tests():
    result = runner_service.execute({"revision": "r1", "source_files": {"src/app.py": "VALUE = 1\n"}})
    assert result["status"] == "INCONCLUSIVE"
    assert result["exit_code"] is None


@pytest.mark.parametrize("path", ["../escape.py", "/absolute.py", "C:\\evil.py", "src\\evil.py", ".git/config"])
def test_runner_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        runner_service.execute({"revision": "r1", "source_files": {path: "x=1\n"}})


def test_runner_rejects_oversized_file_count(monkeypatch):
    monkeypatch.setattr(runner_service, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="File count"):
        runner_service.execute({"revision": "r1", "source_files": {"a.py": "x=1", "b.py": "x=2"}})


def test_runner_times_out_and_reports_no_success(monkeypatch):
    monkeypatch.setattr(runner_service, "MAX_RUNTIME_SECONDS", 1)
    result = runner_service.execute({
        "revision": "timeout-revision",
        "source_files": {"tests/test_slow.py": "import time\ndef test_slow():\n    time.sleep(10)\n"},
    })
    assert result["status"] == "TIMED_OUT"
    assert result["exit_code"] is None
