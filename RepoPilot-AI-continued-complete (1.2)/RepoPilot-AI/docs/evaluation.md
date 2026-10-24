# Evaluation methodology

## Current deterministic checks

Run:

```powershell
python -m pytest -q
cd samples\session-expiry
python -m pytest -q tests\repro_session_manager.py
```

The root suite uses mocked GitHub and model responses and tests URL validation, bounded ingestion, evidence retrieval, structured model response validation, provider diagnostics, patch validation, disposable patch review, SQLite persistence, authentication/session boundaries, job ownership, rate-limit configuration, runner path/size validation, runner pass/fail/timeout/no-test states, runner result mapping, and error states. The sample regression suite checks session expiry at the boundary.

## Current local validation (2026-10-10)

- Root tests: 55 passed, no warnings.
- Session-expiry sample tests: 3 passed.
- Python compilation and JavaScript syntax checks: passed.
- Runner service HTTP smoke test: a submitted harmless Python test passed.
- API-to-runner end-to-end smoke test: the demo investigation verification endpoint returned `PASSED`, exit code 0, and `3 passed` in runner logs.
- API HTTP smoke test: health endpoint and demo investigation creation passed.
- SQLite backup script smoke test: backup integrity check passed.
- Compose YAML parsed and expected services/internal runner network were present.

Docker Engine/Compose is not installed in the validation environment, so container image builds, actual network isolation, Caddy certificate issuance, and the Compose production profile were not executed here. A real model API key and production host/domain were not available, so live provider analysis and public deployment remain unverified.

## What is not measured

There are no SWE-bench results, retrieval Recall@k results, patch test-pass rates, latency/cost benchmarks, or claimed model quality success percentages. Passing deterministic tests is evidence, not proof that a defect is eliminated. A live `/models` check also does not prove every structured analysis request succeeds.

## Next evaluation work

Use a versioned public issue set, pin repository commits, exclude reference patches from prompts, and report sample size and failures. Measure retrieval Recall@k against reviewed relevant files; separately measure patch acceptance and post-patch test outcomes. Record provider/model ID, prompt revision, latency, and cost.
