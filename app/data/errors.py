from __future__ import annotations


class DataProviderError(RuntimeError):
    """Base error for upstream market-data providers."""


class FinMindRequestError(DataProviderError):
    """Raised when FinMind returns HTTP/API errors or malformed payloads."""
