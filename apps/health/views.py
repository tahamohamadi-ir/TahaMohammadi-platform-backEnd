"""Health/readiness endpoint — no secrets, no internal paths, no stack traces."""

from django.conf import settings as django_settings
from django.db import connection
from django.http import JsonResponse


def _contact_health() -> tuple[str, str | None]:
    """Return (contact_status, detail) without leaking secrets.

    - disabled: contact form not enabled in SiteSettings.
    - ok: enabled and EMAIL_HOST + recipient present.
    - error: enabled but EMAIL_HOST or recipient missing (503 path).
    """
    try:
        from apps.siteconfig.models import SiteSettings

        row = SiteSettings.objects.filter(site_key="default").first()
        if not row or not row.contact_form_enabled:
            return "disabled", None
        recipient = (getattr(django_settings, "CONTACT_FORM_TO", "") or "").strip()
        if not recipient:
            recipient = (getattr(row, "contact_email", "") or "").strip()
        email_host = (getattr(django_settings, "EMAIL_HOST", "") or "").strip()
        if not email_host:
            return "error", "contact form enabled but EMAIL_HOST not configured"
        if not recipient:
            return (
                "error",
                "contact form enabled but no recipient "
                "(CONTACT_FORM_TO / SiteSettings.contact_email)",
            )
        return "ok", None
    except Exception:  # pragma: no cover - missing table before migrate should not flap health
        return "unknown", None


def health(request):
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # pragma: no cover - defensive for readiness reporting
        db_ok = False
    if not db_ok:
        return JsonResponse({"status": "degraded", "db": "error", "contact": "unknown"})
    contact_status, detail = _contact_health()
    if contact_status == "error":
        payload: dict[str, str] = {"status": "degraded", "db": "ok", "contact": "error"}
        if detail:
            payload["detail"] = detail
        return JsonResponse(payload)
    return JsonResponse(
        {
            "status": "ok",
            "db": "ok",
            "contact": contact_status,
        }
    )
