from __future__ import annotations

import json
import base64
import hashlib
import hmac
import secrets
import time
from contextvars import ContextVar
import logging
import os
import threading
import re
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import httpx

from repopilot import (
    GitHubIngestor,
    LLMProvider,
    LLMUnavailable,
    RepositoryError,
    StructuredAnalysis,
    demo_snapshot,
    rank_evidence,
    validate_unified_diff,
)
from verification import DisabledExecutionWorker, HttpExecutionWorker, VerificationState, allowed_transition

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("REPOPILOT_DB_PATH", BASE_DIR / "repo_pilot.db"))
FAVICON_PATH = BASE_DIR / "static" / "favicon.svg"

app = FastAPI(title="RepoPilot AI", version="0.5.0")
CURRENT_USER: ContextVar[str] = ContextVar("repopilot_current_user", default="local")
AUTH_MODE = os.environ.get("REPOPILOT_AUTH_MODE", "disabled").strip().lower()
if AUTH_MODE not in {"disabled", "required"}:
    raise RuntimeError("REPOPILOT_AUTH_MODE must be either disabled or required")
SESSION_SECRET = os.environ.get("REPOPILOT_SESSION_SECRET", "").strip()
if AUTH_MODE == "required" and len(SESSION_SECRET) < 32:
    raise RuntimeError("REPOPILOT_SESSION_SECRET must contain at least 32 characters when authentication is required")
SESSION_COOKIE = "repopilot_session"
COOKIE_SECURE = os.environ.get("REPOPILOT_COOKIE_SECURE", "auto").strip().lower()
if COOKIE_SECURE not in {"auto", "true", "false"}:
    raise RuntimeError("REPOPILOT_COOKIE_SECURE must be auto, true, or false")


def _cookie_is_secure(request: Request) -> bool:
    if COOKIE_SECURE == "true":
        return True
    if COOKIE_SECURE == "false":
        return False
    return request.url.scheme == "https"


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return base64.urlsafe_b64encode(salt).decode().rstrip("=") + "." + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _password_matches(password: str, stored: str) -> bool:
    try:
        salt_s, digest_s = stored.split(".", 1)
        salt = base64.urlsafe_b64decode(salt_s + "=" * (-len(salt_s) % 4))
        expected = base64.urlsafe_b64decode(digest_s + "=" * (-len(digest_s) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _issue_session(username: str) -> str:
    if not SESSION_SECRET:
        raise HTTPException(status_code=503, detail="Session signing secret is not configured.")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": username, "exp": int(time.time()) + 60 * 60 * 12}, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def _read_session(token: str | None) -> str | None:
    if not token or not SESSION_SECRET:
        return None
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(data.get("exp", 0)) <= int(time.time()) or not isinstance(data.get("sub"), str):
            return None
        return data["sub"]
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        username = _read_session(request.cookies.get(SESSION_COOKIE))
        if AUTH_MODE == "required":
            public = request.url.path in {"/api/health", "/api/auth/register", "/api/auth/login", "/api/auth/logout"} or request.url.path.startswith("/static/") or request.url.path == "/favicon.ico"
            if request.url.path.startswith("/api/") and not public and not username:
                return JSONResponse({"detail": "Sign in is required."}, status_code=401)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and username and request.url.path.startswith("/api/"):
                origin = request.headers.get("origin")
                if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                    return JSONResponse({"detail": "Cross-origin request rejected."}, status_code=403)
        token = CURRENT_USER.set(username or "local")
        try:
            response = await call_next(request)
            return response
        finally:
            CURRENT_USER.reset(token)



class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Small per-process brake for public account endpoints; use edge limits too."""
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/api/auth/login", "/api/auth/register"} and request.method == "POST":
            client_ip = request.client.host if request.client else "unknown"
            key = f"{client_ip}:{request.url.path}"
            now = time.time()
            with self._lock:
                hits = [hit for hit in self._hits.get(key, []) if now - hit < 900]
                if len(hits) >= 10:
                    self._hits[key] = hits
                    return JSONResponse({"detail": "Too many account requests. Try again later."}, status_code=429)
                hits.append(now)
                self._hits[key] = hits
                # Keep the in-memory map bounded in long-running local processes.
                if len(self._hits) > 2000:
                    self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 900}
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(AuthMiddleware)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
execution_worker = (
    HttpExecutionWorker(os.environ["REPOPILOT_RUNNER_URL"])
    if os.environ.get("REPOPILOT_RUNNER_URL", "").strip()
    else DisabledExecutionWorker()
)
logger = logging.getLogger("repopilot")


class InvestigationRequest(BaseModel):
    repo_url: str = Field(..., min_length=4, max_length=300)
    bug_report: str = Field(..., min_length=20, max_length=12000)
    expected_behavior: str | None = Field(default=None, max_length=4000)
    reproduction_steps: str | None = Field(default=None, max_length=4000)


class PatchApproval(BaseModel):
    approved: bool


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                repo_url TEXT NOT NULL,
                bug_report TEXT NOT NULL,
                expected_behavior TEXT,
                reproduction_steps TEXT,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                root_cause TEXT NOT NULL,
                patch TEXT NOT NULL,
                verification TEXT NOT NULL,
                evidence TEXT NOT NULL,
                steps TEXT NOT NULL,
                indexing_status TEXT NOT NULL,
                revision TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        for name in ("details_json", "events_json"):
            if name not in columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")


init_db()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_provider() -> LLMProvider | None:
    if os.environ.get("MODEL_PROVIDER", "disabled").lower() in {"", "disabled", "demo"}:
        return None
    api_key = os.environ.get("MODEL_API_KEY", "").strip()
    if not api_key:
        raise LLMUnavailable("MODEL_API_KEY is not configured")
    base_url = os.environ.get("MODEL_BASE_URL", "https://api.openai.com/v1").strip()
    parsed_url = urlparse(base_url)
    if parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise LLMUnavailable("MODEL_BASE_URL must use HTTPS unless it points to a loopback development service.")
    if not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise LLMUnavailable("MODEL_BASE_URL is not a valid provider URL.")
    return LLMProvider(
        api_key=api_key,
        base_url=base_url,
        model=os.environ.get("MODEL_NAME", "gpt-4o-mini"),
        provider=os.environ.get("MODEL_PROVIDER", "openai_compatible"),
    )


def _analysis_result(
    job_id: str,
    payload: InvestigationRequest,
    snapshot: dict[str, Any],
    evidence: list[dict[str, Any]],
    analysis: StructuredAnalysis | None,
    analysis_error: str | None,
) -> dict[str, Any]:
    created_at = now_iso()
    patch = analysis.patch if analysis else ""
    patch_error = None
    if patch:
        try:
            validate_unified_diff(patch, {item["path"] for item in evidence})
        except ValueError as exc:
            patch_error = str(exc)
            patch = ""

    hypotheses = [item.model_dump() for item in analysis.hypotheses] if analysis else []
    events = [
        {"stage": "repository_ingestion", "status": "complete", "at": now_iso(),
         "detail": f"Indexed {snapshot['file_count']} source files at commit {snapshot['revision']}."},
        {"stage": "evidence_retrieval", "status": "complete", "at": now_iso(),
         "detail": f"Retrieved {len(evidence)} code sections with exact source line ranges."},
    ]
    if snapshot.get("indexing_warning"):
        events[0]["status"] = "partial"
        events[0]["detail"] += f" {snapshot['indexing_warning']}"
    if analysis:
        events.append({"stage": "llm_investigation", "status": "complete", "at": now_iso(),
                       "detail": f"Structured analysis returned by {analysis.provider}."})
    else:
        events.append({"stage": "llm_investigation", "status": "unavailable", "at": now_iso(),
                       "detail": analysis_error or "No model provider configured; no model analysis was performed."})
    if patch_error:
        events.append({"stage": "patch_validation", "status": "failed", "at": now_iso(), "detail": patch_error})
    elif patch:
        events.append({"stage": "patch_validation", "status": "complete", "at": now_iso(),
                       "detail": "Unified diff targets indexed source files and passed structural validation."})

    verification = {
        "status": VerificationState.NOT_RUN.value,
        "command": None,
        "tested_commit": snapshot["revision"],
        "baseline": None,
        "post_patch": None,
        "exit_code": None,
        "duration_seconds": None,
        "logs": "",
        "details": "Tests were not executed. No isolated execution worker is enabled.",
    }
    return {
        "job_id": job_id,
        "owner": CURRENT_USER.get(),
        "status": "completed" if snapshot["indexing_status"] == "complete" else "inconclusive",
        "repo_url": snapshot["repo_url"],
        "repository_mode": snapshot["mode"],
        "repository_name": snapshot["repository_name"],
        "revision": snapshot["revision"],
        "indexing_status": snapshot["indexing_status"],
        "indexed_files": snapshot["file_paths"],
        "file_count": snapshot["file_count"],
        "summary": analysis.summary if analysis else (
            "Repository evidence was retrieved. Configure a supported model provider to generate root-cause hypotheses and a candidate patch."
        ),
        "summary_citations": [item.model_dump() for item in analysis.summary_citations] if analysis else [],
        "likely_files": list(dict.fromkeys(item["path"] for item in evidence)),
        "root_cause": analysis.root_cause if analysis else "",
        "root_cause_citation": analysis.root_cause_citation.model_dump() if analysis else None,
        "hypotheses": hypotheses,
        "patch": patch,
        "patch_error": patch_error,
        "patch_approved": False,
        "patch_workspace": None,
        "verification": verification,
        "evidence": evidence,
        "steps": events,
        "events": events,
        "errors": [analysis_error] if analysis_error else ([patch_error] if patch_error else []),
        "created_at": created_at,
        "updated_at": created_at,
        "source_files": snapshot["source_files"],
        "expected_behavior": payload.expected_behavior,
        "reproduction_steps": payload.reproduction_steps,
    }


def _save_job(job: dict[str, Any], payload: InvestigationRequest) -> None:
    created_at = job["created_at"]
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                id, repo_url, bug_report, expected_behavior, reproduction_steps,
                status, summary, root_cause, patch, verification, evidence, steps,
                indexing_status, revision, created_at, updated_at, details_json, events_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["job_id"], payload.repo_url, payload.bug_report, payload.expected_behavior,
                payload.reproduction_steps, job["status"], job["summary"], job["root_cause"], job["patch"],
                json.dumps(job["verification"]), json.dumps(job["evidence"]), json.dumps(job["steps"]),
                job["indexing_status"], job["revision"], created_at, created_at,
                json.dumps(job), json.dumps(job["events"]),
            ),
        )


def _get_job(job_id: str) -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    details = json.loads(row["details_json"]) if row["details_json"] else {}
    if details:
        if AUTH_MODE == "required" and details.get("owner", "local") != CURRENT_USER.get():
            raise HTTPException(status_code=404, detail="Investigation not found")
        return details
    return {
        "job_id": row["id"], "repo_url": row["repo_url"], "status": row["status"],
        "summary": row["summary"], "root_cause": row["root_cause"], "patch": row["patch"],
        "verification": json.loads(row["verification"]), "evidence": json.loads(row["evidence"]),
        "steps": json.loads(row["steps"]), "indexing_status": row["indexing_status"],
        "revision": row["revision"], "events": json.loads(row["events_json"] or "[]"),
    }


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public_result = dict(job)
    public_result.pop("source_files", None)
    public_result.pop("approved_source_files", None)
    return public_result


def _update_job(job: dict[str, Any]) -> None:
    job["updated_at"] = now_iso()
    with get_connection() as connection:
        connection.execute(
            "UPDATE jobs SET status=?, summary=?, patch=?, verification=?, evidence=?, steps=?, "
            "updated_at=?, details_json=?, events_json=? WHERE id=?",
            (
                job["status"], job["summary"], job["patch"], json.dumps(job["verification"]),
                json.dumps(job["evidence"]), json.dumps(job["steps"]), job["updated_at"],
                json.dumps(job), json.dumps(job["events"]), job["job_id"],
            ),
        )


def _investigate(payload: InvestigationRequest) -> dict[str, Any]:
    try:
        if payload.repo_url.strip().lower() == "demo://session-expiry":
            snapshot = demo_snapshot()
        else:
            snapshot = GitHubIngestor().ingest(payload.repo_url)
    except RepositoryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    evidence = rank_evidence(
        snapshot["source_files"],
        " ".join(filter(None, [payload.bug_report, payload.expected_behavior, payload.reproduction_steps])),
    )
    analysis = None
    analysis_error = None
    try:
        provider = load_provider()
        if provider:
            analysis = provider.analyze(
                payload.bug_report,
                payload.expected_behavior or "",
                evidence,
                payload.reproduction_steps or "",
            )
        else:
            analysis_error = "LLM provider is disabled. Repository ingestion and retrieval completed without AI-generated conclusions."
    except LLMUnavailable as exc:
        logger.warning("Model analysis unavailable: %s", exc)
        analysis_error = str(exc)

    result = _analysis_result(str(uuid4()), payload, snapshot, evidence, analysis, analysis_error)
    _save_job(result, payload)
    return _public_job(result)


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
async def favicon() -> FileResponse:
    """Serve the product icon so browsers never log a missing /favicon.ico request."""
    if not FAVICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="Favicon asset is missing.")
    return FileResponse(FAVICON_PATH, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


class AuthCredentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(..., min_length=12, max_length=256)
    registration_code: str | None = Field(default=None, min_length=8, max_length=256)


@app.post("/api/auth/register")
def register_account(credentials: AuthCredentials, request: Request) -> dict[str, str]:
    if AUTH_MODE != "required":
        raise HTTPException(status_code=404, detail="Account authentication is disabled in this environment.")
    if not SESSION_SECRET:
        raise HTTPException(status_code=503, detail="Set REPOPILOT_SESSION_SECRET before enabling account authentication.")
    invite_code = os.environ.get("REPOPILOT_REGISTRATION_CODE", "").strip()
    if not invite_code:
        raise HTTPException(status_code=503, detail="Account registration is closed until REPOPILOT_REGISTRATION_CODE is configured.")
    if not credentials.registration_code or not hmac.compare_digest(credentials.registration_code, invite_code):
        raise HTTPException(status_code=403, detail="A valid registration code is required.")
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count >= int(os.environ.get("REPOPILOT_MAX_USERS", "100")):
            raise HTTPException(status_code=403, detail="Registration limit reached.")
        try:
            connection.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)", (credentials.username.lower(), _password_hash(credentials.password), now_iso()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Username is already registered.") from exc
    response = JSONResponse({"authenticated": True, "username": credentials.username.lower()})
    response.set_cookie(SESSION_COOKIE, _issue_session(credentials.username.lower()), httponly=True, secure=_cookie_is_secure(request), samesite="strict", max_age=43200, path="/")
    return response


@app.post("/api/auth/login")
def login_account(credentials: AuthCredentials, request: Request):
    if AUTH_MODE != "required":
        raise HTTPException(status_code=404, detail="Account authentication is disabled in this environment.")
    with get_connection() as connection:
        row = connection.execute("SELECT password_hash FROM users WHERE username = ?", (credentials.username.lower(),)).fetchone()
    if not row or not _password_matches(credentials.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    response = JSONResponse({"authenticated": True, "username": credentials.username.lower()})
    response.set_cookie(SESSION_COOKIE, _issue_session(credentials.username.lower()), httponly=True, secure=_cookie_is_secure(request), samesite="strict", max_age=43200, path="/")
    return response


@app.post("/api/auth/logout")
def logout_account():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
def current_account():
    if AUTH_MODE != "required":
        return {"authenticated": False, "auth_mode": "disabled"}
    username = CURRENT_USER.get()
    if username == "local":
        raise HTTPException(status_code=401, detail="Sign in is required.")
    return {"authenticated": True, "username": username}


@app.get("/api/health")
async def healthcheck() -> dict[str, Any]:
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        database_status = "ok"
    except sqlite3.Error:
        logger.exception("Database health check failed")
        database_status = "error"
    runner_status = "disabled"
    if isinstance(execution_worker, HttpExecutionWorker):
        try:
            runner_response = httpx.get(f"{execution_worker.base_url}/health", timeout=2.0, follow_redirects=False)
            runner_response.raise_for_status()
            runner_status = "ok" if runner_response.status_code == 200 else "unavailable"
        except httpx.HTTPError:
            runner_status = "unavailable"
    status = "ok" if database_status == "ok" and runner_status in {"ok", "disabled"} else "degraded"
    provider_name = os.environ.get("MODEL_PROVIDER", "disabled").strip().lower() or "disabled"
    model_configured = provider_name not in {"disabled", "demo"} and bool(os.environ.get("MODEL_API_KEY", "").strip())
    return JSONResponse({
        "status": status,
        "service": "RepoPilot AI",
        "database": database_status,
        "model_provider": provider_name,
        "model_configured": model_configured,
        "execution_worker": runner_status,
        "auth_mode": AUTH_MODE,
        "auth_secret_configured": bool(SESSION_SECRET),
    }, status_code=200 if status == "ok" else 503)


@app.post("/api/diagnostics/provider")
def diagnose_model_provider() -> dict[str, Any]:
    """Make an explicit, user-triggered connectivity/authentication check for the configured provider."""
    provider_name = os.environ.get("MODEL_PROVIDER", "disabled").strip().lower()
    api_key = os.environ.get("MODEL_API_KEY", "").strip()
    base_url = os.environ.get("MODEL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("MODEL_NAME", "gpt-4o-mini")
    parsed_url = urlparse(base_url)
    if not parsed_url.hostname or parsed_url.username or parsed_url.password or (parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise HTTPException(status_code=400, detail="MODEL_BASE_URL must be a valid HTTPS URL (loopback HTTP is allowed for development).")
    if provider_name in {"", "disabled", "demo"}:
        raise HTTPException(status_code=409, detail="A live model provider is not enabled.")
    if not api_key:
        raise HTTPException(status_code=503, detail="MODEL_API_KEY is not configured on the server.")
    try:
        response = httpx.get(
            f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0, follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Model provider connectivity check timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Model provider connectivity check failed.") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=502, detail="Provider rejected the configured credentials or permissions.")
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="Provider rate limit reached during connectivity check.")
    if not 200 <= response.status_code < 300:
        raise HTTPException(status_code=502, detail=f"Provider returned HTTP {response.status_code}.")
    return {"status": "connected", "provider": provider_name, "model": model, "credential": "accepted", "secret_returned": False}


@app.post("/api/repositories/analyze")
def analyze_repository(payload: InvestigationRequest) -> dict[str, Any]:
    return _investigate(payload)


@app.post("/api/jobs")
def create_job(payload: InvestigationRequest) -> dict[str, Any]:
    return _investigate(payload)


@app.get("/api/jobs")
async def list_jobs() -> dict[str, Any]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, repo_url, status, summary, revision, created_at, updated_at "
            "FROM jobs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    jobs = []
    for row in rows:
        item = dict(row)
        if AUTH_MODE == "required":
            detail = _get_job(item["id"])
            if detail.get("owner") != CURRENT_USER.get():
                continue
        jobs.append(item)
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    return _public_job(_get_job(job_id))


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail_page(request: Request, job_id: str) -> HTMLResponse:
    job = _get_job(job_id)
    return templates.TemplateResponse(request, "job.html", {"job": _public_job(job)})


@app.post("/api/jobs/{job_id}/verify")
async def verify_job(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    current = VerificationState(job["verification"]["status"])
    if current == VerificationState.RUNNING:
        raise HTTPException(status_code=409, detail="Verification is already running for this investigation.")
    if not allowed_transition(current, VerificationState.RUNNING):
        raise HTTPException(status_code=409, detail=f"Verification cannot start from {current.value}.")
    job["verification"].update({"status": VerificationState.RUNNING.value, "details": "Isolated verification has started."})
    job["events"].append(
        {"stage": "verification", "status": VerificationState.RUNNING.value, "at": now_iso(),
         "detail": "Isolated verification has started."}
    )
    job["steps"] = job["events"]
    _update_job(job)

    execution_job = dict(job)
    if job.get("patch_approved") and isinstance(job.get("approved_source_files"), dict):
        execution_job["source_files"] = job["approved_source_files"]
    try:
        result = execution_worker.run(execution_job)
    except Exception:
        logger.exception("Isolated verification worker failed")
        result = {
            "status": VerificationState.BLOCKED.value, "command": None,
            "tested_commit": job.get("revision"), "baseline": None, "post_patch": None,
            "exit_code": None, "duration_seconds": None, "logs": "",
            "details": "Isolated runner failed unexpectedly; no test success was recorded.",
        }
    try:
        target = VerificationState(result["status"])
    except (KeyError, ValueError, TypeError):
        target = VerificationState.BLOCKED
        result = {
            "status": target.value, "command": None, "tested_commit": job.get("revision"),
            "baseline": None, "post_patch": None, "exit_code": None,
            "duration_seconds": None, "logs": "",
            "details": "Verification worker returned an invalid result; no test success was recorded.",
        }
    if allowed_transition(VerificationState.RUNNING, target):
        job["verification"].update(result)
        job["events"].append(
            {"stage": "verification", "status": target.value, "at": now_iso(), "detail": result["details"]}
        )
        job["steps"] = job["events"]
        _update_job(job)
    return _public_job(job)


@app.post("/api/jobs/{job_id}/approve-patch")
def approve_patch(job_id: str, approval: PatchApproval) -> dict[str, Any]:
    job = _get_job(job_id)
    if not approval.approved:
        raise HTTPException(status_code=400, detail="Explicit patch approval is required.")
    if job.get("patch_approved"):
        raise HTTPException(status_code=409, detail="This candidate patch has already been reviewed and applied to a disposable workspace.")
    if not job.get("patch"):
        raise HTTPException(status_code=409, detail="There is no validated candidate patch to approve.")

    source_files = job.get("source_files", {})
    try:
        evidence_paths = {item["path"] for item in job.get("evidence", [])}
        validate_unified_diff(job["patch"], evidence_paths)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Patch validation failed: {exc}") from exc

    try:
        workspace_parent = os.environ.get("REPOPILOT_WORKSPACE_ROOT")
        if workspace_parent:
            Path(workspace_parent).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="repopilot-", dir=workspace_parent) as workspace:
            root = Path(workspace).resolve()
            for relative, content in source_files.items():
                safe_path = PurePosixPath(relative)
                if (
                    safe_path.is_absolute()
                    or ".." in safe_path.parts
                    or "\\" in relative
                    or ":" in relative
                    or re.match(r"^[A-Za-z]:", relative)
                ):
                    raise HTTPException(status_code=409, detail="Indexed source contains an unsafe path.")
                destination = root.joinpath(*safe_path.parts).resolve()
                if root not in destination.parents:
                    raise HTTPException(status_code=409, detail="Patch workspace path escaped its root.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content.encode("utf-8"))
            environment = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
            patch_file_name = ".repopilot-candidate.patch"
            (root / patch_file_name).write_bytes(job["patch"].rstrip("\r\n").encode("utf-8") + b"\n")
            check = subprocess.run(
                ["git", "apply", "--check", patch_file_name], cwd=root,
                capture_output=True, timeout=10, env=environment, check=False,
            )
            if check.returncode:
                raise HTTPException(status_code=409, detail=f"Patch does not apply cleanly: {check.stderr[:1000]}")
            applied = subprocess.run(
                ["git", "apply", patch_file_name], cwd=root,
                capture_output=True, timeout=10, env=environment, check=False,
            )
            if applied.returncode:
                raise HTTPException(status_code=409, detail=f"Patch application failed: {applied.stderr[:1000]}")
            changed = []
            for evidence in job["evidence"]:
                file_path = root.joinpath(*PurePosixPath(evidence["path"]).parts)
                if file_path.exists():
                    changed.append({"path": evidence["path"], "content": file_path.read_text(encoding="utf-8")})

            approved_source_files: dict[str, str] = {}
            for relative in source_files:
                safe_relative = PurePosixPath(relative)
                file_path = root.joinpath(*safe_relative.parts)
                if file_path.is_file():
                    approved_source_files[relative] = file_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Git is required for disposable patch review but was not found.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Disposable patch application timed out.") from exc

    job["patch_approved"] = True
    job["patch_workspace"] = "approved-and-applied-to-disposable-workspace"
    job["approved_files"] = changed
    job["approved_source_files"] = approved_source_files
    job["events"].append(
        {"stage": "patch_approval", "status": "complete", "at": now_iso(),
         "detail": "User-approved patch applied to a temporary workspace; the workspace was discarded."}
    )
    job["steps"] = job["events"]
    _update_job(job)
    return _public_job(job)


@app.get("/api/metrics")
async def metrics() -> dict[str, Any]:
    with get_connection() as connection:
        job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    return {
        "agents": ["repository_ingestion", "evidence_retrieval", "structured_llm_investigation", "patch_review"],
        "jobs_processed": job_count,
        "verification": "configured_isolated_runner" if isinstance(execution_worker, HttpExecutionWorker) else "disabled_without_isolated_worker",
        "auth_mode": AUTH_MODE,
        "maturity": "experimental_mvp",
    }
