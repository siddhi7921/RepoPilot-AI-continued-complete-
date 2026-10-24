"""Provider selection, validation, and secret-free configuration reporting."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlparse

from .errors import ProviderConfigurationError, ProviderError
from .retry import RetryPolicy

DISABLED = "disabled"

# api_style values understood by providers.transport.
OPENAI_STYLE = "openai_compatible"
GEMINI_STYLE = "google_gemini"
DISABLED_STYLE = "disabled"

ENV_PROVIDER = "MODEL_PROVIDER"
ENV_API_KEY = "MODEL_API_KEY"
ENV_BASE_URL = "MODEL_BASE_URL"
ENV_MODEL_NAME = "MODEL_NAME"

SERVER_SIDE_LOCATIONS = (
    "server-side environment only: Render service env vars, the docker-compose .env file, "
    "or your local shell. Never frontend code, logs, or repository files."
)


@dataclass(frozen=True)
class ProviderPreset:
    """Everything needed to talk to one provider family by default."""

    name: str
    api_style: str
    default_base_url: str
    default_model: str
    requires_key: bool
    model_list_path: str
    docs_url: str
    notes: str = ""


# Only presets whose default base URL and model are published by the provider
# itself. Anything else must be supplied explicitly as MODEL_PROVIDER=custom.
PRESETS: dict[str, ProviderPreset] = {
    DISABLED: ProviderPreset(
        name=DISABLED,
        api_style=DISABLED_STYLE,
        default_base_url="",
        default_model="",
        requires_key=False,
        model_list_path="",
        docs_url="",
        notes="No model call is made. Ingestion, retrieval and verification still run.",
    ),
    OPENAI_STYLE: ProviderPreset(
        name=OPENAI_STYLE,
        api_style=OPENAI_STYLE,
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        requires_key=True,
        model_list_path="/models",
        docs_url="https://platform.openai.com/docs/api-reference",
    ),
    "gemini": ProviderPreset(
        name="gemini",
        api_style=GEMINI_STYLE,
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        default_model="gemini-2.5-flash",
        requires_key=True,
        model_list_path="/models",
        docs_url="https://ai.google.dev/gemini-api/docs",
        notes=(
            "Google Generative Language API (generateContent). Gemini API free-tier quota is "
            "separate from a Google AI Pro consumer subscription; the subscription does not add API quota."
        ),
    ),
    "groq": ProviderPreset(
        name="groq",
        api_style=OPENAI_STYLE,
        default_base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        requires_key=True,
        model_list_path="/models",
        docs_url="https://console.groq.com/docs/openai",
    ),
    "openrouter": ProviderPreset(
        name="openrouter",
        api_style=OPENAI_STYLE,
        default_base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini",
        requires_key=True,
        model_list_path="/models",
        docs_url="https://openrouter.ai/docs",
    ),
    "deepseek": ProviderPreset(
        name="deepseek",
        api_style=OPENAI_STYLE,
        default_base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        requires_key=True,
        model_list_path="/models",
        docs_url="https://api-docs.deepseek.com",
    ),
    "mistral": ProviderPreset(
        name="mistral",
        api_style=OPENAI_STYLE,
        default_base_url="https://api.mistral.ai/v1",
        default_model="mistral-small-latest",
        requires_key=True,
        model_list_path="/models",
        docs_url="https://docs.mistral.ai",
    ),
    "ollama": ProviderPreset(
        name="ollama",
        api_style=OPENAI_STYLE,
        default_base_url="http://127.0.0.1:11434/v1",
        default_model="llama3.1",
        requires_key=False,
        model_list_path="/models",
        docs_url="https://github.com/ollama/ollama/blob/main/docs/openai.md",
        notes="Loopback HTTP is permitted for this local runtime only.",
    ),
    "custom": ProviderPreset(
        name="custom",
        api_style=OPENAI_STYLE,
        default_base_url="",
        default_model="",
        requires_key=True,
        model_list_path="/models",
        docs_url="",
        notes="Set MODEL_BASE_URL and MODEL_NAME explicitly.",
    ),
}

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def supported_providers() -> list[str]:
    return sorted(PRESETS)
def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class ProviderSettings:
    """Resolved, validated provider configuration for one investigation run."""

    provider: str
    api_style: str
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    max_output_tokens: int = 3000
    max_prompt_chars: int = 24000
    max_evidence_sections: int = 8
    max_concurrency: int = 2
    daily_request_budget: int = 0
    retry: RetryPolicy = field(default_factory=RetryPolicy.from_env)

    @property
    def base_host(self) -> str:
        return urlparse(self.base_url).hostname or ""

    def redaction_secrets(self) -> list[str]:
        return [self.api_key] if self.api_key else []

    def sanitized_endpoint(self) -> str:
        """Endpoint safe for logs and API responses: it never includes the key."""
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"

    def public_summary(self) -> dict:
        return {
            "provider": self.provider,
            "api_style": self.api_style,
            "model": self.model,
            "base_url_host": self.base_host,
            "endpoint": self.sanitized_endpoint(),
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "max_prompt_chars": self.max_prompt_chars,
            "max_evidence_sections": self.max_evidence_sections,
            "max_attempts": self.retry.max_attempts,
            "api_key_configured": bool(self.api_key),
        }


def _validate_base_url(base_url: str, *, allow_loopback_http: bool) -> str | None:
    """Return a problem description, or ``None`` when the URL is acceptable."""
    if not base_url:
        return "MODEL_BASE_URL is empty."
    parsed = urlparse(base_url)
    if not parsed.hostname or parsed.username or parsed.password:
        return "MODEL_BASE_URL must be a URL with a host and no embedded credentials."
    if parsed.query or parsed.fragment:
        return "MODEL_BASE_URL must not contain a query string or fragment."
    if parsed.scheme == "https":
        return None
    if parsed.scheme == "http" and allow_loopback_http and parsed.hostname.lower() in LOOPBACK_HOSTS:
        return None
    return "MODEL_BASE_URL must use HTTPS (loopback HTTP is allowed only for a local runtime)."


def _missing_entry(name: str, purpose: str) -> dict:
    return {"name": name, "purpose": purpose, "where": SERVER_SIDE_LOCATIONS}


def resolve_settings(env: Mapping[str, str] | None = None) -> tuple[ProviderSettings | None, dict]:
    """Resolve provider settings plus a secret-free configuration report.

    Returns ``(settings, report)``. ``settings`` is ``None`` whenever the
    provider is disabled or its configuration is unusable; ``report`` always
    describes what is configured, what is missing, and what to do next.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    provider = (source.get(ENV_PROVIDER) or "").strip().lower() or DISABLED
    preset = PRESETS.get(provider)

    missing: list[dict] = []
    invalid: list[dict] = []
    warnings: list[str] = []

    if preset is None:
        invalid.append({
            "name": ENV_PROVIDER,
            "reason": f"Unknown provider {provider!r}.",
            "supported": supported_providers(),
        })
        return None, _report(
            provider=provider, api_style=DISABLED_STYLE, model="", base_url_host="",
            configured=False, missing=missing, invalid=invalid, warnings=warnings,
            next_steps=[f"Set {ENV_PROVIDER} to one of: {', '.join(supported_providers())}."],
        )

    if preset.api_style == DISABLED_STYLE:
        return None, _report(
            provider=provider, api_style=DISABLED_STYLE, model="", base_url_host="",
            configured=False, missing=missing, invalid=invalid, warnings=warnings,
            next_steps=[
                f"Set {ENV_PROVIDER}=gemini (Google AI Studio key) or {ENV_PROVIDER}=openai, then set {ENV_API_KEY}.",
                f"Leave {ENV_PROVIDER}=disabled to keep ingestion and retrieval without AI conclusions.",
            ],
            preset_notes=preset.notes,
        )

    api_key = (source.get(ENV_API_KEY) or "").strip()
    if preset.requires_key and not api_key:
        missing.append(_missing_entry(ENV_API_KEY, f"Credential for the {provider} provider."))

    raw_base = (source.get(ENV_BASE_URL) or "").strip()
    base_url = (raw_base or preset.default_base_url).rstrip("/")
    hostname = (urlparse(base_url).hostname or "").lower()
    allow_loopback_http = provider == "ollama" or hostname in LOOPBACK_HOSTS
    base_problem = _validate_base_url(base_url, allow_loopback_http=allow_loopback_http)
    if base_problem:
        invalid.append({"name": ENV_BASE_URL, "reason": base_problem})

    model = (source.get(ENV_MODEL_NAME) or "").strip() or preset.default_model
    if not model:
        missing.append(_missing_entry(ENV_MODEL_NAME, "Model identifier to request from the provider."))
    elif not MODEL_ID_RE.match(model):
        invalid.append({
            "name": ENV_MODEL_NAME,
            "reason": "Model identifier contains characters no supported provider uses.",
        })

    if preset.api_style == GEMINI_STYLE and "/openai" in base_url:
        warnings.append(
            "MODEL_PROVIDER=gemini uses the Generative Language API root "
            "(https://generativelanguage.googleapis.com/v1beta), not the OpenAI-compatibility path."
        )

    next_steps: list[str] = []
    if missing or invalid:
        if missing:
            next_steps.append("Set these server-side variables: " + ", ".join(item["name"] for item in missing) + ".")
        if invalid:
            next_steps.append("Correct these server-side variables: " + ", ".join(item["name"] for item in invalid) + ".")
        next_steps.append("Then call POST /api/diagnostics/provider to confirm the credential and model.")
        return None, _report(
            provider=provider, api_style=preset.api_style, model=model,
            base_url_host=urlparse(base_url).hostname or "", configured=False,
            missing=missing, invalid=invalid, warnings=warnings, next_steps=next_steps,
            preset_notes=preset.notes, docs_url=preset.docs_url,
        )

    settings = ProviderSettings(
        provider=provider,
        api_style=preset.api_style,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=_env_float("MODEL_TIMEOUT_SECONDS", 60.0, minimum=1.0, maximum=600.0),
        max_output_tokens=_env_int("MODEL_MAX_OUTPUT_TOKENS", 3000, minimum=128, maximum=32000),
        max_prompt_chars=_env_int("MODEL_MAX_PROMPT_CHARS", 24000, minimum=1000, maximum=200000),
        max_evidence_sections=_env_int("MODEL_MAX_EVIDENCE_SECTIONS", 8, minimum=1, maximum=40),
        max_concurrency=_env_int("MODEL_MAX_CONCURRENCY", 2, minimum=1, maximum=8),
        daily_request_budget=_env_int("MODEL_DAILY_REQUEST_BUDGET", 0, minimum=0, maximum=1000000),
        retry=RetryPolicy.from_env(),
    )
    report = _report(
        provider=provider, api_style=settings.api_style, model=settings.model,
        base_url_host=settings.base_host, configured=True, missing=missing,
        invalid=invalid, warnings=warnings,
        next_steps=["Call POST /api/diagnostics/provider to verify the credential and model with a live check."],
        preset_notes=preset.notes, docs_url=preset.docs_url,
    )
    report["settings"] = settings.public_summary()
    return settings, report
