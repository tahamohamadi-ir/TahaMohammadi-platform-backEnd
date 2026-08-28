"""Shared security primitives for the custom admin API (ADR-0026, ADM-1).

Same-origin session auth + CSRF + TOTP (django-otp) building blocks shared by
the auth API and the content admin API. Kept in one module so every admin
router enforces the identical security baseline.

Since AB-07 this module is also the SINGLE registry for the admin API's
stable error-code strings, the shared If-Match (optimistic-locking) helpers,
the audit-row writer, and the graph related-record family table. Feature
modules import tokens/helpers from here instead of redeclaring them; the
strings are wire contract and must never be renamed.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from django.http import JsonResponse
from django.middleware.csrf import InvalidTokenFormat, _unmask_cipher_token

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

# ---------------------------------------------------------------------------
# Stable error-code registry (wire contract - declare once, never rename).
# ---------------------------------------------------------------------------

# Security baseline (raised by the guards in this module).
AUTH_REQUIRED = "AUTH_REQUIRED"
FORBIDDEN = "FORBIDDEN"
OTP_REQUIRED = "OTP_REQUIRED"
CSRF_FAILED = "CSRF_FAILED"

# Shared ProblemDetails codes.
VALIDATION = "VALIDATION"
NOT_FOUND = "NOT_FOUND"
PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
STALE_REVISION = "STALE_REVISION"

# Home composition (AB-02).
UNKNOWN_KEY = "UNKNOWN_KEY"
DUPLICATE_KEY = "DUPLICATE_KEY"
DUPLICATE_ORDER = "DUPLICATE_ORDER"
BAD_ENUM = "BAD_ENUM"
TOO_LONG = "TOO_LONG"

# Timeline records (AB-03).
BAD_TYPE = "BAD_TYPE"
INVALID_DETAIL_URL = "INVALID_DETAIL_URL"
BAD_WEIGHT = "BAD_WEIGHT"
UNKNOWN_ID = "UNKNOWN_ID"

# Media presentation (AB-04).
UNKNOWN_FIELD = "UNKNOWN_FIELD"
OUT_OF_RANGE = "OUT_OF_RANGE"
UNKNOWN_LICENSE = "UNKNOWN_LICENSE"

# Graph authoring (AB-05).
IMMUTABLE_ACTIVE = "IMMUTABLE_ACTIVE"
ALREADY_ACTIVE = "ALREADY_ACTIVE"
VALIDATION_BLOCKED = "VALIDATION_BLOCKED"
UNKNOWN_EDGE_ENDPOINT = "UNKNOWN_EDGE_ENDPOINT"
DUPLICATE_RELATED = "DUPLICATE_RELATED"
UNKNOWN_GROUP_MEMBER = "UNKNOWN_GROUP_MEMBER"
DUPLICATE_GROUP_MEMBER = "DUPLICATE_GROUP_MEMBER"


class AdminError(Exception):
    """Structured API error carrying status + Problem-Details-style body."""

    def __init__(self, status: int, code: str, message: str, fields: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields or {}


def _api_error_handler(request, exc):
    if isinstance(exc, AdminError):
        payload: dict = {"code": exc.code, "message": exc.message}
        if exc.fields:
            payload["fields"] = exc.fields
        return JsonResponse(payload, status=exc.status)
    return None


def _client_ip(request) -> str:
    return (request.META.get("REMOTE_ADDR") or "unknown")[:45]


def _check_csrf(request) -> None:
    """Verify the same-origin CSRF token (ninja views are csrf_exempt at the
    Django middleware level, so we enforce CSRF explicitly for unsafe methods).

    Mirrors Django's CsrfViewMiddleware token check: unmask the header token and
    compare it constant-time with the csrftoken cookie.
    """
    header = request.META.get("HTTP_X_CSRFTOKEN", "")
    cookie = request.COOKIES.get("csrftoken", "")
    try:
        secret = _unmask_cipher_token(header)
    except InvalidTokenFormat:
        secret = ""
    if not secret or not cookie or not secrets.compare_digest(secret, cookie):
        raise AdminError(403, CSRF_FAILED, "CSRF token missing or invalid.")


def _require_staff_session(request) -> None:
    """Authenticated + staff guard (used by ``auth/me`` and login-less reads)."""
    if not request.user.is_authenticated:
        raise AdminError(401, AUTH_REQUIRED, "Authentication is required.")
    if not request.user.is_staff:
        raise AdminError(403, FORBIDDEN, "Staff access is required.")


def _require_admin_otp(request) -> None:
    """Authenticated + staff + verified OTP session guard for protected endpoints."""
    _require_staff_session(request)
    if getattr(request.user, "otp_device", None) is None:
        raise AdminError(403, OTP_REQUIRED, "A verified TOTP session is required.")


def _parse_positive_int(
    request, name: str, raw: str | None, default: int, max_value: int
) -> int:
    """Parse an integer query param; invalid/out-of-range returns 400 VALIDATION."""
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise AdminError(400, VALIDATION, f"Invalid {name}.") from None
    if value < 1 or value > max_value:
        raise AdminError(
            400, VALIDATION, f"{name} must be between 1 and {max_value}."
        )
    return value


# ---------------------------------------------------------------------------
# If-Match (optimistic locking) helpers - one copy for every AB module.
# ---------------------------------------------------------------------------


def _format_revision(value: datetime) -> str:
    """ECMA-262 style ISO string at millisecond precision (JS Date safety)."""
    text = value.isoformat()
    if value.microsecond:
        text = text[:23] + text[26:]
    if text.endswith("+00:00"):
        text = text.removesuffix("+00:00") + "Z"
    return text


def _parse_revision(header: str | None) -> datetime | None:
    raw = (header or "").strip().strip('"')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _revisions_match(expected: datetime, current: datetime) -> bool:
    """Compare both sides at millisecond precision (JS Date round-trip safety)."""
    try:
        expected_ms = expected.astimezone(UTC).replace(
            microsecond=(expected.microsecond // 1000) * 1000
        )
        current_ms = current.astimezone(UTC).replace(
            microsecond=(current.microsecond // 1000) * 1000
        )
    except (TypeError, ValueError):
        return False
    return expected_ms == current_ms


def _require_if_match(
    request,
    *,
    current: datetime | None,
    missing_message: str,
    stale_message: str,
) -> None:
    """Shared If-Match gate: 428 PRECONDITION_REQUIRED when the header is
    missing, 409 STALE_REVISION on revision mismatch.

    ``current`` is the resource revision (latest ``updated_at``). ``None``
    means "no rows yet", in which case only the literal empty revision
    (``If-Match: ""``) passes - the home-composition convention (AB-02).
    """
    header = request.headers.get("If-Match")
    if header is None:
        raise AdminError(428, PRECONDITION_REQUIRED, missing_message)
    raw = header.strip().strip('"')
    expected = _parse_revision(raw)
    if current is None:
        if expected is None and raw == "":
            return
        raise AdminError(409, STALE_REVISION, stale_message)
    if expected is None or not _revisions_match(expected, current):
        raise AdminError(409, STALE_REVISION, stale_message)


# ---------------------------------------------------------------------------
# Audit-row writer.
# ---------------------------------------------------------------------------


def _audit_log(
    request, *, action: str, model_name: str, object_id: str, detail: str
) -> None:
    """Single audit-row writer shared by every admin mutating endpoint."""
    AuditLog.objects.create(
        user=request.user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        ip=_client_ip(request),
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Graph related-record families (AB-05/AB-07).
# ---------------------------------------------------------------------------

# SYNC-GUARD: the related-record family table. BK-05 (apps/api/api.py,
# ``public_graph_payload``) derives the public family string as
# ``content_type.model`` (lowercase model name) at read time and composes edge
# ids as ``{source}->{target}:{relationType}``; it does not export a reusable
# helper, so the forward table (family -> model) lives here with the SAME
# lowercase keys the public read serves (one payload everywhere). When BK-05
# exports its mapping helper, import it here and delete this copy.
GRAPH_RELATED_FAMILIES: dict[str, type] = {
    "landing": Landing,
    "profile": Profile,
    "article": Article,
    "series": Series,
    "researchtopic": ResearchTopic,
    "researchstatement": ResearchStatement,
    "project": Project,
    "publication": Publication,
    "book": Book,
    "talk": Talk,
    "download": Download,
}


def _published_related_exists(family: str, record_id: str) -> bool:
    """Resolver for validate_graph_payload: published existence per family."""
    model = GRAPH_RELATED_FAMILIES.get(family)
    if model is None:
        return False
    try:
        pk = int(record_id)
    except (TypeError, ValueError):
        return False
    public = getattr(model.objects, "public", None)
    if public is None:
        return False
    return public().filter(pk=pk).exists()
