"""Error taxonomy for model providers.

Every failure mode the application must distinguish is represented as a
dedicated exception class. Each one carries a stable machine-readable code, a
``retryable`` flag, an optional provider-supplied retry delay, and an
``action_required`` hint that the API can surface without leaking secrets.
"""
from __future__ import annotations

import re

REDACTED = "[redacted]"


class ProviderError(Exception):
    """Base class for every model-provider failure."""

    code = "provider_error"
    retryable = False
    action_required: str | None = None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        retry_after: float | None = None,
        action_required: str | None = None,
        provider_detail: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.retry_after = retry_after
        if action_required is not None:
            self.action_required = action_required
        self.provider_detail = provider_detail
        self.http_status = http_status

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "action_required": self.action_required,
            "http_status": self.http_status,
        }


class ProviderConfigurationError(ProviderError):
    """Credentials or endpoint configuration are missing or invalid."""

    code = "provider_not_configured"
    retryable = False


class ProviderAuthError(ProviderError):
    """The provider rejected the configured credentials or permissions."""

    code = "provider_auth_failed"
    retryable = False
    action_required = "Replace the API key with a valid credential for this provider, then retry."


class ProviderQuotaExhaustedError(ProviderError):
    """The account quota cannot serve this request right now.

    Retrying cannot succeed until the quota window resets or billing and quota
    limits change, so this must never be retried automatically.
    """

    code = "provider_quota_exceeded"
    retryable = False


class ProviderRateLimitError(ProviderError):
    """A short-term request or token-rate limit; safe to retry after a delay."""

    code = "provider_rate_limited"
    retryable = True


class ProviderUnsupportedModelError(ProviderError):
    """The configured model id is not available to this account."""

    code = "provider_model_unavailable"
    retryable = False
    action_required = "Point MODEL_NAME at a model id your provider account can serve, then retry."


class ProviderTimeoutError(ProviderError):
    """The provider did not answer inside the configured timeout."""

    code = "provider_timeout"
    retryable = True


class ProviderUpstreamError(ProviderError):
    """Transient provider-side failure such as a 5xx response or reset connection."""

    code = "provider_upstream_failure"
    retryable = True


class ProviderResponseError(ProviderError):
    """The provider answered, but not with usable structured content."""

    code = "provider_malformed_response"
    retryable = False


class ProviderContentBlockedError(ProviderError):
    """The provider's safety filters stopped the response."""

    code = "provider_content_blocked"
    retryable = False
    action_required = "Rewrite the bug report without disallowed content, then retry."


class ProviderRequestBudgetError(ProviderQuotaExhaustedError):
    """The local per-day request budget was reached before the provider was called."""

    code = "provider_request_budget_exhausted"
    action_required = "Raise MODEL_DAILY_REQUEST_BUDGET or wait for the next UTC day, then retry."


def redact_secrets(text: object, secrets: list[str] | tuple[str, ...]) -> str:
    """Return ``text`` with every known secret replaced by a redaction marker."""
    rendered = str(text)
    for secret in secrets:
        if secret and len(secret) >= 6:
            rendered = rendered.replace(secret, REDACTED)
    return rendered


_SECRET_QUERY_RE = re.compile(
    r"([?&](?:key|api[_-]?key|access_token|token)=)[^&\s]+", re.IGNORECASE
)


def sanitize_message(text: object, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Redact secrets and credential-bearing query strings from provider text."""
    return _SECRET_QUERY_RE.sub(r"\1" + REDACTED, redact_secrets(text, secrets))
