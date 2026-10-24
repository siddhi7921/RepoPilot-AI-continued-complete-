import base64
import json
import shutil
from pathlib import Path

import httpx
import pytest

import app
import repopilot
from repopilot import (
    Hypothesis,
    LLMProvider,
    RepositoryError,
    StructuredAnalysis,
    rank_evidence,
    validate_unified_diff,
)
from verification import DisabledExecutionWorker, VerificationState, allowed_transition


def _response(data, status_code=200, headers=None):
    class Response:
        def __init__(self):
            self.status_code = status_code
            self.headers = headers or {}

        def json(self):
            return data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("error", request=None, response=self)

    return Response()


@pytest.mark.parametrize(
    ("repo_url", "expected"),
    [
        ("https://github.com/acme/platform", ("acme", "platform")),
        ("https://github.com/acme/platform.git", ("acme", "platform")),
    ],
)
def test_repo_url_validation_accepts_public_github_urls(repo_url, expected):
    assert repopilot.validate_github_repo_url(repo_url) == expected


@pytest.mark.parametrize(
    "repo_url",
    [
        "http://github.com/acme/platform",
        "https://github.com.evil.test/acme/platform",
        "https://user:password@github.com/acme/platform",
        "https://github.com/acme/platform/issues",
        "https://github.com/acme/../secret",
        "https://github.com/acme//platform",
    ],
)
def test_repo_url_validation_rejects_unsupported_or_unsafe_urls(repo_url):
    with pytest.raises(ValueError):
        repopilot.validate_github_repo_url(repo_url)


def test_github_ingestion_retrieves_real_commit_files_and_lines(monkeypatch):
    commit_sha = "a" * 40
    blob_sha = "b" * 40
    code = "def valid(now, expires_at):\n    if now > expires_at:\n        return False\n    return True\n"
    payloads = {
        f"{repopilot.GITHUB_API}/repos/acme/platform": {
            "full_name": "acme/platform",
            "default_branch": "main",
            "private": False,
        },
        f"{repopilot.GITHUB_API}/repos/acme/platform/commits/main": {"sha": commit_sha},
        f"{repopilot.GITHUB_API}/repos/acme/platform/git/trees/{commit_sha}?recursive=1": {
            "tree": [
                {"type": "blob", "path": "src/session.py", "sha": blob_sha, "size": len(code)},
                {"type": "blob", "path": "node_modules/lib/index.js", "sha": "c" * 40, "size": 10},
                {"type": "blob", "mode": "120000", "path": "src/session-link.py", "sha": "d" * 40, "size": 18},
            ]
        },
        f"{repopilot.GITHUB_API}/repos/acme/platform/git/blobs/{blob_sha}": {
            "encoding": "base64",
            "content": base64.b64encode(code.encode()).decode(),
        },
    }
    monkeypatch.setattr(repopilot.httpx, "get", lambda url, **kwargs: _response(payloads[url]))

    snapshot = repopilot.GitHubIngestor(token="").ingest("https://github.com/acme/platform")
    evidence = rank_evidence(snapshot["source_files"], "session expires token valid")

    assert snapshot["mode"] == "github"
    assert snapshot["revision"] == commit_sha
    assert snapshot["file_paths"] == ["src/session.py"]
    assert evidence[0]["path"] == "src/session.py"
    assert evidence[0]["line_start"] == 1
    assert "now > expires_at" in evidence[0]["snippet"]


def test_github_ingestion_prioritizes_package_source_over_root_configuration(monkeypatch):
    commit_sha = "a" * 40
    blob_sha = "b" * 40
    source = "def handle_request():\n    return True\n"
    payloads = {
        f"{repopilot.GITHUB_API}/repos/acme/platform": {
            "full_name": "acme/platform",
            "default_branch": "main",
            "private": False,
        },
        f"{repopilot.GITHUB_API}/repos/acme/platform/commits/main": {"sha": commit_sha},
        f"{repopilot.GITHUB_API}/repos/acme/platform/git/trees/{commit_sha}?recursive=1": {
            "tree": [
                {"type": "blob", "path": "setup.py", "sha": "c" * 40, "size": 30},
                {"type": "blob", "path": "src/app.py", "sha": blob_sha, "size": len(source)},
            ]
        },
        f"{repopilot.GITHUB_API}/repos/acme/platform/git/blobs/{blob_sha}": {
            "encoding": "base64",
            "content": base64.b64encode(source.encode()).decode(),
        },
    }
    monkeypatch.setattr(repopilot, "MAX_FILES", 1)
    monkeypatch.setattr(repopilot.httpx, "get", lambda url, **kwargs: _response(payloads[url]))

    snapshot = repopilot.GitHubIngestor(token="").ingest("https://github.com/acme/platform")
    assert snapshot["file_paths"] == ["src/app.py"]


def test_github_ingestion_does_not_fallback_when_repo_is_missing(monkeypatch):
    monkeypatch.setattr(
        repopilot.httpx,
        "get",
        lambda *args, **kwargs: _response({}, status_code=404),
    )
    with pytest.raises(RepositoryError) as error:
        repopilot.GitHubIngestor(token="").ingest("https://github.com/acme/missing")
    assert error.value.status_code == 404


def test_github_rate_limit_is_reported(monkeypatch):
    monkeypatch.setattr(
        repopilot.httpx,
        "get",
        lambda *args, **kwargs: _response({}, status_code=403, headers={"X-RateLimit-Remaining": "0"}),
    )
    with pytest.raises(RepositoryError) as error:
        repopilot.GitHubIngestor(token="").ingest("https://github.com/acme/platform")
    assert error.value.status_code == 429


def test_github_timeout_is_reported_without_demo_fallback(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(repopilot.httpx, "get", timeout)
    with pytest.raises(RepositoryError) as error:
        repopilot.GitHubIngestor(token="").ingest("https://github.com/acme/platform")
    assert error.value.status_code == 504


def test_llm_malformed_response_is_reported_without_crashing(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "not JSON"}}]}

    monkeypatch.setattr(repopilot.httpx, "post", lambda *args, **kwargs: Response())
    provider = LLMProvider("test-key", "https://llm.example/v1", "test-model", "test")
    with pytest.raises(repopilot.LLMUnavailable, match="malformed"):
        provider.analyze("issue", "", [{"path": "src/a.py", "line_start": 1, "line_end": 3, "snippet": "code"}])


def test_llm_accepts_structured_analysis_with_evidence_bounded_citations(monkeypatch):
    structured = {
        "summary": "The expiration guard allows equality.",
        "summary_citations": [{"file_path": "src/a.py", "line_start": 2, "line_end": 3}],
        "root_cause": "The strict comparison leaves the token valid at expiry.",
        "root_cause_citation": {"file_path": "src/a.py", "line_start": 2, "line_end": 3},
        "hypotheses": [
            {
                "description": "The strict boundary check is likely incorrect.",
                "citation": {"file_path": "src/a.py", "line_start": 2, "line_end": 3},
                "confidence": 0.8,
                "confidence_reason": "The compared condition is present in the source snippet.",
            }
        ],
        "patch": "--- a/src/a.py\n+++ b/src/a.py\n@@ -2 +2 @@\n-if now > expiry:\n+if now >= expiry:",
    }

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(structured)}}]}

    monkeypatch.setattr(repopilot.httpx, "post", lambda *args, **kwargs: Response())
    provider = LLMProvider("test-key", "https://llm.example/v1", "test-model", "test-provider")
    result = provider.analyze(
        "session expires",
        "Reject on expiry",
        [{"path": "src/a.py", "line_start": 1, "line_end": 4, "snippet": "code"}],
    )
    assert result.provider == "test-provider"
    assert result.root_cause_citation.file_path == "src/a.py"


def test_llm_rejects_citations_outside_retrieved_evidence(monkeypatch):
    structured = {
        "summary": "summary",
        "summary_citations": [{"file_path": "src/a.py", "line_start": 1, "line_end": 2}],
        "root_cause": "cause",
        "root_cause_citation": {"file_path": "src/a.py", "line_start": 1, "line_end": 2},
        "hypotheses": [
            {
                "description": "claim",
                "citation": {"file_path": "src/other.py", "line_start": 1, "line_end": 2},
                "confidence": 0.5,
                "confidence_reason": "limited",
            }
        ],
        "patch": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new",
    }

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(structured)}}]}

    monkeypatch.setattr(repopilot.httpx, "post", lambda *args, **kwargs: Response())
    provider = LLMProvider("test-key", "https://llm.example/v1", "test-model", "test")
    evidence = [{"path": "src/a.py", "line_start": 1, "line_end": 3, "snippet": "code"}]
    with pytest.raises(repopilot.LLMUnavailable, match="outside"):
        provider.analyze("issue", "", evidence)


def test_llm_timeout_is_reported(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("late")

    monkeypatch.setattr(repopilot.httpx, "post", timeout)
    provider = LLMProvider("test-key", "https://llm.example/v1", "test-model", "test")
    with pytest.raises(repopilot.LLMUnavailable, match="timed out"):
        provider.analyze("issue", "", [{"path": "src/a.py", "line_start": 1, "line_end": 3, "snippet": "code"}])


def test_llm_rate_limit_is_reported(monkeypatch):
    class Response:
        status_code = 429

    monkeypatch.setattr(repopilot.httpx, "post", lambda *args, **kwargs: Response())
    provider = LLMProvider("test-key", "https://llm.example/v1", "test-model", "test")
    with pytest.raises(repopilot.LLMUnavailable, match="rate limit"):
        provider.analyze("issue", "", [{"path": "src/a.py", "line_start": 1, "line_end": 3, "snippet": "code"}])


def test_patch_validation_checks_unified_diff_and_allowed_paths():
    patch = "--- a/src/session.py\n+++ b/src/session.py\n@@ -1 +1 @@\n-old\n+new"
    assert validate_unified_diff(patch, {"src/session.py"}) == ["src/session.py"]

    with pytest.raises(ValueError, match="outside"):
        validate_unified_diff(
            "--- a/../../secret\n+++ b/../../secret\n@@ -1 +1 @@\n-old\n+new",
            {"src/session.py"},
        )

    with pytest.raises(ValueError, match="line counts"):
        validate_unified_diff(
            "--- a/src/session.py\n+++ b/src/session.py\n@@ -1,2 +1 @@\n-old\n+new",
            {"src/session.py"},
        )


@pytest.mark.parametrize(
    "patch",
    [
        "--- a/../../outside.py\n+++ b/../../outside.py\n@@ -1 +1 @@\n-old\n+new",
        "--- a/C:/outside.py\n+++ b/C:/outside.py\n@@ -1 +1 @@\n-old\n+new",
        "--- a/..\\outside.py\n+++ b/..\\outside.py\n@@ -1 +1 @@\n-old\n+new",
        "--- a/src/session.py\n+++ b/src/session.py\n@@ -1 +1 @@\n-old\n+new\nGIT binary patch",
        "--- a/src/session.py\n+++ b/src/session.py\n@@ malformed @@\n-old\n+new",
    ],
)
def test_patch_validation_rejects_path_traversal_binary_and_malformed_diffs(patch):
    with pytest.raises(ValueError):
        validate_unified_diff(patch, {"src/session.py"})


def test_patch_validation_rejects_oversized_input():
    with pytest.raises(ValueError, match="64 KB"):
        validate_unified_diff("x" * 64001, {"src/session.py"})


def test_explicit_demo_mode_indexes_local_fixture_without_fake_github_claims():
    snapshot = repopilot.demo_snapshot()
    evidence = rank_evidence(snapshot["source_files"], "session expires valid expiry")
    assert snapshot["mode"] == "demo"
    assert snapshot["revision"] == "local-demo"
    assert evidence
    assert all(item["path"] in snapshot["source_files"] for item in evidence)


def test_investigation_without_model_keeps_real_demo_evidence_and_unrun_verification(monkeypatch):
    database_path = Path(__file__).with_name("test_investigation.sqlite3")
    if database_path.exists():
        database_path.unlink()
    monkeypatch.setattr(app, "DB_PATH", database_path)
    monkeypatch.setenv("MODEL_PROVIDER", "disabled")
    try:
        app.init_db()
        payload = app.InvestigationRequest(
            repo_url="demo://session-expiry",
            bug_report="The session remains valid after the expiry boundary.",
        )
        result = app._investigate(payload)
        assert result["repository_mode"] == "demo"
        assert result["revision"] == "local-demo"
        assert result["evidence"]
        assert result["patch"] == ""
        assert result["verification"]["status"] == "NOT_RUN"
        assert result["errors"]
        assert "source_files" not in result
    finally:
        if database_path.exists():
            database_path.unlink()


def test_job_persistence_round_trips_details_and_is_restart_independent(monkeypatch):
    database_path = Path(__file__).with_name("test_jobs.sqlite3")
    if database_path.exists():
        database_path.unlink()
    monkeypatch.setattr(app, "DB_PATH", database_path)
    try:
        app.init_db()
        payload = app.InvestigationRequest(
            repo_url="demo://session-expiry",
            bug_report="Sessions remain valid at the expiry boundary.",
        )
        snapshot = repopilot.demo_snapshot()
        evidence = rank_evidence(snapshot["source_files"], payload.bug_report)
        job = app._analysis_result("job-123", payload, snapshot, evidence, None, "provider disabled")
        app._save_job(job, payload)

        loaded = app._get_job("job-123")
        assert loaded["repository_mode"] == "demo"
        assert loaded["revision"] == "local-demo"
        assert loaded["verification"]["status"] == "NOT_RUN"
        assert loaded["evidence"][0]["snippet"]
    finally:
        if database_path.exists():
            database_path.unlink()


def test_approved_patch_only_changes_a_disposable_copy(monkeypatch):
    database_path = Path(__file__).with_name("test_patch.sqlite3")
    workspace_root = Path(__file__).with_name("test_workspaces")
    if database_path.exists():
        database_path.unlink()
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    monkeypatch.setattr(app, "DB_PATH", database_path)
    monkeypatch.setenv("REPOPILOT_WORKSPACE_ROOT", str(workspace_root))
    try:
        app.init_db()
        content = "old\n"
        patch = "--- a/src/module.py\n+++ b/src/module.py\n@@ -1 +1 @@\n-old\n+new"
        payload = app.InvestigationRequest(
            repo_url="demo://session-expiry",
            bug_report="The module old value is incorrect and should be new.",
        )
        snapshot = {
            "repo_url": payload.repo_url,
            "repository_name": "test fixture",
            "revision": "local-test",
            "mode": "demo",
            "indexing_status": "complete",
            "file_count": 1,
            "file_paths": ["src/module.py"],
            "source_files": {"src/module.py": content},
        }
        evidence = [
            {
                "path": "src/module.py",
                "line_start": 1,
                "line_end": 1,
                "score": 1,
                "matched_terms": ["module"],
                "snippet": content,
            }
        ]
        analysis = StructuredAnalysis(
            summary="candidate patch",
            summary_citations=[{"file_path": "src/module.py", "line_start": 1, "line_end": 1}],
            root_cause="The old value is wrong.",
            root_cause_citation={"file_path": "src/module.py", "line_start": 1, "line_end": 1},
            hypotheses=[
                Hypothesis(
                    description="The implementation returns an incorrect value.",
                    citation={"file_path": "src/module.py", "line_start": 1, "line_end": 1},
                    confidence=0.6,
                    confidence_reason="One matching source line.",
                )
            ],
            patch=patch,
        )
        job = app._analysis_result("patch-job", payload, snapshot, evidence, analysis, None)
        app._save_job(job, payload)

        result = app.approve_patch("patch-job", app.PatchApproval(approved=True))
        assert result["patch_approved"] is True
        assert result["approved_files"] == [{"path": "src/module.py", "content": "new\n"}]
        assert database_path.exists()
        with pytest.raises(app.HTTPException) as repeated:
            app.approve_patch("patch-job", app.PatchApproval(approved=True))
        assert repeated.value.status_code == 409
    finally:
        if database_path.exists():
            database_path.unlink()
        if workspace_root.exists():
            shutil.rmtree(workspace_root)


def test_verification_state_transitions_and_worker_fail_closed():
    worker = DisabledExecutionWorker()
    result = worker.run({"revision": "abc123"})
    assert result["status"] == "BLOCKED"
    assert result["exit_code"] is None
    assert allowed_transition(VerificationState.NOT_RUN, VerificationState.BLOCKED)
    assert not allowed_transition(VerificationState.PASSED, VerificationState.RUNNING)


def test_health_reports_database_and_model_configuration_without_exposing_secrets(monkeypatch):
    database_path = Path(__file__).with_name("test_health.sqlite3")
    if database_path.exists():
        database_path.unlink()
    monkeypatch.setattr(app, "DB_PATH", database_path)
    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MODEL_API_KEY", "test-secret-value")
    try:
        app.init_db()
        result = __import__("asyncio").run(app.healthcheck())
        result_data = json.loads(result.body) if hasattr(result, "body") else result
        assert result_data["status"] == "ok"
        assert result_data["database"] == "ok"
        assert result_data["model_configured"] is True
        assert "test-secret-value" not in json.dumps(result_data)
    finally:
        if database_path.exists():
            database_path.unlink()


def test_security_headers_are_set_on_http_responses():
    from fastapi.testclient import TestClient

    response = TestClient(app.app).get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_password_hash_uses_salt_and_verifies_without_storing_plaintext():
    stored = app._password_hash("correct horse battery staple")
    assert stored != "correct horse battery staple"
    assert app._password_matches("correct horse battery staple", stored)
    assert not app._password_matches("wrong password", stored)


def test_signed_session_rejects_tampering_and_expired_tokens(monkeypatch):
    monkeypatch.setattr(app, "SESSION_SECRET", "unit-test-secret-that-is-not-for-production")
    token = app._issue_session("alice")
    assert app._read_session(token) == "alice"
    assert app._read_session(token + "tampered") is None
    monkeypatch.setattr(app.time, "time", lambda: 9_999_999_999)
    assert app._read_session(token) is None


def test_owner_scope_hides_other_users_jobs_when_auth_is_required(monkeypatch, tmp_path):
    database_path = tmp_path / "owner-scope.sqlite3"
    monkeypatch.setattr(app, "DB_PATH", database_path)
    monkeypatch.setattr(app, "AUTH_MODE", "required")
    app.init_db()
    payload = app.InvestigationRequest(repo_url="demo://session-expiry", bug_report="Session expiry is not handled at the boundary.")
    snapshot = repopilot.demo_snapshot()
    evidence = rank_evidence(snapshot["source_files"], payload.bug_report)
    owner_token = app.CURRENT_USER.set("alice")
    try:
        job = app._analysis_result("owner-job", payload, snapshot, evidence, None, "provider disabled")
        app._save_job(job, payload)
    finally:
        app.CURRENT_USER.reset(owner_token)
    other_token = app.CURRENT_USER.set("bob")
    try:
        with pytest.raises(app.HTTPException) as error:
            app._get_job("owner-job")
        assert error.value.status_code == 404
    finally:
        app.CURRENT_USER.reset(other_token)


def test_required_auth_registers_account_and_protects_job_api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "DB_PATH", tmp_path / "auth-flow.sqlite3")
    monkeypatch.setattr(app, "AUTH_MODE", "required")
    monkeypatch.setattr(app, "SESSION_SECRET", "test-secret-long-enough-for-auth-flow")
    monkeypatch.setenv("REPOPILOT_REGISTRATION_CODE", "unit-test-invite-code")
    app.init_db()
    with TestClient(app.app) as client:
        assert client.get("/api/jobs").status_code == 401
        response = client.post("/api/auth/register", json={"username": "alice", "password": "a-very-long-test-password", "registration_code": "unit-test-invite-code"})
        assert response.status_code == 200
        assert response.json()["authenticated"] is True
        assert "repopilot_session" in response.cookies
        assert client.get("/api/auth/me").json()["username"] == "alice"
        assert client.get("/api/jobs").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/jobs").status_code == 401


def test_provider_diagnostics_does_not_return_secret_and_checks_live_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MODEL_API_KEY", "test-secret-never-return-this")
    monkeypatch.setenv("MODEL_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    seen = {}

    class Response:
        status_code = 200

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs["headers"]
        return Response()

    monkeypatch.setattr(app.httpx, "get", fake_get)
    with TestClient(app.app) as client:
        response = client.post("/api/diagnostics/provider")
    assert response.status_code == 200
    assert response.json() == {
        "status": "connected", "provider": "openai_compatible", "model": "test-model",
        "credential": "accepted", "secret_returned": False,
    }
    assert seen["url"] == "https://provider.example/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer test-secret-never-return-this"
    assert "test-secret-never-return-this" not in response.text


def test_provider_diagnostics_fails_closed_without_credentials(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with TestClient(app.app) as client:
        response = client.post("/api/diagnostics/provider")
    assert response.status_code == 503


def test_verification_uses_approved_snapshot_when_patch_was_approved(monkeypatch):
    from fastapi.testclient import TestClient

    job = {
        "job_id": "verify-job", "revision": "abc", "status": "completed",
        "source_files": {"src/app.py": "VALUE = 1\n"},
        "approved_source_files": {"src/app.py": "VALUE = 2\n"},
        "patch_approved": True,
        "verification": {"status": "NOT_RUN"}, "events": [], "steps": [],
        "summary": "test", "patch": "", "evidence": [], "owner": "local",
    }
    captured = {}

    class Worker:
        def run(self, payload):
            captured["source_files"] = payload["source_files"]
            return {
                "status": "PASSED", "command": "python -m pytest -q", "tested_commit": "abc",
                "baseline": None, "post_patch": None, "exit_code": 0,
                "duration_seconds": 0.1, "logs": "1 passed", "details": "Tests completed.",
            }

    monkeypatch.setattr(app, "_get_job", lambda job_id: job)
    monkeypatch.setattr(app, "execution_worker", Worker())
    monkeypatch.setattr(app, "_update_job", lambda updated: None)
    with TestClient(app.app) as client:
        response = client.post("/api/jobs/verify-job/verify")
    assert response.status_code == 200
    assert captured["source_files"]["src/app.py"] == "VALUE = 2\n"
    assert response.json()["verification"]["status"] == "PASSED"
    assert "approved_source_files" not in response.json()


def test_session_expires_at_exact_boundary(monkeypatch):
    monkeypatch.setattr(app, "SESSION_SECRET", "unit-test-session-secret-long-enough")
    monkeypatch.setattr(app.time, "time", lambda: 1_000)
    token = app._issue_session("alice")
    monkeypatch.setattr(app.time, "time", lambda: 1_000 + 12 * 60 * 60)
    assert app._read_session(token) is None


def test_registration_requires_invite_code(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "DB_PATH", tmp_path / "invite-code.sqlite3")
    monkeypatch.setattr(app, "AUTH_MODE", "required")
    monkeypatch.setattr(app, "SESSION_SECRET", "test-secret-long-enough-for-auth-flow")
    monkeypatch.setenv("REPOPILOT_REGISTRATION_CODE", "invite-code-long-enough")
    app.init_db()
    with TestClient(app.app) as client:
        response = client.post("/api/auth/register", json={
            "username": "alice", "password": "a-very-long-test-password"
        })
    assert response.status_code == 403


def test_ui_exposes_invite_code_and_provider_diagnostic_controls():
    from fastapi.testclient import TestClient

    with TestClient(app.app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert 'id="registrationCode"' in response.text
    assert 'id="providerDiagnostic"' in response.text


def test_provider_diagnostic_rejects_remote_insecure_http(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MODEL_API_KEY", "test-secret-never-return-this")
    monkeypatch.setenv("MODEL_BASE_URL", "http://provider.example/v1")
    with TestClient(app.app) as client:
        response = client.post("/api/diagnostics/provider")
    assert response.status_code == 400
    assert "HTTPS" in response.json()["detail"]


def test_favicon_is_served_without_a_not_found_error():
    from fastapi.testclient import TestClient

    with TestClient(app.app) as client:
        for path in ("/favicon.ico", "/favicon.svg"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("image/svg+xml")
            assert "<svg" in response.text


def test_ui_references_the_favicon_asset():
    from fastapi.testclient import TestClient

    with TestClient(app.app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert 'rel="icon"' in response.text
    assert "/favicon.svg" in response.text
