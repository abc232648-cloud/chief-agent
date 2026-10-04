class GatewayError(Exception):
    """Base gateway error."""

class ProviderUnavailable(GatewayError):
    """Provider cannot currently serve the request."""

class PaidRouteBlocked(GatewayError):
    """A configuration would allow a paid route."""

class InvalidProviderResponse(GatewayError):
    """Provider returned an unusable response."""
