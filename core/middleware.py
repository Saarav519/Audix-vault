from django.conf import settings
from django.utils.functional import SimpleLazyObject

from core.scoping import scope_for


class PortalContextMiddleware:
    """Attach request.portal: the client the current viewer may read."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.portal = SimpleLazyObject(lambda: scope_for(request))
        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        storage = " ".join(settings.STORAGE_ORIGINS)
        self.policy = "; ".join([
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            f"img-src 'self' data: blob: {storage}".strip(),
            f"connect-src 'self' {storage}".strip(),
            f"frame-src 'self' {storage}".strip(),
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
        ])

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.policy)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response
