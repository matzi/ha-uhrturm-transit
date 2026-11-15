"""TRIAS client exceptions."""


class TriasError(Exception):
    """Base class for TRIAS errors."""


class TriasConnectionError(TriasError):
    """Endpoint not reachable, timeout or HTTP error."""


class TriasAuthError(TriasError):
    """Request rejected because of missing/invalid credentials."""


class TriasParseError(TriasError):
    """Response is not valid TRIAS XML."""


class TriasApiError(TriasError):
    """Valid TRIAS response that reports an error and contains no results."""

    def __init__(self, messages: list[str]) -> None:
        """Initialize."""
        super().__init__("; ".join(messages) or "unknown TRIAS error")
        self.messages = messages
