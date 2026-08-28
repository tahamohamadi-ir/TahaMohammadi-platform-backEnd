"""Rebuild trigger endpoint (P3-08) — POST only, signed, gated.

When HMAC validation succeeds, starts ``rebuild-web.sh`` in the background.
The endpoint stays off the public Caddy surface; loopback callers only.
"""

import time

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.rebuild.services import (
    MAX_TRIGGER_AGE_SECONDS,
    invoke_static_rebuild,
    validate_rebuild_token,
)

_GENERIC_DENIAL = {"status": "denied"}


@csrf_exempt
@require_POST
def rebuild_trigger(request):
    """Accept a signed rebuild trigger after validating freshness and HMAC.

    CSRF-exempt because this is a machine-to-machine endpoint authenticated by
    the HMAC signature (a script caller has no browser session), never by users.
    """
    enabled = getattr(settings, "REBUILD_TRIGGER_ENABLED", False)
    secret = getattr(settings, "REBUILD_TRIGGER_SECRET", "")
    if not enabled or not secret:
        return JsonResponse(_GENERIC_DENIAL, status=403)

    try:
        timestamp = int(request.POST.get("timestamp", ""))
    except ValueError:
        return JsonResponse(_GENERIC_DENIAL, status=403)

    if abs(int(time.time()) - timestamp) > MAX_TRIGGER_AGE_SECONDS:
        return JsonResponse(_GENERIC_DENIAL, status=403)

    token = request.POST.get("token", "")
    if not validate_rebuild_token(token, secret, timestamp):
        return JsonResponse(_GENERIC_DENIAL, status=403)

    invoke_static_rebuild(enabled=True)
    return JsonResponse({"status": "ok", "triggered": True})
