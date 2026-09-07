"""API endpoints for first-party aggregate analytics (§I07).

Public:
- POST /api/v1/analytics/events (ingest aggregate events)

Admin:
- GET /api/v1/admin/analytics (report received events)
"""

from __future__ import annotations

import datetime
import re
import uuid
from urllib.parse import urlparse

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from ninja import Field, Query, Router, Schema
from ninja.responses import Status

from apps.analytics.models import AggregateEvent
from apps.api.admin_common import (
    VALIDATION,
    AdminError,
    _require_admin_otp,
)

analytics_public_router = Router()
analytics_admin_router = Router()

VALID_EVENTS = {
    "page_view",
    "cv_download",
    "research_profile_download",
    "demo_click",
    "contact_click",
    "contact_submit_success",
}

VALID_LOCALES = {"fa", "en"}

# A09: registered action-target registry. Only cv_download currently has
# shipped sub-targets — the two document slots the public settings endpoint
# actually serves (apps/api/api.py: academic_cv, industry_resume). Every other
# event accepts an empty target only, until its sender packet (PU-21)
# registers real action IDs with backend support. Arbitrary text is never
# accepted, no matter how well-formed it looks.
REGISTERED_ACTION_TARGETS: dict[str, frozenset] = {
    "page_view": frozenset(),
    "cv_download": frozenset({"academic_cv", "industry_resume"}),
    "research_profile_download": frozenset(),
    "demo_click": frozenset(),
    "contact_click": frozenset(),
    "contact_submit_success": frozenset(),
}

PATH_MAX_LENGTH = 512
TARGET_MAX_LENGTH = 64
MAX_BODY_BYTES = 4096
MAX_RANGE_DAYS = 366
RATE_LIMIT_MAX_HITS = 300
RATE_LIMIT_WINDOW = 60

_SEGMENT = r"[^/?#\s]+"
# A09: canonical browser-route structures (PRODUCT-INTERFACES-V2 §I02 and the
# ROUTE-REGISTRY families). No locale prefix inside: the caller supplies it.
# Legacy redirect-only families (writing/teaching/creative), compatibility
# aliases (courses/creative-works), and non-route surfaces (tags/graph/series
# list) are deliberately absent — beacons must report canonical URLs.
_CANONICAL_FAMILY_PATTERNS = [
    r"",
    r"about",
    r"about/" + _SEGMENT,
    r"blog",
    r"blog/" + _SEGMENT,
    r"blog/series/" + _SEGMENT,
    r"research",
    r"research/" + _SEGMENT,
    r"research/statements/" + _SEGMENT,
    r"research/projects",
    r"research/projects/" + _SEGMENT,
    r"research/publications",
    r"research/publications/" + _SEGMENT,
    r"projects",
    r"projects/" + _SEGMENT,
    r"publications",
    r"publications/" + _SEGMENT,
    r"books",
    r"books/" + _SEGMENT,
    r"talks",
    r"talks/" + _SEGMENT,
    r"resources",
    r"resources/" + _SEGMENT,
    r"resources/" + _SEGMENT + r"/file",
    r"education",
    r"education/" + _SEGMENT,
    r"education/" + _SEGMENT + r"/lessons/" + _SEGMENT,
    r"gallery",
    r"gallery/" + _SEGMENT,
    r"collections",
    r"collections/" + _SEGMENT,
    r"cv",
    r"contact",
    r"search",
]
_CANONICAL_ROUTE_RES = [
    re.compile(r"^/(fa|en)/?$")
    if not pattern
    else re.compile(r"^/(fa|en)/" + pattern + r"/?$")
    for pattern in _CANONICAL_FAMILY_PATTERNS
]


def _path_locale(path: str) -> str | None:
    """Locale segment of a canonical path, or None for the neutral gateway."""
    match = re.match(r"^/(fa|en)(/|$)", path)
    return match.group(1) if match else None


def _canonicalize_page_path(raw: str) -> str | None:
    """Normalize to canonical form, or None when the path is not canonical.

    Canonical means: leading slash, no query/fragment/whitespace, a structure
    from the registered browser-route table, and a trailing slash (gateway
    root ``/`` excepted). Returns the normalized path for aggregation.
    """
    path = (raw or "").strip()
    if not path.startswith("/") or len(path) > PATH_MAX_LENGTH:
        return None
    if "?" in path or "#" in path or any(c.isspace() for c in path):
        return None
    if "//" in path:
        return None
    if path == "/":
        # Language gateway: locale-neutral, accepted for either locale.
        return "/"
    if path.endswith("/"):
        path = path.rstrip("/") or "/"
    if not any(rx.match(path) for rx in _CANONICAL_ROUTE_RES):
        return None
    if path != "/" and not path.endswith("/"):
        path += "/"
    return path


class EventIngestIn(Schema):
    """Anonymous aggregate event payload (§I07).

    Strict privacy policy:
    extra = 'forbid' rejects any unknown fields (such as email, visitor_id,
    cookie, tokens, full referrers).
    """

    model_config = {"extra": "forbid"}

    event: str
    pagePath: str
    locale: str
    target: str = ""


class EventIngestOut(Schema):
    """Ingest acceptance response."""

    status: str = "accepted"


class AnalyticsRowOut(Schema):
    """Single aggregate event counter row."""

    date: str
    pagePath: str
    locale: str
    event: str
    target: str = ""
    count: int


class AnalyticsReportOut(Schema):
    """Admin received events report payload (§I07)."""

    model_config = {"populate_by_name": True}

    from_: str = Field(serialization_alias="from", validation_alias="from")
    to: str
    timezone: str = "UTC"
    updatedAt: str
    metric: str = "received_events"
    rows: list[AnalyticsRowOut] = Field(default_factory=list)


class ErrorEnvelopeOut(Schema):
    """Normalized error envelope (PRODUCT-INTERFACES-V2 §I08, ERROR-CONTRACT)."""

    code: str
    message: str
    field_errors: dict[str, list[str]] = Field(default_factory=dict)
    request_id: str


def _make_request_id(request) -> str:
    return getattr(request, "request_id", None) or str(uuid.uuid4())


def _error_response(
    request,
    status: int,
    code: str,
    message: str,
    field_errors: dict[str, list[str]] | None = None,
):
    return Status(
        status,
        ErrorEnvelopeOut(
            code=code,
            message=message,
            field_errors=field_errors or {},
            request_id=_make_request_id(request),
        ),
    )


def analytics_validation_envelope(request, errors: list) -> dict:
    """Build an I08 envelope for schema-shape (422) rejections on this endpoint."""
    field_errors: dict[str, list[str]] = {}
    for err in errors or []:
        loc = err.get("loc") if isinstance(err, dict) else None
        if isinstance(loc, (list, tuple)) and len(loc) > 1:
            key = ".".join(str(part) for part in loc[1:])
        else:
            key = "body"
        message = err.get("msg") if isinstance(err, dict) else None
        field_errors.setdefault(key, []).append(str(message or "Invalid value."))
    return ErrorEnvelopeOut(
        code="INVALID_INPUT",
        message="Request body failed validation.",
        field_errors=field_errors,
        request_id=_make_request_id(request),
    ).dict()


def _same_origin(request) -> bool:
    """Accept absent Origin/Referer (curl/native); reject foreign origins."""
    host = request.get_host()
    for header in ("HTTP_ORIGIN", "HTTP_REFERER"):
        value = request.META.get(header, "")
        if not value:
            continue
        try:
            parsed = urlparse(value)
            if parsed.netloc and parsed.netloc != host:
                return False
        except Exception:
            return False
    return True


def _check_rate_limit(request) -> bool:
    """Per-IP sliding cache rate limit."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else request.META.get("REMOTE_ADDR", "unknown")
    )
    key = f"analytics:ratelimit:{ip}"
    try:
        hits = cache.get(key, 0)
        if hits >= RATE_LIMIT_MAX_HITS:
            return False
        cache.set(key, hits + 1, timeout=RATE_LIMIT_WINDOW)
    except Exception:
        pass
    return True


@analytics_public_router.post(
    "/v1/analytics/events",
    response={
        200: EventIngestOut,
        202: EventIngestOut,
        400: ErrorEnvelopeOut,
        403: ErrorEnvelopeOut,
        413: ErrorEnvelopeOut,
        422: ErrorEnvelopeOut,
        429: ErrorEnvelopeOut,
    },
    summary="Ingest first-party anonymous aggregate event",
)
def ingest_event(request, payload: EventIngestIn):
    """Ingest anonymous first-party event and update daily aggregate counters.

    A09 validation (§I07, fail-closed, I08 envelope on every rejection):

    - Oversized bodies are rejected before anything is stored.
    - Foreign cross-origin posts are rejected.
    - Per-IP rate limits are enforced.
    - ``event`` must be a known enum member; ``locale`` must be fa/en.
    - ``pagePath`` must be a canonical route whose locale matches ``locale``
      (the locale-neutral gateway ``/`` is accepted for either locale).
    - ``target`` must be a registered action ID for the event (page views
      carry no target; only ``cv_download`` has shipped sub-targets).

    Privacy & Security: persists zero visitor identifiers; unknown fields are
    rejected by the schema (422 envelope); failed analytics never block
    navigation or forms.
    """
    raw_body = request.body if hasattr(request, "body") else b""
    if len(raw_body or b"") > MAX_BODY_BYTES:
        return _error_response(
            request,
            413,
            "PAYLOAD_TOO_LARGE",
            f"Request body exceeds the {MAX_BODY_BYTES} byte limit.",
            {"body": [f"Body must be at most {MAX_BODY_BYTES} bytes."]},
        )

    if not _same_origin(request):
        return _error_response(
            request,
            403,
            "FORBIDDEN",
            "Cross-origin request rejected.",
        )

    if not _check_rate_limit(request):
        return _error_response(
            request,
            429,
            "RATE_LIMITED",
            "Rate limit exceeded.",
        )

    if payload.event not in VALID_EVENTS:
        return _error_response(
            request,
            400,
            "INVALID_INPUT",
            f"Invalid event type: {payload.event}",
            {"event": ["Must be one of the registered event names."]},
        )

    if payload.locale not in VALID_LOCALES:
        return _error_response(
            request,
            400,
            "INVALID_INPUT",
            f"Invalid locale: {payload.locale}",
            {"locale": ["Must be 'fa' or 'en'."]},
        )

    canonical_path = _canonicalize_page_path(payload.pagePath)
    if canonical_path is None:
        return _error_response(
            request,
            400,
            "INVALID_INPUT",
            "Invalid canonical pagePath.",
            {"pagePath": ["Must be a canonical site route without query/fragment."]},
        )
    path_locale = _path_locale(canonical_path)
    if path_locale is not None and path_locale != payload.locale:
        return _error_response(
            request,
            400,
            "INVALID_INPUT",
            "pagePath locale does not match locale.",
            {"pagePath": ["Route locale must match the event locale."]},
        )

    target = (payload.target or "").strip()
    allowed_targets = REGISTERED_ACTION_TARGETS.get(payload.event, frozenset())
    if len(target) > TARGET_MAX_LENGTH or (
        target and target not in allowed_targets
    ):
        return _error_response(
            request,
            400,
            "INVALID_INPUT",
            "Invalid target action ID.",
            {"target": ["Must be a registered action ID for the event."]},
        )

    today = timezone.now().date()
    AggregateEvent.record_event(
        date=today,
        locale=payload.locale,
        page_path=canonical_path,
        event=payload.event,
        target=target,
    )
    return EventIngestOut(status="accepted")


@analytics_admin_router.get(
    "",
    response=AnalyticsReportOut,
    summary="Retrieve first-party received events report",
)
def get_analytics_report(
    request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    locale: str | None = Query(None),
) -> AnalyticsReportOut:
    """Admin report of received events aggregate counters (§I07).

    Authentication: Staff session + verified OTP required.
    Window: Maximum 366 days.
    """
    _require_admin_otp(request)

    today = timezone.now().date()

    if to:
        try:
            to_date = datetime.date.fromisoformat(to.strip())
        except ValueError:
            raise AdminError(
                400, VALIDATION, "Invalid 'to' date. Must be ISO YYYY-MM-DD."
            ) from None
    else:
        to_date = today

    if from_:
        try:
            from_date = datetime.date.fromisoformat(from_.strip())
        except ValueError:
            raise AdminError(
                400, VALIDATION, "Invalid 'from' date. Must be ISO YYYY-MM-DD."
            ) from None
    else:
        from_date = to_date - datetime.timedelta(days=30)

    if from_date > to_date:
        raise AdminError(400, VALIDATION, "'from' date cannot be after 'to' date.")

    if (to_date - from_date).days > MAX_RANGE_DAYS:
        raise AdminError(400, VALIDATION, f"Date range exceeds maximum of {MAX_RANGE_DAYS} days.")

    if locale:
        locale = locale.strip()
        if locale not in VALID_LOCALES:
            raise AdminError(400, VALIDATION, f"Invalid locale '{locale}'. Must be 'fa' or 'en'.")

    qs = AggregateEvent.objects.filter(date__gte=from_date, date__lte=to_date)
    if locale:
        qs = qs.filter(locale=locale)
    qs = qs.order_by("date", "page_path", "event", "target")

    rows = [
        {
            "date": item.date.isoformat(),
            "pagePath": item.page_path,
            "locale": item.locale,
            "event": item.event,
            "target": item.target,
            "count": item.count,
        }
        for item in qs
    ]

    return JsonResponse(
        {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "timezone": "UTC",
            "updatedAt": timezone.now().isoformat(),
            "metric": "received_events",
            "rows": rows,
        }
    )
