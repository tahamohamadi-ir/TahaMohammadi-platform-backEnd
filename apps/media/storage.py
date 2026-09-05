"""Safe storage naming — random hex prefix, extension from detected mime only.

The user filename is never trusted beyond a sanitized basename: no
user-controlled directories, no spaces, and no unicode in the stored name.
"""

import re
import secrets
from pathlib import Path

from django.core.exceptions import ValidationError

from apps.media.sniff import sniff_mime
from apps.media.validators import ALLOWED_MIMES, MIME_TO_EXTENSIONS

_SAFE_BASENAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _sniff_mime(instance):
    """Detect the mime of ``instance.file`` from content, or return None."""
    file_field = getattr(instance, "file", None)
    if not file_field or not getattr(file_field, "name", None):
        return None
    try:
        file_field.seek(0)
        mime = sniff_mime(file_field)
        file_field.seek(0)
    except (ValueError, OSError):  # unreadable/absent file
        return None
    return mime


def media_upload_path(instance, filename):
    """Return a storage path derived from detected mime and a random prefix.

    The extension comes from ``MIME_TO_EXTENSIONS`` keyed on the detected
    mime (``instance.mime`` when set by ``Media.save``, otherwise sniffed from
    content). If the content cannot be classified, storing it is refused.
    """
    mime = getattr(instance, "mime", None) or None
    if mime not in ALLOWED_MIMES:
        mime = _sniff_mime(instance)
    if mime not in ALLOWED_MIMES:
        raise ValidationError(
            "Cannot determine a safe storage extension for this file."
        )

    extension = sorted(MIME_TO_EXTENSIONS[mime])[0]
    stem = Path(filename).name  # strip any user-controlled directories
    stem = Path(stem).stem
    sanitized = _SAFE_BASENAME_RE.sub("-", stem).strip("-")
    if not sanitized:
        sanitized = "media"

    return f"media/{secrets.token_hex(8)}-{sanitized}{extension}"
