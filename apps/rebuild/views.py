"""Rebuild trigger and internal machine callback endpoints (P3-08, PU-07-jobs).

Endpoints:
- ``/rebuild-trigger/`` POST: loopback script trigger.
- ``/api/v1/internal/publication-jobs/<uuid:job_id>`` GET: runner job payload retrieval.
- ``/api/v1/internal/publication-jobs/<uuid:job_id>/result`` POST: runner result callback.
"""

from __future__ import annotations

import json
import time

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.rebuild.models import PublicationJob
from apps.rebuild.services import (
    MAX_TRIGGER_AGE_SECONDS,
    invoke_static_rebuild,
    record_job_result,
    validate_machine_request,
    validate_rebuild_token,
)

_GENERIC_DENIAL = {"status": "denied"}


@csrf_exempt
@require_http_methods(["POST"])
def rebuild_trigger(request):
    """Accept a signed rebuild trigger after validating freshness and HMAC."""
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


@csrf_exempt
@require_http_methods(["GET"])
def internal_publication_job_detail(request, job_id):
    """Retrieve publication job payload for authenticated build runner."""
    is_valid, msg, status_code = validate_machine_request(request)
    if not is_valid:
        return JsonResponse({"code": "AUTH_FAILED", "message": msg}, status=status_code)

    job = PublicationJob.objects.filter(id=job_id).first()
    if not job:
        return JsonResponse(
            {"code": "NOT_FOUND", "message": f"Publication job {job_id} not found"},
            status=404,
        )

    return JsonResponse(job.to_dict(), status=200)


@csrf_exempt
@require_http_methods(["POST"])
def internal_publication_job_result(request, job_id):
    """Authenticated callback endpoint for build runner result submission."""
    is_valid, msg, status_code = validate_machine_request(request)
    if not is_valid:
        return JsonResponse({"code": "AUTH_FAILED", "message": msg}, status=status_code)

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"code": "INVALID_JSON", "message": "Malformed JSON body"}, status=400)

    state = data.get("state")
    if not state:
        return JsonResponse(
            {"code": "VALIDATION_FAILED", "message": "Missing required field 'state'"},
            status=400,
        )

    status_code, payload = record_job_result(
        job_id,
        state=state,
        artifact_revision=data.get("artifactRevision"),
        error_code=data.get("errorCode"),
        removal_state=data.get("removalState"),
    )
    return JsonResponse(payload, status=status_code)
