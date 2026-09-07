"""Staff-only and public share draft preview for content models (P3-07 / PU-07-preview).

Preview covers every publishable entity and its private attachments.
Staff preview: ``/staff/preview/<kind>/<pk>/`` (session + MFA).
Public share: ``/preview/share/<token>/`` (stateless HMAC, expiring TTL).
Private attachments: ``/preview/share/<token>/file/`` and
``/preview/share/<token>/attachment/<media_id>/``.
"""

from __future__ import annotations

import html as html_lib
from pathlib import PurePosixPath

from django.http import FileResponse, Http404, HttpResponseGone
from django.shortcuts import get_object_or_404, render

from apps.content.html_sanitize import sanitize_html
from apps.content.models import (
    Article,
    Book,
    Collection,
    Course,
    CreativeWork,
    Download,
    Landing,
    Lesson,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
)
from apps.content.preview_token import (
    PreviewTokenStatus,
    build_preview_token,
    parse_preview_token,
)
from apps.media.models import Media
from apps.security.decorators import staff_otp_required

PREVIEW_KINDS = {
    "landing": Landing,
    "profile": Profile,
    "article": Article,
    "series": Series,
    "research-topic": ResearchTopic,
    "research-statement": ResearchStatement,
    "project": Project,
    "publication": Publication,
    "book": Book,
    "talk": Talk,
    "download": Download,
    "course": Course,
    "creative-work": CreativeWork,
    "lesson": Lesson,
    "collection": Collection,
}


def sanitize_preview_body(raw: str) -> str:
    """Apply the same local allowlist contract used by ADR-0022 tests."""
    return sanitize_html(raw)


def _preview_media_url(media: Media | None, token: str | None) -> str | None:
    if media is None:
        return None
    if media.is_active:
        return media.file.url
    # Private media MUST NOT leak public static /media/ URL
    if token:
        return f"/preview/share/{token}/attachment/{media.pk}/"
    return None


def _render_story_to_html(story, token: str | None) -> str:
    """Render draft story composition blocks into sanitized preview HTML."""
    if not story:
        return ""
    html_chunks: list[str] = []
    sections = story.sections.filter(enabled=True).order_by("position")
    for section in sections:
        blocks = section.blocks.filter(enabled=True).order_by("position")
        for block in blocks:
            st = block.settings or {}
            b_type = block.block_type
            if b_type == "text":
                body = sanitize_preview_body(str(st.get("body") or ""))
                if body:
                    html_chunks.append(f'<div class="story-block story-text">{body}</div>')
            elif b_type == "heading":
                level = int(st.get("level", 2))
                level = max(2, min(level, 6))
                text = html_lib.escape(str(st.get("text") or ""))
                if text:
                    html_chunks.append(f'<h{level} class="story-heading">{text}</h{level}>')
            elif b_type == "quote":
                quote = sanitize_preview_body(str(st.get("quote") or st.get("text") or ""))
                author = html_lib.escape(str(st.get("author") or ""))
                cite_html = f"<cite>{author}</cite>" if author else ""
                html_chunks.append(
                    f'<blockquote class="story-quote"><p>{quote}</p>{cite_html}</blockquote>'
                )
            elif b_type == "math":
                m_html = sanitize_preview_body(str(st.get("html") or ""))
                if m_html:
                    html_chunks.append(f'<div class="story-math">{m_html}</div>')
            elif b_type == "code":
                code_text = html_lib.escape(str(st.get("code") or ""))
                lang = html_lib.escape(str(st.get("language") or ""))
                html_chunks.append(
                    f'<pre class="story-code"><code class="language-{lang}">'
                    f"{code_text}</code></pre>"
                )
            elif b_type in ("figure", "media"):
                media_id = st.get("mediaId")
                if isinstance(media_id, (int, str)) and str(media_id).isdigit():
                    m = Media.objects.filter(pk=int(media_id)).first()
                    url = _preview_media_url(m, token)
                    caption = html_lib.escape(str(st.get("caption") or (m.title if m else "")))
                    alt = html_lib.escape(str((m.alt_text if m else "") or caption))
                    if url:
                        html_chunks.append(
                            f'<figure class="story-figure"><img src="{url}" alt="{alt}" />'
                            f'<figcaption>{caption}</figcaption></figure>'
                        )
            elif b_type == "file":
                dl_id = st.get("downloadId")
                if isinstance(dl_id, (int, str)) and str(dl_id).isdigit():
                    dl = Download.objects.filter(pk=int(dl_id)).select_related("media").first()
                    if dl and dl.media:
                        if dl.media.is_active and dl.allows_public_file():
                            f_url = dl.media.file.url
                        elif token:
                            f_url = f"/preview/share/{token}/attachment/{dl.media.pk}/"
                        else:
                            f_url = "#"
                        dl_title = html_lib.escape(dl.title)
                        html_chunks.append(
                            f'<div class="story-file">'
                            f'<a href="{f_url}" class="button button-download">'
                            f"Download: {dl_title}</a></div>"
                        )
            elif b_type == "table":
                cols = st.get("columns", [])
                rows = st.get("rows", [])
                caption = html_lib.escape(str(st.get("caption") or ""))
                if cols:
                    t_lines = ['<table class="story-table">']
                    if caption:
                        t_lines.append(f"<caption>{caption}</caption>")
                    t_lines.append("<thead><tr>")
                    for col in cols:
                        lbl = html_lib.escape(str(col.get("label") or col.get("key") or ""))
                        t_lines.append(f"<th>{lbl}</th>")
                    t_lines.append("</tr></thead><tbody>")
                    for row in rows:
                        t_lines.append("<tr>")
                        for col in cols:
                            k = col.get("key")
                            v = html_lib.escape(str(row.get(k, "")))
                            t_lines.append(f"<td>{v}</td>")
                        t_lines.append("</tr>")
                    t_lines.append("</tbody></table>")
                    html_chunks.append("".join(t_lines))
    return "\n".join(html_chunks)


def _preview_context(
    kind: str, obj, *, token: str | None = None, is_staff: bool = False
) -> dict:
    # 1. Main text content
    main_text = (
        getattr(obj, "body", "")
        or getattr(obj, "description", "")
        or getattr(obj, "summary", "")
        or getattr(obj, "abstract", "")
        or getattr(obj, "objective", "")
        or ""
    )
    body_chunks = []
    if main_text:
        body_chunks.append(sanitize_preview_body(main_text))

    # 2. Attached story
    story = getattr(obj, "story", None)
    if story:
        story_html = _render_story_to_html(story, token)
        if story_html:
            body_chunks.append(story_html)

    # 3. Download entity attachment
    attachment_data = None
    if kind == "download" and hasattr(obj, "media") and obj.media:
        m = obj.media
        is_private = not m.is_active or not obj.allows_public_file()
        if is_private and token:
            dl_url = f"/preview/share/{token}/file/"
        elif not is_private:
            dl_url = m.file.url
        else:
            dl_url = "#"
        attachment_data = {
            "title": m.title,
            "mime": m.mime,
            "size": m.size,
            "url": dl_url,
            "is_private": is_private,
        }

    # 4. Project case-study, evidence, diagrams, screenshots
    case_study = getattr(obj, "case_study", None)
    evidence_items = list(obj.evidence_items.all()) if hasattr(obj, "evidence_items") else []
    collaborators = list(obj.collaborators.all()) if hasattr(obj, "collaborators") else []
    funding = list(obj.funding_entries.all()) if hasattr(obj, "funding_entries") else []

    diagrams = []
    if hasattr(obj, "diagrams"):
        for d in obj.diagrams.select_related("diagram_image").order_by("id"):
            d_url = _preview_media_url(d.diagram_image, token)
            diagrams.append({
                "title": d.title,
                "version": d.version,
                "url": d_url,
                "altText": d.alt_text,
                "visibility": d.visibility,
            })

    screenshots = []
    if hasattr(obj, "screenshots"):
        for s in obj.screenshots.select_related("screenshot_image").order_by("id"):
            s_url = _preview_media_url(s.screenshot_image, token)
            screenshots.append({
                "caption": s.caption,
                "url": s_url,
                "altText": s.alt_text,
                "visibility": s.visibility,
            })

    return {
        "kind": kind,
        "object": obj,
        "title": getattr(obj, "title", str(obj)),
        "content_locale": getattr(obj, "locale", ""),
        "slug": getattr(obj, "slug", ""),
        "status": getattr(obj, "status", ""),
        "body_html": "\n<hr />\n".join(body_chunks),
        "attachment": attachment_data,
        "case_study": case_study,
        "evidence_items": evidence_items,
        "collaborators": collaborators,
        "funding": funding,
        "diagrams": diagrams,
        "screenshots": screenshots,
        "is_public_share": not is_staff,
    }


def _render_preview(
    request, kind: str, obj, *, token: str | None = None, is_public_share: bool = False
):
    context = _preview_context(kind, obj, token=token, is_staff=not is_public_share)
    response = render(request, "content/staff_preview.html", context)
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "private, no-store"
    return response


@staff_otp_required
def staff_content_preview(request, kind: str, pk: int):
    """Read-only staff preview of a content row (any lifecycle status, including draft)."""
    model = PREVIEW_KINDS.get(kind)
    if model is None:
        raise Http404("Unknown preview kind")
    obj = get_object_or_404(model, pk=pk)
    # Generate staff preview token for secure attachment streaming
    token = build_preview_token(kind, obj.pk)
    return _render_preview(request, kind, obj, token=token, is_public_share=False)


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
    return _render_preview(request, payload.kind, obj, token=token, is_public_share=True)


def public_share_preview_file(request, token: str):
    """Stream private download attachment via valid preview share token."""
    status, payload = parse_preview_token(token)
    if status is PreviewTokenStatus.INVALID or payload is None:
        raise Http404("Invalid preview token")
    if status is PreviewTokenStatus.EXPIRED:
        return HttpResponseGone("Preview link expired")
    if payload.kind != "download":
        raise Http404("Entity does not support file download")
    dl = get_object_or_404(Download, pk=payload.pk)
    media = getattr(dl, "media", None)
    if not media or not media.file:
        raise Http404("Download file not found")
    filename = PurePosixPath(media.file.name).name or "download"
    response = FileResponse(
        media.file.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type=media.mime or "application/octet-stream",
    )
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _get_attached_media_ids(obj) -> set[int]:
    """Collect all valid media IDs attached to an entity or its story/diagrams."""
    ids: set[int] = set()
    for attr in ("media_id", "cover_media_id", "social_image_id", "slides_media_id"):
        val = getattr(obj, attr, None)
        if val:
            ids.add(val)
    if hasattr(obj, "diagrams"):
        ids.update(
            obj.diagrams.filter(diagram_image__isnull=False).values_list(
                "diagram_image_id", flat=True
            )
        )
    if hasattr(obj, "screenshots"):
        ids.update(
            obj.screenshots.filter(screenshot_image__isnull=False).values_list(
                "screenshot_image_id", flat=True
            )
        )
    story = getattr(obj, "story", None)
    if story:
        for sec in story.sections.all():
            for block in sec.blocks.all():
                st = block.settings or {}
                for k in ("mediaId", "beforeMediaId", "afterMediaId"):
                    v = st.get(k)
                    if isinstance(v, int):
                        ids.add(v)
                    elif isinstance(v, str) and v.isdigit():
                        ids.add(int(v))
                m_ids = st.get("mediaIds")
                if isinstance(m_ids, list):
                    for v in m_ids:
                        if isinstance(v, int):
                            ids.add(v)
                        elif isinstance(v, str) and v.isdigit():
                            ids.add(int(v))
                dl_id = st.get("downloadId")
                if dl_id:
                    pk = int(dl_id) if str(dl_id).isdigit() else None
                    if pk:
                        dl = Download.objects.filter(pk=pk).first()
                        if dl and dl.media_id:
                            ids.add(dl.media_id)
    return ids


def public_share_preview_attachment(request, token: str, media_id: int):
    """Stream private media attachment verified as attached to the previewed entity."""
    status, payload = parse_preview_token(token)
    if status is PreviewTokenStatus.INVALID or payload is None:
        raise Http404("Invalid preview token")
    if status is PreviewTokenStatus.EXPIRED:
        return HttpResponseGone("Preview link expired")
    model = PREVIEW_KINDS.get(payload.kind)
    if model is None:
        raise Http404("Unknown preview kind")
    obj = get_object_or_404(model, pk=payload.pk)

    allowed_ids = _get_attached_media_ids(obj)
    if media_id not in allowed_ids:
        raise Http404("Media not attached to this preview object")

    media = get_object_or_404(Media, pk=media_id)
    if not media.file:
        raise Http404("Media file not found")

    content_type = media.mime or "application/octet-stream"
    filename = PurePosixPath(media.file.name).name or "attachment"
    is_image = content_type.startswith("image/")
    response = FileResponse(
        media.file.open("rb"),
        as_attachment=not is_image,
        filename=filename,
        content_type=content_type,
    )
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
