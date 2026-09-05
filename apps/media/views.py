"""Public /media/ delivery — active files only (staff may preview inactive)."""

from __future__ import annotations

from django.http import FileResponse, Http404

from apps.media.models import Media


def serve_public_media(request, name: str):
    """Serve a library file if it is active, or if the caller is staff.

    Anonymous and non-staff callers only receive ``is_active=True`` rows.
    Path traversal is rejected; lookup is by stored ``FileField`` name.
    """
    if not name or ".." in name.split("/"):
        raise Http404()
    media = Media.objects.filter(file=name).first()
    if media is None:
        raise Http404()
    user = getattr(request, "user", None)
    staff = bool(user and user.is_authenticated and getattr(user, "is_staff", False))
    if not media.is_active and not staff:
        raise Http404()
    try:
        handle = media.file.open("rb")
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise Http404() from exc
    return FileResponse(
        handle,
        as_attachment=False,
        content_type=media.mime or "application/octet-stream",
    )
