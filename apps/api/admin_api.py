"""Custom admin API (django-ninja) — ``/api/v1/admin/*`` (ADR-0026, ADM-0/ADM-1).

Same-origin session auth + CSRF + TOTP (django-otp), reusing the existing
security baseline (``AuditLog``, login rate limit, noindex). The public
read-only ``/api/`` and the Astro public site are untouched.

Endpoints:
- ``auth/csrf``    GET   — ensure the CSRF cookie is set and return its token.
- ``auth/login``   POST  — email + password (+ OTP/recovery code when enrolled).
- ``auth/logout``  POST  — end the admin session.
- ``auth/me``      GET   — current admin user (authenticated + staff).
- ``dashboard/summary`` GET — action-oriented content counts (staff + OTP).

Errors follow the Problem-Details-style contract:
``{"code": str, "message": str, "fields"?: {field: [messages]}}``
Never include secrets, hashes, or request bodies.
"""

from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.middleware.csrf import get_token
from django_otp import DEVICE_ID_SESSION_KEY, user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice
from ninja import Field, NinjaAPI, Schema

from apps.api.admin_common import (
    AdminError,
    _api_error_handler,
    _check_csrf,
    _client_ip,
    _require_admin_otp,
    _require_staff_session,
)
from apps.content.feature_flags import enabled_feature_flags
from apps.content.models import (
    Article,
    Book,
    Download,
    Landing,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
)
from apps.security.models import AuditLog
from apps.security.recovery import consume_recovery_code

LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 300
_RATE_KEY = "security:login-limit-api"


admin_api = NinjaAPI(
    title="Taha Custom Admin API",
    version="0.1.0",
    urls_namespace="admin_api",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
admin_api.exception_handler(AdminError)(_api_error_handler)


class LoginIn(Schema):
    """Admin login request — email (username), password, optional OTP/recovery code."""

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    otpToken: str | None = Field(default=None, max_length=40)


class AdminUserOut(Schema):
    """Safe admin identity — never includes session, CSRF, or secrets."""

    id: int
    email: str
    displayName: str
    isStaff: bool
    mfaEnrolled: bool
    otpVerified: bool
    featureFlags: dict[str, bool] = Field(default_factory=dict)


class DashboardSummaryOut(Schema):
    """Action-oriented counts for the admin dashboard."""

    contentCounts: dict[str, int]
    drafts: int
    published: int


_CONTENT_MODELS = {
    "landing": Landing,
    "profile": Profile,
    "article": Article,
    "series": Series,
    "researchTopic": ResearchTopic,
    "researchStatement": ResearchStatement,
    "project": Project,
    "publication": Publication,
    "book": Book,
    "talk": Talk,
    "download": Download,
}


def _admin_logout_audit(request, user) -> None:
    AuditLog.objects.create(
        user=user,
        action="admin.logout",
        model_name="user",
        object_id=str(user.pk) if user is not None else "",
        ip=_client_ip(request),
        detail="POST /api/v1/admin/auth/logout -> 200",
    )


def _audit(request, *, action: str, user=None, status: int) -> None:
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name="user",
        object_id=str(user.pk) if user is not None else "",
        ip=_client_ip(request),
        detail=f"POST /api/v1/admin/auth/login -> {status}",
    )


def _is_rate_limited(request) -> bool:
    key = f"{_RATE_KEY}:{_client_ip(request)}"
    return cache.get(key, 0) >= LOGIN_RATE_LIMIT


def _bump_rate_limit(request) -> None:
    key = f"{_RATE_KEY}:{_client_ip(request)}"
    cache.set(key, cache.get(key, 0) + 1, LOGIN_RATE_WINDOW_SECONDS)


def _clear_rate_limit(request) -> None:
    cache.delete(f"{_RATE_KEY}:{_client_ip(request)}")


def _serialize_user(user, otp_verified: bool | None = None) -> AdminUserOut:
    if otp_verified is None:
        otp_verified = getattr(user, "otp_device", None) is not None
    return AdminUserOut(
        id=user.pk,
        email=user.email,
        displayName=user.get_full_name() or user.email,
        isStaff=user.is_staff,
        mfaEnrolled=user_has_device(user, confirmed=True),
        otpVerified=otp_verified,
        featureFlags=enabled_feature_flags(),
    )


@admin_api.get(
    "/auth/csrf",
    summary="Return the CSRF token and ensure the csrftoken cookie is set.",
)
def auth_csrf(request):
    return {"csrfToken": get_token(request)}


@admin_api.post("/auth/login", response=AdminUserOut, summary="Admin login.")
def auth_login(request, payload: LoginIn):
    _check_csrf(request)
    if _is_rate_limited(request):
        _audit(request, action="login.blocked", status=429)
        raise AdminError(
            429, "RATE_LIMITED", "Too many login attempts. Please try again later."
        )

    user = authenticate(
        request,
        username=payload.email.strip(),
        password=payload.password,
    )
    if user is None or not user.is_staff:
        _bump_rate_limit(request)
        _audit(request, action="login.failed", status=401)
        raise AdminError(401, "AUTH_FAILED", "Invalid credentials.")

    token = (payload.otpToken or "").strip()
    otp_ok = False
    if user_has_device(user, confirmed=True):
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        verified = device is not None and device.verify_token(token)
        otp_ok = verified or consume_recovery_code(user, token, ip=_client_ip(request))
        if not otp_ok:
            _bump_rate_limit(request)
            _audit(request, action="login.failed", user=user, status=401)
            raise AdminError(401, "AUTH_FAILED", "Invalid credentials.")
        if device is None:
            device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device is not None:
            login(request, user)
            request.session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        else:
            _bump_rate_limit(request)
            _audit(request, action="login.failed", user=user, status=401)
            raise AdminError(401, "AUTH_FAILED", "Invalid credentials.")
    else:
        login(request, user)

    _clear_rate_limit(request)
    _audit(request, action="login.success", user=user, status=200)
    return _serialize_user(request.user, otp_verified=otp_ok)


@admin_api.post("/auth/logout", summary="End the admin session.")
def auth_logout(request):
    _check_csrf(request)
    user = request.user if request.user.is_authenticated else None
    logout(request)
    _admin_logout_audit(request, user)
    return {"ok": True}


@admin_api.get("/auth/me", response=AdminUserOut, summary="Current admin user.")
def auth_me(request):
    _require_staff_session(request)
    return _serialize_user(request.user)


@admin_api.get(
    "/dashboard/summary",
    response=DashboardSummaryOut,
    summary="Action-oriented content counts.",
)
def dashboard_summary(request):
    _require_admin_otp(request)
    counts: dict[str, int] = {}
    for key, model in _CONTENT_MODELS.items():
        counts[key] = model.objects.count()
    drafts = sum(model.objects.filter(status="draft").count() for model in _CONTENT_MODELS.values())
    published = sum(
        model.objects.filter(status="published").count()
        for model in _CONTENT_MODELS.values()
    )
    return DashboardSummaryOut(contentCounts=counts, drafts=drafts, published=published)


from apps.api.admin_content import content_router  # noqa: E402

admin_api.add_router("/content", content_router)

from apps.api.admin_media import media_router  # noqa: E402

admin_api.add_router("/media", media_router)

from apps.api.admin_composition import composition_router  # noqa: E402

admin_api.add_router("/composition", composition_router)

from apps.api.admin_health import health_router  # noqa: E402

admin_api.add_router("/overview", health_router)

from apps.api.admin_siteconfig import siteconfig_router  # noqa: E402

admin_api.add_router("", siteconfig_router)

from apps.api.admin_mfa import mfa_router  # noqa: E402

admin_api.add_router("/auth/mfa", mfa_router)

# [ADMIN-API] Track AB begin - home composition router (AB-00/AB-02).
# Registration mechanism: this add_router sequence (the existing surface);
# no separate admin_urls.py aggregation module is warranted.
from apps.api.admin_home import home_router  # noqa: E402

admin_api.add_router("/home-modules", home_router)

from apps.api.admin_timeline import timeline_router  # noqa: E402

admin_api.add_router("/timeline", timeline_router)

# AB-04: direct root-group registration (not add_router) so the literal
# media/licenses path resolves before the legacy untyped media/<media_id>
# route; see apps/api/admin_media_ext.py module docstring.
from apps.api.admin_media_ext import register_media_ext  # noqa: E402

register_media_ext(admin_api)

# AB-05/AB-06: graph authoring router + pure validator service.
from apps.api.admin_graph import graph_router  # noqa: E402

admin_api.add_router("/graph", graph_router)
# [ADMIN-API] Track AB end
