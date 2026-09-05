"""Magic-byte sniffing for the media library (images, PDF, SVG, AV)."""

from __future__ import annotations

import io

import filetype

SVG_MIME = "image/svg+xml"
_SVG_FORBIDDEN = (b"<script", b"javascript:", b"onload=", b"onerror=", b"<foreignobject")


def _as_file(value):
    if hasattr(value, "seek") and hasattr(value, "read"):
        return value
    return io.BytesIO(value)


def _looks_like_svg(head: bytes) -> bool:
    text = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if text.lower().startswith(b"<?xml"):
        end = text.find(b"?>")
        if end == -1:
            return False
        text = text[end + 2 :].lstrip()
    return text.lower().startswith(b"<svg")


def sniff_mime(value) -> str | None:
    """Return a detected MIME from file content, or None if unknown."""
    handle = _as_file(value)
    pos = handle.tell()
    try:
        head = handle.read(2048)
        kind = filetype.guess(head)
        if kind is not None:
            return kind.mime
        if _looks_like_svg(head):
            return SVG_MIME
        return None
    finally:
        handle.seek(pos)


def svg_is_safe(value) -> bool:
    """Reject SVG that embeds script-like payloads (fail-closed)."""
    handle = _as_file(value)
    pos = handle.tell()
    try:
        payload = handle.read()
    finally:
        handle.seek(pos)
    lower = payload.lower()
    return not any(token in lower for token in _SVG_FORBIDDEN)


def mime_family(mime: str) -> str:
    """Coarse family used for replace-compat and story block checks."""
    value = (mime or "").lower()
    if value.startswith("image/"):
        return "image"
    if value.startswith("video/"):
        return "video"
    if value.startswith("audio/"):
        return "audio"
    if value == "application/pdf":
        return "pdf"
    return "other"
