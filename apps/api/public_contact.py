"""Public contact form endpoint (board A10 decision 2026-08-23).

Non-persistent by policy: the message is emailed straight to the owner and
NEVER stored — no model row, no message body in logs or audit. Spam controls
are deliberately cheap and deterministic: honeypot field, same-origin
Origin/Referer check, per-IP cache rate limit, hard length caps.

The public site is static (no-JS), so the footer form POSTs urlencoded fields
and the endpoint answers with a tiny styled HTML page; API clients posting
JSON get JSON back. Both paths behave identically for validation.
"""

from __future__ import annotations

import json
import logging
import re

from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import HttpRequest, HttpResponse
from django.utils.html import escape
from ninja import Router

from apps.security.models import AuditLog
from apps.siteconfig.models import SiteSettings

logger = logging.getLogger(__name__)

contact_router = Router()

EMAIL_MAX = 254
NAME_MAX = 120
MESSAGE_MAX = 4000
RATE_LIMIT_KEY = "contact:{ip}"
RATE_LIMIT_HITS = 5
RATE_LIMIT_TTL = 3600
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_STATUS_STYLES = (
    "body{font-family:system-ui,sans-serif;margin:0;display:grid;"
    "place-items:center;min-height:100vh;background:#f7f8f5;color:#182328}"
    "main{max-width:34rem;padding:2rem;text-align:center}a{color:#087c73}"
)


def _client_ip(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _same_origin(request: HttpRequest) -> bool:
    """Accept absent Origin/Referer (curl, some no-JS agents); reject foreign."""
    host = request.get_host()
    for header in ("HTTP_ORIGIN", "HTTP_REFERER"):
        value = request.META.get(header, "")
        if not value:
            continue
        origin = value.split("://", 1)[-1].split("/", 1)[0]
        if origin != host:
            return False
    return True


def _html_response(heading: str, detail: str, *, ok: bool, locale: str) -> HttpResponse:
    back = f"/{locale}/"
    color = "#087c73" if ok else "#a77b28"
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='robots' content='noindex'>"
        f"<title>{escape(heading)}</title><style>{_STATUS_STYLES}</style></head>"
        "<body><main><h1 style='color:" + color + "'>"
        f"{escape(heading)}</h1><p>{escape(detail)}</p>"
        f"<p><a href='{escape(back)}'>{escape(back)}</a></p></main></body></html>"
    )
    return HttpResponse(
        html, status=200 if ok else 422, content_type="text/html; charset=utf-8"
    )


def _json_response(payload: dict, *, status: int) -> HttpResponse:
    import json as _json

    return HttpResponse(
        _json.dumps(payload), status=status, content_type="application/json"
    )


def _recipient_email() -> str:
    configured = (getattr(django_settings, "CONTACT_FORM_TO", "") or "").strip()
    if configured:
        return configured
    row = SiteSettings.objects.filter(site_key="default").first()
    return (row.contact_email if row else "").strip()


def _form_enabled() -> bool:
    row = SiteSettings.objects.filter(site_key="default").first()
    return bool(row and row.contact_form_enabled)


@contact_router.post("/contact", summary="Contact form (emailed to owner, not stored).")
def contact_submit(request: HttpRequest) -> HttpResponse:
    # urlencoded (browser default) and multipart both count as form posts.
    is_form = "form" in (request.content_type or "")
    locale = "en"

    def fail(detail: str, status: int = 400) -> HttpResponse:
        heading = "Message not sent" if locale == "en" else "پیام ارسال نشد"
        if is_form:
            return _html_response(heading, detail, ok=False, locale=locale)
        return _json_response({"ok": False, "error": detail}, status=status)

    if request.method != "POST":  # router guarantees POST; defensive
        return fail("method not allowed", 405)

    if not _same_origin(request):
        return fail("cross-origin submissions are rejected", 400)

    if is_form:
        data = request.POST
    else:
        try:
            data = json.loads(request.body or b"{}")
            if not isinstance(data, dict):
                data = {}
        except (ValueError, UnicodeDecodeError):
            return fail("invalid JSON body", 400)

    locale = data.get("locale") if data.get("locale") in ("fa", "en") else "en"

    # Rate limit per client IP (cheap cache counter; LocMem default is fine
    # at personal-site volume).
    ip = _client_ip(request)
    cache_key = RATE_LIMIT_KEY.format(ip=ip)
    hits = cache.get(cache_key)
    if hits is None:
        cache.set(cache_key, 1, RATE_LIMIT_TTL)
    elif hits >= RATE_LIMIT_HITS:
        AuditLog.objects.create(
            user=None,
            action="contact.ratelimited",
            model_name="contact",
            object_id=ip,
            ip=ip,
            detail="reason=rate_limit",
        )
        return fail(
            "Too many messages from this address; try again later."
            if locale == "en"
            else "تعداد پیام‌ها از این آدرس زیاد است؛ بعداً تلاش کنید.",
            429,
        )
    else:
        cache.set(cache_key, hits + 1, RATE_LIMIT_TTL)

    # Honeypot: pretend success so bots learn nothing; send nothing.
    if (data.get("website") or "").strip():
        heading = "Message sent" if locale == "en" else "پیام شما ارسال شد"
        detail = (
            "A copy was emailed to the site owner."
            if locale == "en"
            else "پیام برای مالک سایت ایمیل شد."
        )
        return _html_response(heading, detail, ok=True, locale=locale)

    email = (data.get("email") or "").strip()[:EMAIL_MAX]
    name = (data.get("name") or "").strip()[:NAME_MAX]
    message = (data.get("message") or "").strip()[:MESSAGE_MAX]

    if not EMAIL_RE.fullmatch(email):
        return fail(
            "A valid email address is required."
            if locale == "en"
            else "یک ایمیل معتبر لازم است."
        )
    if not message:
        return fail(
            "Message must not be empty." if locale == "en" else "متن پیام خالی است."
        )

    recipient = _recipient_email()
    if not recipient:
        return fail(
            "The contact inbox is not configured yet."
            if locale == "en"
            else "ایمیل دریافت‌کننده هنوز تنظیم نشده است.",
            503,
        )
    if not _form_enabled():
        return fail("Not found.", 404)
    if not getattr(django_settings, "EMAIL_HOST", ""):
        logger.warning("contact form hit without EMAIL_HOST configured")
        return fail(
            "Email delivery is not configured yet."
            if locale == "en"
            else "ارسال ایمیل هنوز تنظیم نشده است.",
            503,
        )

    subject = f"[website-contact] {name or email}"
    body = f"From: {name or '—'} <{email}>\nLocale: {locale}\n\n{message}"
    send_mail(
        subject=subject,
        message=body,
        from_email=(
            getattr(django_settings, "DEFAULT_FROM_EMAIL", "") or None
        ),
        recipient_list=[recipient],
        fail_silently=False,
    )
    # Audit WITHOUT the message body (non-persistence policy).
    AuditLog.objects.create(
        user=None,
        action="contact.sent",
        model_name="contact",
        object_id=email,
        ip=ip,
        detail="channel=email; body not stored",
    )

    heading = "Message sent" if locale == "en" else "پیام شما ارسال شد"
    detail = (
        "A copy was emailed to the site owner."
        if locale == "en"
        else "پیام برای مالک سایت ایمیل شد."
    )
    if is_form:
        return _html_response(heading, detail, ok=True, locale=locale)
    return _json_response({"ok": True}, status=200)
