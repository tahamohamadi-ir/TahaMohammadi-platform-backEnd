"""Staff-only and public share draft preview for content models (P3-07 / DEFER-0016).

Preview targets plain Django models Landing / Profile / Article.
Staff preview: ``/staff/preview/<kind>/<pk>/`` (session + MFA).
Public share: ``/preview/share/<token>/`` (stateless HMAC, 15-minute TTL).
"""

from django.http import Http404, HttpResponseGone
from django.shortcuts import get_object_or_404, render

from apps.content.html_sanitize import sanitize_html
from apps.content.models import Article, Landing, Profile
from apps.content.preview_token import PreviewTokenStatus, parse_preview_token
from apps.security.decorators import staff_otp_required

PREVIEW_KINDS = {
    "landing": Landing,
    "profile": Profile,
    "article": Article,
}


def sanitize_preview_body(raw: str) -> str:
    """Apply the same local allowlist contract used by ADR-0022 tests."""
    return sanitize_html(raw)


def _preview_context(kind: str, obj) -> dict:
    return {
        "kind": kind,
        "object": obj,
        "title": obj.title,
        "content_locale": obj.locale,
        "slug": obj.slug,
        "status": obj.status,
        "body_html": sanitize_preview_body(obj.body),
        "is_public_share": False,
    }


def _render_preview(request, kind: str, obj, *, is_public_share: bool = False):
    context = _preview_context(kind, obj)
    context["is_public_share"] = is_public_share
    return render(request, "content/staff_preview.html", context)


@staff_otp_required
def staff_content_preview(request, kind: str, pk: int):
    """Read-only staff preview of a content row (any lifecycle status, including draft)."""
    model = PREVIEW_KINDS.get(kind)
    if model is None:
        raise Http404("Unknown preview kind")
    obj = get_object_or_404(model, pk=pk)
    return _render_preview(request, kind, obj)


def public_share_preview(request, token: str):
    """Read-only public preview via signed token — no session."""
    status, payload = parse_preview_token(token)
    if status is PreviewTokenStatus.INVALID or payload is None:
        raise Http404("Invalid preview token")
    if status is PreviewTokenStatus.EXPIRED:
        return HttpResponseGone("Preview link expired")
    model = PREVIEW_KINDS.get(payload.kind)
    if model is None:
        raise Http404("Unknown preview kind")
    obj = get_object_or_404(model, pk=payload.pk)
    return _render_preview(request, payload.kind, obj, is_public_share=True)
