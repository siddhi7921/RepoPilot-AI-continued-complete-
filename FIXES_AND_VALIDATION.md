# RepoPilot AI — Hardening and Validation Report

## Implemented in this build

- Fixed root-level pytest import configuration and updated project setup documentation.
- Added account registration/login/logout, salted PBKDF2 password hashing, signed expiring HttpOnly sessions, secure-cookie configuration, per-account job ownership, invite-code-gated registration, same-origin checks, and a per-process account-endpoint throttle.
- Added baseline browser security headers and health checks for SQLite plus the configured runner service.
- Added an authenticated provider connectivity diagnostic that checks the configured `/models` endpoint, rejects remote HTTP model URLs, and never returns the provider secret.
- Added a separate runner service and Dockerfile. The API calls it over a private internal network; the runner has bounded file count/size/output/runtime, Python pytest-only execution, process-group termination on timeout, a restricted container configuration, and explicit `INCONCLUSIVE` results when tests are absent.
- Reserved part of the bounded GitHub ingestion file budget for Python test files.
- Verification now transitions through `RUNNING`, can retry after blocked/failed/inconclusive/timed-out states, and uses the approved source snapshot after patch approval.
- Added production Compose configuration with Caddy TLS ingress, loopback-only API port, runner resource limits, health checks, and persistent SQLite storage. Added a Render Blueprint with a public API, private runner, persistent disk, generated session secret, and explicit environment-secret placeholders.
- Added SQLite online-backup script with integrity checking.
- Updated README, architecture/security/evaluation docs, and CI to include runner tests and sample regression tests.

## Validation completed in this environment

- `python -m py_compile app.py repopilot.py verification.py runner_service.py scripts/backup_db.py` — passed.
- `node --check static/app.js` — passed.
- `python -m pytest -q -W default` — 55 passed, no warnings.
- From `samples/session-expiry`: `python -m pytest -q tests/repro_session_manager.py` — 3 passed.
- Local API smoke test — `/api/health` returned `ok`; demo investigation creation returned HTTP 200.
- Runner HTTP smoke test — a harmless Python test submitted to the runner returned `PASSED`.
- API-to-runner end-to-end smoke test — created the demo investigation through the running API, called its verification endpoint, and got `PASSED` with exit code 0 and `3 passed` in the runner logs.
- UI smoke test — account invite-code and provider-diagnostic controls are present; frontend JavaScript syntax check passed.
- SQLite backup utility smoke test — generated backup passed `PRAGMA integrity_check` and retained the test row.
- `docker-compose.yml` parsed as YAML and the expected services/internal network were present.

## Checks that could not be performed here

- Docker Engine/Compose is not installed in this environment, so images, container runtime restrictions, Compose networking, and Caddy startup/certificate issuance were not executed. The Render Blueprint was YAML-parsed locally but not applied to the account.
- No real model provider credential was available. The provider diagnostic and analysis paths are covered by mocked tests, but a real provider request remains unverified.
- No production host/domain credentials were available. Public HTTPS deployment, live account flow behind Caddy, monitoring, and off-host backup/restore have not been verified. The Render account currently has no services, and the updated project has no Git remote/repository URL, so hosted deployment could not be started.
- No independent third-party security audit was performed. Container isolation is defense-in-depth, not a certified sandbox. For hostile public workloads, replace the runner with a per-job microVM or equivalent stronger boundary and add distributed edge rate limiting.

## Environment note

`python -m pip check` in the shared validation environment reports an unrelated pre-existing global package conflict (`moviepy` requires `pillow<12`, while the environment has Pillow 12.3.0). Pillow/moviepy are not dependencies in this project's `requirements.txt`; this environment-wide conflict does not appear in the project test suite or the project container dependency list.
