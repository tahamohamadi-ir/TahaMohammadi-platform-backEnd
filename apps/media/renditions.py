"""Public media delivery contract — originals are never the public image path.

Until the media-runtime phase generates real rendition files, this module is the
canonical rule set for what *may* be published. Callers must not invent CDN URLs
or Caddy ``/media/`` routes here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenditionSpec:
    name: str
    max_width: int
    max_height: int
    format: str  # e.g. webp, jpeg


# Ordered from smallest to largest; public pages should prefer the smallest
# adequate rendition. "original" is intentionally absent.
RENDITION_SPECS: tuple[RenditionSpec, ...] = (
    RenditionSpec("thumb", 320, 320, "webp"),
    RenditionSpec("card", 800, 800, "webp"),
    RenditionSpec("full", 1600, 1600, "webp"),
)

RENDITION_NAMES: frozenset[str] = frozenset(spec.name for spec in RENDITION_SPECS)


def is_image_mime(mime: str) -> bool:
    return (mime or "").lower().startswith("image/")


def original_forbidden_for_public(mime: str) -> bool:
    """Image originals must not be the public delivery object."""
    return is_image_mime(mime)


def select_public_rendition_name(mime: str, available: set[str] | frozenset[str]) -> str | None:
    """Pick the largest allowed rendition that exists.

    For non-image MIME types (e.g. PDF), returns ``None`` to signal that the
    caller must use a non-image public policy (download gated separately).

    For images, returns a name from ``RENDITION_NAMES`` or raises if only an
    original would be available.
    """
    if not is_image_mime(mime):
        return None

    known = available & RENDITION_NAMES
    if not known:
        raise ValueError(
            "public image delivery requires a rendition; original bytes are forbidden"
        )

    for spec in reversed(RENDITION_SPECS):
        if spec.name in known:
            return spec.name
    raise ValueError("no matching rendition spec")  # pragma: no cover
