class GatewayError(Exception):
    """Base gateway error."""

class ProviderUnavailable(GatewayError):
    """Provider cannot currently serve the request."""
    def __init__(self, message='', *, status_code=None):
        super().__init__(message)
        self.status_code=status_code if type(status_code) is int and 100<=status_code<=599 else None

class PaidRouteBlocked(GatewayError):
    """A configuration would allow a paid route."""

class InvalidProviderResponse(GatewayError):
    """Provider returned an unusable response."""
