# RepoPilot AI

**From bug report to evidence-backed patch review.**

RepoPilot AI indexes a bounded snapshot of a public GitHub repository, retrieves source evidence, optionally requests structured analysis from an OpenAI-compatible model, validates citations and unified diffs, and stores an investigation report. Original repositories are never modified.

This remains an experimental MVP. Its isolated runner is a hardened container baseline, not a certified sandbox. Do not expose arbitrary untrusted code execution to the public without a stronger per-job isolation boundary and independent security review.

## Features

- HTTPS-only public GitHub URL validation and bounded commit/tree/blob ingestion.
- Pinned commit metadata, source snippets, and line-numbered evidence.
- Optional server-side model integration with structured response validation and evidence-bounded citations.
- Explicit patch approval; validated diffs are applied only to a disposable workspace.
- SQLite persistence for jobs, evidence, events, patch review, and verification status.
- Optional account authentication, salted PBKDF2 password hashing, signed expiring sessions, per-account job ownership, and security headers.
- Separate private runner container for Python pytest files included in the bounded source snapshot.
- Health endpoint, explicit provider connectivity diagnostic, and SQLite online-backup utility.

## Local development

Python 3.13 is the tested version. Git is needed only for applying an approved patch in a temporary workspace.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Use `demo://session-expiry` for the included fixture or provide an HTTPS public GitHub repository URL. Network errors, missing repositories, and rate limits are reported; they are never silently replaced with fabricated evidence.

## Model provider

Set the following environment variables server-side (for example, in `.env`, which must never be committed):

```env
MODEL_PROVIDER=openai_compatible
MODEL_API_KEY=your-private-key
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o-mini
```

Provider keys are never returned by the API. Remote model endpoints must use HTTPS; HTTP is allowed only for loopback development services. If the provider is disabled or credentials are missing, repository ingestion and evidence retrieval still work but the application does not invent AI conclusions. `POST /api/diagnostics/provider` checks the configured provider's `/models` endpoint; it requires authentication when auth is enabled and does not guarantee every structured analysis request will succeed.

## Docker Compose runner

The Compose setup builds a web API and a separate runner container. The runner has no published host port, is on an internal Docker network, runs as an unprivileged user, has no Linux capabilities, a read-only root filesystem, restricted temporary storage, and CPU/memory/PID/time/file/output limits. It only runs included Python pytest files and does not install repository dependencies or execute arbitrary shell/npm commands. Missing tests are reported as `INCONCLUSIVE`.

Create `.env` from `.env.example` and set both `REPOPILOT_SESSION_SECRET` and `REPOPILOT_REGISTRATION_CODE` to fresh random values. Generate a value with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then run:

```powershell
docker compose up --build -d
```

The API host port is bound to `127.0.0.1:8000`. In this local Compose configuration, authentication is required. The runner executes untrusted test code inside a restricted container; containers share the host kernel, so this is defense-in-depth rather than a guarantee of safe sandboxing. For hostile public workloads, use disposable microVMs or equivalent stronger per-job isolation and an independent review.

## Production HTTPS deployment

Use a Linux host with Docker Engine/Compose, a real DNS name pointing to the host, and public inbound ports 80 and 443. In `.env`, set:

- `DOMAIN=your.real.domain`
- `REPOPILOT_AUTH_MODE=required`
- `REPOPILOT_COOKIE_SECURE=true`
- `REPOPILOT_SESSION_SECRET=` a fresh `secrets.token_urlsafe(48)` value
- `REPOPILOT_REGISTRATION_CODE=` a separate private invite code shared only with intended account creators
- `MODEL_PROVIDER`, `MODEL_API_KEY`, `MODEL_BASE_URL=https://api.openai.com/v1`, and `MODEL_NAME` if using a live model

Then run:

```sh
docker compose --profile production up --build -d
```

Caddy obtains and renews HTTPS certificates when DNS and firewall prerequisites are correct. Do not use `DOMAIN=localhost` for public hosting. Keep `.env` private and restrict Docker-host access. After deployment, verify `https://your.real.domain/api/health`, register/sign in, call the provider diagnostic endpoint while authenticated, and submit a harmless investigation. A successful `/models` check is not a guarantee of end-to-end AI analysis.

## Backups

Create a consistent SQLite backup inside the persistent volume:

```sh
docker compose exec api python scripts/backup_db.py /data/repo_pilot.db /data/backups
```

Copy backups off-host and rehearse a restore. A backup in the same Docker volume is not an off-site backup.

## Tests

Run the root suite:

```powershell
python -m pytest -q
```

Run the sample boundary tests separately:

```powershell
cd samples\session-expiry
python -m pytest -q tests\repro_session_manager.py
```

The deterministic tests cover GitHub ingestion validation, mocked provider response validation, authentication and ownership, path validation, successful/failed runner samples, no-test handling, runner result mapping, and error states. They do not prove Docker isolation, live provider access, or production deployment.

## API overview

- `GET /api/health` (returns 503 when the database or configured runner is unhealthy)
- `POST /api/jobs` or `POST /api/repositories/analyze`
- `GET /api/jobs` and `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/approve-patch` with `{"approved": true}`
- `POST /api/jobs/{job_id}/verify`
- `POST /api/diagnostics/provider` to explicitly check provider connectivity
- `POST /api/auth/register`, `/api/auth/login`, `/api/auth/logout`; `GET /api/auth/me`

See [docs/security.md](docs/security.md) for residual risks and the production checklist. Private GitHub repositories, automatic PR creation, arbitrary command execution, dependency installation inside the runner, and baseline-vs-post-patch differential testing are not supported.

## Deploy to Render

A `render.yaml` Blueprint is included for a public API and a separate **private** runner service. It uses a persistent disk for SQLite and paid Starter instances; review current Render pricing before creating resources. The runner is not configured as a public web service.

1. Push this complete project directory to a GitHub, GitLab, or Bitbucket repository. The original uploaded folder had no Git remote, so it cannot be deployed directly from the ZIP.
2. In Render, choose **New → Blueprint**, connect that repository, and select the branch containing `render.yaml`.
3. Review the two Starter instances and persistent disk cost before applying the Blueprint.
4. In the API service's environment settings, set `REPOPILOT_REGISTRATION_CODE` to a fresh private invite code. Add a real `MODEL_API_KEY` only when enabling a supported live provider. Keep secrets out of source control.
5. Deploy and check `https://<your-api-service>.onrender.com/api/health`. It should report `status: ok`, `database: ok`, and `execution_worker: ok`.
6. Register an account using the invite code, run the provider diagnostic after configuring credentials, and test a harmless demo verification before accepting real workloads.

The Blueprint is configuration, not proof of a live deployment. The deployment has not been applied because the updated code is currently local and there is no repository URL connected to the Render account. Docker builds and hosted HTTPS/provider integration must be verified after the repository is connected.
