"""Tests for PU-07-preview: Extend expiring private preview
to every publishable entity and its private attachments.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-07-preview.md
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.composition.models import (
    CompositionBlock,
    CompositionPage,
    CompositionSection,
)
from apps.content.models import (
    Article,
    Book,
    Collection,
    Course,
    CreativeWork,
    Download,
    Landing,
    Lesson,
    LifecycleStatus,
    Profile,
    Project,
    ProjectCaseStudyDetails,
    ProjectDiagram,
    ProjectEvidence,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
)
from apps.content.preview_token import build_preview_token
from apps.content.views_preview import PREVIEW_KINDS
from apps.media.models import Media

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def db_access(db):
    pass


@pytest.fixture
def admin_api_client(db, admin_user):
    totp_device = TOTPDevice.objects.create(
        user=admin_user, name="default", confirmed=True
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session["django_otp_device_id"] = totp_device.persistent_id
    session.save()
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def test_preview_kinds_coverage():
    """PREVIEW_KINDS must register all 15 publishable models."""
    expected_entities = {
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
    for entity, model in expected_entities.items():
        assert entity in PREVIEW_KINDS, f"Missing {entity} in PREVIEW_KINDS"
        assert PREVIEW_KINDS[entity] is model, f"Mismatched model for {entity}"


def test_all_15_entities_public_share_preview(admin_api_client):
    """Every publishable entity can be previewed via signed expiring token."""
    media = Media.objects.create(
        file=SimpleUploadedFile("prev.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
        title="Preview Media",
        mime="application/pdf",
        is_active=False,
    )
    course = Course.objects.create(locale="en", slug="prev-course-1", title="Preview Course 1")
    instances = [
        (
            "landing",
            Landing.objects.create(
                locale="en", slug="prev-landing", title="Preview Landing", body="<p>landing</p>"
            ),
        ),
        (
            "profile",
            Profile.objects.create(
                locale="en", slug="prev-profile", title="Preview Profile", body="<p>profile</p>"
            ),
        ),
        (
            "article",
            Article.objects.create(
                locale="en", slug="prev-article", title="Preview Article", body="<p>article</p>"
            ),
        ),
        (
            "series",
            Series.objects.create(
                locale="en", slug="prev-series", title="Preview Series", description="series desc"
            ),
        ),
        (
            "research-topic",
            ResearchTopic.objects.create(
                locale="en", slug="prev-topic", title="Preview Topic", summary="topic summary"
            ),
        ),
        (
            "research-statement",
            ResearchStatement.objects.create(
                locale="en",
                slug="prev-statement",
                title="Preview Statement",
                body="<p>statement</p>",
            ),
        ),
        (
            "project",
            Project.objects.create(
                locale="en",
                slug="prev-project",
                title="Preview Project",
                objective="project objective",
            ),
        ),
        (
            "publication",
            Publication.objects.create(
                locale="en",
                slug="prev-pub",
                title="Preview Publication",
                abstract="pub abstract",
            ),
        ),
        (
            "book",
            Book.objects.create(
                locale="en", slug="prev-book", title="Preview Book", description="book desc"
            ),
        ),
        (
            "talk",
            Talk.objects.create(
                locale="en", slug="prev-talk", title="Preview Talk", abstract="talk abstract"
            ),
        ),
        (
            "download",
            Download.objects.create(
                locale="en",
                slug="prev-dl",
                title="Preview Download",
                description="dl desc",
                media=media,
            ),
        ),
        ("course", course),
        (
            "creative-work",
            CreativeWork.objects.create(
                locale="en", slug="prev-cw", title="Preview Creative", description="cw desc"
            ),
        ),
        (
            "lesson",
            Lesson.objects.create(
                course=course,
                locale="en",
                slug="prev-lesson",
                title="Preview Lesson",
                summary="lesson summary",
            ),
        ),
        (
            "collection",
            Collection.objects.create(
                locale="en", slug="prev-col", title="Preview Collection", description="col desc"
            ),
        ),
    ]

    client = Client()
    for entity, obj in instances:
        token = build_preview_token(entity, obj.pk)
        url = f"/preview/share/{token}/"
        response = client.get(url)
        assert response.status_code == 200, f"Failed preview for {entity}"
        assert response.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
        assert "no-store" in response.headers.get("Cache-Control", "")
        content = response.content.decode()
        assert obj.title in content


def test_private_download_does_not_expose_public_media_url():
    """Private download preview must NOT output /media/ URL;
    streams via expiring preview file route.
    """
    media = Media.objects.create(
        file=SimpleUploadedFile(
            "confidential.pdf", b"%PDF-1.4 confidential data", content_type="application/pdf"
        ),
        title="Confidential Report",
        mime="application/pdf",
        is_active=False,  # PRIVATE FILE
    )
    dl = Download.objects.create(
        locale="en",
        slug="confidential-dl",
        title="Confidential Download",
        description="Private download description",
        media=media,
        status=LifecycleStatus.DRAFT,
    )

    client = Client()
    token = build_preview_token("download", dl.pk)

    # 1. Preview HTML MUST NOT contain the public static /media/ URL
    preview_res = client.get(f"/preview/share/{token}/")
    assert preview_res.status_code == 200
    html_content = preview_res.content.decode()
    assert "/media/" not in html_content, "Public /media/ URL leaked for private file!"
    assert f"/preview/share/{token}/file/" in html_content

    # 2. Anonymous access to /media/... directly returns 404
    direct_media_res = client.get(f"/media/{media.file.name}")
    assert direct_media_res.status_code == 404

    # 3. Secure streaming via preview route works with valid token
    file_res = client.get(f"/preview/share/{token}/file/")
    assert file_res.status_code == 200
    assert file_res.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
    assert "no-store" in file_res.headers.get("Cache-Control", "")
    assert file_res.headers.get("X-Content-Type-Options") == "nosniff"
    assert b"%PDF-1.4 confidential data" in b"".join(file_res.streaming_content)

    # 4. Expired token on preview file route returns 410 Gone
    expired_token = build_preview_token("download", dl.pk, ttl_seconds=-60)
    expired_res = client.get(f"/preview/share/{expired_token}/file/")
    assert expired_res.status_code == 410

    # 5. Tampered token returns 404
    tampered_res = client.get(f"/preview/share/{token[:-4]}dead/file/")
    assert tampered_res.status_code == 404


def test_story_with_private_media_attachment():
    """Story with private media attachment renders preview URL without leaking /media/ URL."""
    media = Media.objects.create(
        file=SimpleUploadedFile("secret_figure.png", PNG_1X1, content_type="image/png"),
        title="Secret Figure",
        alt_text="A secret diagram",
        mime="image/png",
        is_active=False,  # PRIVATE MEDIA
    )
    story = CompositionPage.objects.create(
        key="article-story-preview",
        kind="story",
        locale="en",
        title="Article Story",
        status="draft",
    )
    sec = CompositionSection.objects.create(
        page=story,
        position=0,
        layout="1col",
        ratio="",
        enabled=True,
    )
    CompositionBlock.objects.create(
        section=sec,
        position=0,
        block_type="figure",
        settings={"mediaId": media.pk, "caption": "Secret diagram caption"},
        enabled=True,
    )
    art = Article.objects.create(
        locale="en",
        slug="story-preview-art",
        title="Article with Private Story Media",
        body="<p>Article body</p>",
        status=LifecycleStatus.DRAFT,
        story=story,
    )

    client = Client()
    token = build_preview_token("article", art.pk)

    # 1. Preview HTML renders story with private preview attachment URL and no /media/ leak
    preview_res = client.get(f"/preview/share/{token}/")
    assert preview_res.status_code == 200
    html_content = preview_res.content.decode()
    assert "/media/" not in html_content, "Public /media/ URL leaked for private story figure!"
    expected_attachment_path = f"/preview/share/{token}/attachment/{media.pk}/"
    assert expected_attachment_path in html_content
    assert "Secret diagram caption" in html_content

    # 2. Fetching the attachment via preview attachment route succeeds with private headers
    att_res = client.get(expected_attachment_path)
    assert att_res.status_code == 200
    assert att_res.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
    assert "no-store" in att_res.headers.get("Cache-Control", "")
    assert att_res.headers.get("Content-Type") == "image/png"
    assert b"".join(att_res.streaming_content) == PNG_1X1

    # 3. Unattached media ID on this token returns 404 (isolation guard)
    foreign_media = Media.objects.create(
        file=SimpleUploadedFile("other.png", PNG_1X1, content_type="image/png"),
        title="Other Media",
        mime="image/png",
        is_active=False,
    )
    foreign_res = client.get(f"/preview/share/{token}/attachment/{foreign_media.pk}/")
    assert foreign_res.status_code == 404


def test_project_case_study_and_private_diagrams_in_preview():
    """Project preview renders case-study details, evidence, and private diagrams
    without /media/ leak.
    """
    diagram_media = Media.objects.create(
        file=SimpleUploadedFile("arch_diagram.png", PNG_1X1, content_type="image/png"),
        title="Architecture Diagram",
        mime="image/png",
        is_active=False,  # PRIVATE DIAGRAM
    )
    proj = Project.objects.create(
        locale="en",
        slug="preview-project-arch",
        title="Architectural Project",
        objective="Project introduction",
        status=LifecycleStatus.DRAFT,
    )
    ProjectCaseStudyDetails.objects.create(
        project=proj,
        depth="standard",
        problem="Large scale data fragmentation",
        technical_decisions="Distributed ledger sync engine",
        outcomes_summary="Reduced latency by 45%",
    )
    ProjectEvidence.objects.create(
        project=proj,
        label="Internal Benchmark Results",
        value="Sub-second query resolution",
        visibility="internal",
        source="Lab log #42",
    )
    ProjectDiagram.objects.create(
        project=proj,
        title="System Topology",
        diagram_date=timezone.now().date(),
        visibility="restricted",
        diagram_image=diagram_media,
    )

    client = Client()
    token = build_preview_token("project", proj.pk)

    preview_res = client.get(f"/preview/share/{token}/")
    assert preview_res.status_code == 200
    html_content = preview_res.content.decode()

    # Case study and evidence must be rendered for editorial preview
    assert "Distributed ledger sync engine" in html_content
    assert "Internal Benchmark Results" in html_content
    assert "System Topology" in html_content

    # Private diagram image MUST NOT leak public /media/ URL
    assert "/media/" not in html_content
    diagram_attachment_path = f"/preview/share/{token}/attachment/{diagram_media.pk}/"
    assert diagram_attachment_path in html_content

    # Streaming diagram attachment via preview URL works
    diag_res = client.get(diagram_attachment_path)
    assert diag_res.status_code == 200
    assert diag_res.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
    assert b"".join(diag_res.streaming_content) == PNG_1X1
