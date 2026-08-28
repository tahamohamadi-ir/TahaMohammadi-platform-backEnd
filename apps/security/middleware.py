"""Admin-security middleware — lightweight audit logging and login rate limiting (P3)."""

from django.core.cache import cache
from django.http import HttpResponse

from apps.security.models import AuditLog

LOGIN_PATH = "/staff/login/"
STAFF_PREFIX = "/staff/"
SKIP_PREFIXES = ("/health/", "/static/", "/media/")
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 300
NOINDEX_PREFIXES = ("/admin/", "/staff/", "/api/", "/rebuild-trigger/")
PREVIEW_PREFIXES = ("/staff/preview/", "/preview/share/")
ADMIN_OPENAPI_DOCS_PREFIX = "/api/v1/admin/docs"
ADMIN_OPENAPI_SCHEMA_PATH = "/api/v1/admin/openapi.json"
PREVIEW_ROBOTS = "noindex, nofollow, noarchive"
DEFAULT_ROBOTS = "noindex, nofollow"
PREVIEW_CACHE_CONTROL = "no-store"


def is_admin_openapi_path(path: str) -> bool:
    """True for staff-gated Ninja Swagger UI and OpenAPI schema under the admin API."""
    return path == ADMIN_OPENAPI_SCHEMA_PATH or path.startswith(ADMIN_OPENAPI_DOCS_PREFIX)


class AdminOpenAPIGateMiddleware:
    """Return 404 (not redirect) for unauthenticated access to admin OpenAPI docs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_admin_openapi_path(request.path) and not self._allowed(request):
            return HttpResponse(status=404)
        return self.get_response(request)

    @staticmethod
    def _allowed(request) -> bool:
        if not request.user.is_authenticated or not request.user.is_staff:
            return False
        return getattr(request.user, "otp_device", None) is not None


class AuditMiddleware:
    """Record security-relevant events into AuditLog, never touching request bodies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method != "POST":
            return response
        path = request.path
        if path.startswith(SKIP_PREFIXES):
            return response
        if path == LOGIN_PATH:
            self._record_login(request, response)
        elif path.startswith(STAFF_PREFIX) and request.user.is_authenticated:
            self._record_mutation(request, response)
        return response

    def _record_login(self, request, response):
        if request.user.is_authenticated:
            action = "login.success"
            user = request.user
            object_id = str(request.user.pk)
        elif response.status_code == 429:
            action = "login.blocked"
            user = None
            object_id = ""
        else:
            action = "login.failed"
            user = None
            object_id = ""
        AuditLog.objects.create(
            user=user,
            action=action,
            model_name="user",
            object_id=object_id,
            ip=self._client_ip(request),
            detail=f"{request.method} {request.path} -> {response.status_code}",
        )

    def _record_mutation(self, request, response):
        segments = request.path[len(STAFF_PREFIX) :].strip("/").split("/")
        model_name = segments[0] if segments else ""
        object_id = next((segment for segment in segments[1:] if segment.isdigit()), "")
        AuditLog.objects.create(
            user=request.user,
            action="admin.mutation",
            model_name=model_name,
            object_id=object_id,
            ip=self._client_ip(request),
            detail=f"{request.method} {request.path} -> {response.status_code}",
        )

    @staticmethod
    def _client_ip(request):
        return (request.META.get("REMOTE_ADDR") or "")[:45]


class LoginRateLimitMiddleware:
    """Cache-backed per-IP limit on staff login posts; the limit+1th attempt gets a 429."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not (request.method == "POST" and request.path == LOGIN_PATH):
            return self.get_response(request)
        cache_key = self._cache_key(request)
        attempts = cache.get(cache_key, 0)
        if attempts >= LOGIN_RATE_LIMIT:
            return HttpResponse(
                "Too many login attempts. Please try again later.", status=429
            )
        response = self.get_response(request)
        if request.user.is_authenticated:
            cache.delete(cache_key)
        else:
            cache.set(cache_key, attempts + 1, LOGIN_RATE_WINDOW_SECONDS)
        return response

    @staticmethod
    def _cache_key(request):
        ip = request.META.get("REMOTE_ADDR", "unknown")
        return f"security:login-limit:{ip}"


class NoIndexMiddleware:
    """Keep machine-facing paths out of search indexes via X-Robots-Tag.

    Staff preview under ``/staff/preview/`` and public share tokens under
    ``/preview/share/`` also get ``noarchive`` and ``Cache-Control: no-store``
    (P3-07 / ADR-0022 / DEFER-0016).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path
        if path.startswith(PREVIEW_PREFIXES):
            response.headers["X-Robots-Tag"] = PREVIEW_ROBOTS
            response.headers["Cache-Control"] = PREVIEW_CACHE_CONTROL
        elif is_admin_openapi_path(path):
            response.headers["X-Robots-Tag"] = DEFAULT_ROBOTS
            response.headers["Cache-Control"] = PREVIEW_CACHE_CONTROL
        elif path.startswith(NOINDEX_PREFIXES):
            response.headers["X-Robots-Tag"] = DEFAULT_ROBOTS
        return response
