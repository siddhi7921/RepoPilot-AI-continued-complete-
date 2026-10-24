"""Retry policy for model-provider calls.

Delays use exponential backoff with bounded jitter, and a provider-supplied
delay (``Retry-After`` or Google ``RetryInfo.retryDelay``) always wins when it is
available and inside the configured ceiling.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 20.0
DEFAULT_JITTER = 0.35
DEFAULT_MAX_PROVIDER_DELAY = 30.0
def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry behaviour for one provider client."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay: float = DEFAULT_BASE_DELAY
    max_delay: float = DEFAULT_MAX_DELAY
    jitter: float = DEFAULT_JITTER
    max_provider_delay: float = DEFAULT_MAX_PROVIDER_DELAY

    @classmethod
    def from_env(cls) -> "RetryPolicy":
        return cls(
            max_attempts=_env_int("MODEL_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, minimum=1, maximum=8),
            base_delay=_env_float("MODEL_RETRY_BASE_SECONDS", DEFAULT_BASE_DELAY, minimum=0.0, maximum=60.0),
            max_delay=_env_float("MODEL_RETRY_MAX_SECONDS", DEFAULT_MAX_DELAY, minimum=0.0, maximum=300.0),
            jitter=_env_float("MODEL_RETRY_JITTER", DEFAULT_JITTER, minimum=0.0, maximum=1.0),
            max_provider_delay=_env_float(
                "MODEL_MAX_PROVIDER_RETRY_SECONDS", DEFAULT_MAX_PROVIDER_DELAY, minimum=0.0, maximum=600.0
            ),
        )

    def delay_for(self, attempt: int, retry_after: float | None = None, *, rand: random.Random | None = None) -> float:
        """Seconds to wait after ``attempt`` failed (1-based).

        A provider-supplied delay is honoured up to ``max_provider_delay``.
        Otherwise the delay grows exponentially, capped at ``max_delay``, with up
        to ``jitter`` proportional randomness so concurrent clients do not retry
        in lockstep.
        """
        if retry_after is not None and retry_after >= 0 and self.max_provider_delay > 0:
            return min(float(retry_after), self.max_provider_delay)
        exponent = max(0, attempt - 1)
        capped = min(self.base_delay * (2 ** exponent), self.max_delay)
        if capped <= 0:
            return 0.0
        source = rand or random
        spread = capped * self.jitter
        return max(0.0, capped + source.uniform(-spread, spread))
