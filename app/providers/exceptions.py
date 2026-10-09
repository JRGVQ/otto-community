"""Provider-layer exceptions."""
from __future__ import annotations


class ProviderError(Exception):
    """Base class for mail-provider failures."""


class ProviderNotConfigured(ProviderError):
    """Raised when a provider's credentials/SDK are not available."""


class ProviderAuthError(ProviderError):
    """OAuth/token failure — the account must be reconnected."""


class RateLimited(ProviderError):
    """Provider returned a rate-limit response; back off and retry."""
