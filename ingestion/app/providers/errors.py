class ProviderError(RuntimeError):
    """A source request or response could not be processed."""


class ProviderResponseError(ProviderError):
    """A provider returned a response that violates its expected format."""
