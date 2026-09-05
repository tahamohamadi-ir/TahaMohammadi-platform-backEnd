"""Upload validation — magic-byte sniffing with ``filetype`` plus SVG.

Client-supplied metadata (content_type, filename extension) is never trusted;
the file content itself is the source of truth.
"""

import os

from django.core.exceptions import ValidationError

from apps.media.sniff import SVG_MIME, sniff_mime, svg_is_safe

ALLOWED_MIMES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/svg+xml",
    "application/pdf",
    "video/mp4",
    "video/webm",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
}

MIME_TO_EXTENSIONS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/gif": {".gif"},
    "image/svg+xml": {".svg"},
    "application/pdf": {".pdf"},
    "video/mp4": {".mp4", ".m4v"},
    "video/webm": {".webm"},
    "audio/mpeg": {".mp3"},
    "audio/mp4": {".m4a", ".mp4"},
    "audio/ogg": {".ogg", ".oga"},
    "audio/wav": {".wav"},
    "audio/x-wav": {".wav"},
    "audio/webm": {".weba", ".webm"},
}

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_AV_FILE_SIZE = 50 * 1024 * 1024
AV_MIMES = {
    mime for mime in ALLOWED_MIMES if mime.startswith(("video/", "audio/"))
}


def _limit_for_mime(mime: str) -> int:
    if mime in AV_MIMES:
        return MAX_AV_FILE_SIZE
    return MAX_FILE_SIZE


def validate_file_type(value):
    """Reject files whose magic bytes are missing or not on the allowlist.

    Also rejects files whose extension does not match the detected content.
    The file pointer is restored to its original position either way.
    """
    value.seek(0)
    mime = sniff_mime(value)
    value.seek(0)

    if mime is None or mime not in ALLOWED_MIMES:
        raise ValidationError("Unsupported file type.")

    if mime == SVG_MIME and not svg_is_safe(value):
        raise ValidationError("Unsupported file type.")

    ext = os.path.splitext(value.name)[1].lower()
    if ext not in MIME_TO_EXTENSIONS.get(mime, set()):
        raise ValidationError("File extension does not match file content.")


def validate_file_size(value):
    """Reject files larger than the type-specific size cap."""
    pos = value.tell() if hasattr(value, "tell") else 0
    mime = None
    try:
        if hasattr(value, "seek"):
            value.seek(0)
            mime = sniff_mime(value)
    finally:
        if hasattr(value, "seek"):
            value.seek(pos)
    limit = _limit_for_mime(mime or "")
    if value.size > limit:
        if mime in AV_MIMES:
            raise ValidationError("File too large. Max size for audio/video is 50MB.")
        raise ValidationError("File too large. Max size is 5MB.")
