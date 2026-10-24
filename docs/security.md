# Security model and deployment checklist

RepoPilot AI handles untrusted issue text, public repository source, and model output. Its runner is an initial container-isolated execution facility, not a certified sandbox. Do not use it for hostile public workloads without a stronger per-job microVM/VM boundary and independent review.

## Implemented controls

- GitHub URL parsing requires HTTPS and the exact `github.com` hostname. API origin is fixed, redirects are disabled, and repository file count/size/aggregate ingestion is bounded.
- Model API keys stay server-side. Remote provider URLs must use HTTPS (loopback HTTP is allowed for development). Provider responses are schema-validated and citations must match retrieved evidence; diffs are path/hunk validated.
- Account passwords are PBKDF2-HMAC-SHA256 hashed with random salts. Signed sessions expire after 12 hours, use HttpOnly/SameSite=Strict cookies, and can be configured `Secure` for HTTPS.
- Auth-required mode fails at startup if the session secret is missing/too short. Job reads enforce account ownership. Account login/registration routes have a per-process request throttle; it is not a distributed rate limiter.
- Security headers and cross-origin checks are enabled. Caddy supplies HTTPS and HSTS in the production Compose profile. The API's host port is loopback-only; Caddy is the public ingress.
- Patch approval is explicit, uses validated paths, and applies the patch only in a disposable workspace. Verification runs in a separate runner container, not in the API process.
- Runner has no published port, is attached to an internal Docker network, runs unprivileged, has no capabilities, no-new-privileges, a read-only root filesystem, bounded tmpfs, CPU/memory/PID/time/file/output limits, and no dependency installation/network egress.
- SQLite backup uses the online backup API and runs an integrity check.

## Important residual risks

- Docker containers share the host kernel. Malicious tests can still attack the kernel, exhaust resources within configured limits, or attempt to interact with the API over the private network. The runner is not safe enough to advertise as a public arbitrary-code execution platform. Prefer disposable microVMs, strong per-job isolation, egress denial, and a narrow authenticated queue for hostile workloads.
- The runner executes only Python pytest files present in the bounded indexed snapshot. It does not install project dependencies, execute npm/shell commands, or run the full repository checkout. Missing tests produce `INCONCLUSIVE`; dependency/import problems may produce `FAILED`.
- A single bounded test run is not baseline-vs-post-patch differential verification. An approved patch is tested against the approved source snapshot, but dependency files not indexed by the ingestion budget can make results inconclusive or fail.
- The account throttle is per process and resets on restart. Add a distributed rate limiter at the edge for production.
- Registration requires `REPOPILOT_REGISTRATION_CODE` when authentication is enabled and is capped by `REPOPILOT_MAX_USERS`. Protect and rotate the invite code; the app does not provide a full invitation-management UI.
- No live provider credentials are bundled. Provider diagnostic success checks `/models`, not the full structured analysis workflow.
- No deployment can be claimed verified until DNS, firewall, HTTPS certificate issuance/renewal, authentication, provider calls, backups, and restore procedures have been checked on the actual host.
- SQLite contains issue text, source snippets, and model output. Restrict filesystem access, define retention, encrypt host/volume backups, and store backups off-host.

## Production checklist

1. Set a strong `REPOPILOT_SESSION_SECRET`, a separate `REPOPILOT_REGISTRATION_CODE`, `REPOPILOT_AUTH_MODE=required`, `REPOPILOT_COOKIE_SECURE=true`, and a real `DOMAIN`.
2. Keep `.env` private; use least-privilege provider credentials and HTTPS-only model endpoints.
3. Open only ports 80/443 publicly; keep SSH restricted. The API host port is loopback-only in Compose.
4. Use `docker compose --profile production up --build -d`, verify the HTTPS certificate, register/sign in, test ownership isolation, and check `/api/health`.
5. Authenticate and call `POST /api/diagnostics/provider`; then submit a harmless test issue and verify that a real model response is produced.
6. Run the verification runner against a known harmless repository containing Python tests and confirm both passing and failing results. Do not rely on the local unit test as proof of Docker isolation.
7. Create an SQLite backup, copy it off-host, and rehearse restore.
8. Configure host monitoring, centralized logs without secrets, patch management, incident response, data retention, and an independent security review.
