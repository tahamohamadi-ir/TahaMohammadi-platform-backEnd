"""Tests for PU-07-revisions: snapshot and restore content, attached story
and ordered relations atomically.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-07-revisions.md
"""

from __future__ import annotations

import json

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
from apps.composition.projection import public_story_document
from apps.content.models import (
    Article,
    Book,
    Collection,
    ContentRevision,
    Course,
    CreativeWork,
    Download,
    Lesson,
    LifecycleStatus,
    Publication,
    PublicationSnapshot,
    Series,
    Talk,
)
from apps.media.models import Media


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


def _create_story(key: str, locale: str, title: str, status: str = "published") -> CompositionPage:
    page = CompositionPage.objects.create(
        key=key,
        kind="story",
        locale=locale,
        title=title,
        status=status,
        published_at=timezone.now() if status == "published" else None,
    )
    sec = CompositionSection.objects.create(
        page=page,
        position=0,
        layout="1col",
        enabled=True,
    )
    CompositionBlock.objects.create(
        section=sec,
        position=0,
        block_type="text",
        settings={"body": "<p>Initial published story paragraph.</p>"},
        enabled=True,
    )
    return page


def test_publication_snapshot_model_exists():
    """Verify PublicationSnapshot model exists with required schema."""
    snap = PublicationSnapshot.objects.create(
        entity_key="article",
        object_id=1,
        locale="en",
        slug="test-snap",
        snapshot={"title": "Test Title", "status": "published"},
    )
    assert snap.pk is not None
    assert snap.entity_key == "article"
    assert snap.object_id == 1
    assert snap.locale == "en"
    assert snap.slug == "test-snap"
    assert snap.snapshot["title"] == "Test Title"
    assert snap.published_at is not None
    assert snap.created_at is not None


def test_atomic_revision_snapshot_includes_story_and_relations(admin_api_client):
    """Admin snapshot captures parent fields, metadata, relations, and full attached story."""
    story = _create_story("art-story-1", "en", "Story 1")
    art = Article.objects.create(
        locale="en",
        slug="article-with-story",
        title="Article With Story",
        body="Body text",
        status=LifecycleStatus.DRAFT,
        story=story,
        seo_title="SEO Article",
        related_records=[{"family": "article", "id": "999"}],
    )

    res = admin_api_client.post(
        f"/api/v1/admin/content/article/{art.pk}/revisions",
        data=json.dumps({"note": "Full snapshot test"}),
        content_type="application/json",
    )
    assert res.status_code == 201
    data = res.json()
    assert data["entityKey"] == "article"
    assert data["objectId"] == art.pk
    assert data["note"] == "Full snapshot test"
    assert data["snapshot"] is not None

    snapshot = data["snapshot"]
    assert snapshot["title"] == "Article With Story"
    assert snapshot["locale"] == "en"
    assert snapshot["fields"]["seo_title"] == "SEO Article"
    assert snapshot["fields"]["related_records"] == [{"family": "article", "id": "999"}]
    assert snapshot["fields"]["story"] == story.pk

    # Full attached composition snapshot
    assert "story" in snapshot
    assert snapshot["story"] is not None
    assert snapshot["story"]["id"] == story.pk
    assert snapshot["story"]["key"] == "art-story-1"
    assert snapshot["story"]["title"] == "Story 1"
    assert len(snapshot["story"]["sections"]) == 1
    sec0 = snapshot["story"]["sections"][0]
    assert sec0["layout"] == "1col"
    assert len(sec0["blocks"]) == 1
    assert sec0["blocks"][0]["blockType"] == "text"
    assert sec0["blocks"][0]["settings"]["body"] == "<p>Initial published story paragraph.</p>"

    # Verify single revision detail endpoint
    rev_id = data["id"]
    res_detail = admin_api_client.get(
        f"/api/v1/admin/content/article/{art.pk}/revisions/{rev_id}",
    )
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert detail_data["id"] == rev_id
    assert detail_data["snapshot"]["story"]["key"] == "art-story-1"


def test_atomic_revision_restore_as_draft(admin_api_client):
    """Restore reverts parent, relations and attached story as draft while preserving history."""
    story = _create_story("art-story-v1", "en", "Original Story")
    art = Article.objects.create(
        locale="en",
        slug="rev-art-v1",
        title="Original Title",
        body="Original body",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=story,
        seo_title="Original SEO",
        related_records=[{"family": "article", "id": "100"}],
    )

    # 1. Create baseline revision v1
    res_snap = admin_api_client.post(
        f"/api/v1/admin/content/article/{art.pk}/revisions",
        data=json.dumps({"note": "v1 snapshot"}),
        content_type="application/json",
    )
    assert res_snap.status_code == 201
    v1_id = res_snap.json()["id"]

    # 2. Mutate live article and story
    art.title = "Mutated Title"
    art.slug = "mutated-slug"
    art.seo_title = "Mutated SEO"
    art.related_records = [{"family": "article", "id": "200"}]
    art.save()

    # Mutate story sections and blocks
    story.title = "Mutated Story Title"
    story.save()
    sec0 = story.sections.first()
    sec0.blocks.all().delete()
    CompositionBlock.objects.create(
        section=sec0,
        position=0,
        block_type="text",
        settings={"body": "<p>MUTATED STORY CONTENT</p>"},
        enabled=True,
    )

    # 3. Restore v1
    res_restore = admin_api_client.post(
        f"/api/v1/admin/content/article/{art.pk}/revisions/{v1_id}/restore",
    )
    assert res_restore.status_code == 200

    # 4. Verify parent restored and forced to draft
    art.refresh_from_db()
    assert art.title == "Original Title"
    assert art.slug == "rev-art-v1"
    assert art.seo_title == "Original SEO"
    assert art.related_records == [{"family": "article", "id": "100"}]
    assert art.status == LifecycleStatus.DRAFT
    assert art.scheduled_for is None

    # 5. Verify attached story restored to v1 blocks and forced to draft
    story.refresh_from_db()
    assert story.title == "Original Story"
    assert story.status == "draft"
    assert story.sections.count() == 1
    blocks = story.sections.first().blocks.all()
    assert blocks.count() == 1
    assert blocks.first().settings["body"] == "<p>Initial published story paragraph.</p>"

    # 6. Verify pre-restore revision was created to preserve history
    revs = ContentRevision.objects.filter(entity_key="article", object_id=art.pk).order_by("-id")
    assert revs.count() >= 2
    pre_restore_rev = revs.first()
    assert pre_restore_rev.note == "pre-restore snapshot"
    assert pre_restore_rev.snapshot["title"] == "Mutated Title"
    pre_story_body = (
        pre_restore_rev.snapshot["story"]["sections"][0]["blocks"][0]["settings"]["body"]
    )
    assert pre_story_body == "<p>MUTATED STORY CONTENT</p>"


def test_publication_snapshot_draft_isolation_in_projection(admin_api_client):
    """Draft edits to an attached story do not leak through public projection
    until explicit publication.
    """
    story = _create_story("pub-story-iso", "en", "Published Story", status="published")
    Article.objects.create(
        locale="en",
        slug="pub-iso-article",
        title="Published Article",
        body="Article body",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=story,
    )

    # Explicitly transition/update to published so PublicationSnapshot is recorded
    PublicationSnapshot.objects.create(
        entity_key="composition",
        object_id=story.pk,
        locale=story.locale,
        slug=story.key,
        snapshot={
            "id": story.pk,
            "key": story.key,
            "title": story.title,
            "locale": story.locale,
            "status": "published",
            "sections": [
                {
                    "layout": "1col",
                    "ratio": "",
                    "enabled": True,
                    "blocks": [
                        {
                            "blockType": "text",
                            "settings": {"body": "<p>APPROVED PUBLIC VERSION</p>"},
                            "enabled": True,
                        }
                    ],
                }
            ],
        },
    )

    # Public projection projects the approved snapshot
    doc = public_story_document(story, "en")
    assert doc is not None
    assert doc["title"] == "Published Story"
    assert doc["sections"][0]["blocks"][0]["settings"]["body"] == "<p>APPROVED PUBLIC VERSION</p>"

    # Now make a draft edit directly on story model/blocks
    story.status = "draft"
    story.title = "Draft In-Progress Story"
    story.save()
    sec0 = story.sections.first()
    sec0.blocks.all().delete()
    CompositionBlock.objects.create(
        section=sec0,
        position=0,
        block_type="text",
        settings={"body": "<p>DRAFT UNAPPROVED LEAK</p>"},
        enabled=True,
    )

    # Public projection MUST continue returning the published snapshot, NOT the draft leak!
    doc_draft = public_story_document(story, "en")
    assert doc_draft is not None
    assert doc_draft["title"] == "Published Story"
    draft_body = doc_draft["sections"][0]["blocks"][0]["settings"]["body"]
    assert draft_body == "<p>APPROVED PUBLIC VERSION</p>"

    # When explicitly published via admin composition update
    res_pub = admin_api_client.put(
        f"/api/v1/admin/composition/{story.pk}",
        data=json.dumps({
            "status": "published",
            "title": "Re-Published Story",
            "sections": [
                {
                    "layout": "1col",
                    "ratio": "",
                    "enabled": True,
                    "blocks": [
                        {
                            "blockType": "text",
                            "settings": {"body": "<p>NEW APPROVED PUBLIC VERSION</p>"},
                            "enabled": True,
                        }
                    ],
                }
            ],
        }),
        content_type="application/json",
        HTTP_IF_MATCH=story.updated_at.isoformat(),
    )
    assert res_pub.status_code == 200

    # Public projection now projects the newly published version
    story.refresh_from_db()
    doc_repub = public_story_document(story, "en")
    assert doc_repub is not None
    assert doc_repub["title"] == "Re-Published Story"
    repub_body = doc_repub["sections"][0]["blocks"][0]["settings"]["body"]
    assert repub_body == "<p>NEW APPROVED PUBLIC VERSION</p>"


def test_project_snapshot_with_nonempty_relations_regression(admin_api_client):
    """REPRO: project with evidence/collaborators/funding must snapshot without AttributeError.

    Before fix: AttributeError: 'ProjectCollaborator' object has no attribute 'affiliation'.
    Uses real model fields (label/value/source, name/role/publication_approved,
    funder/grant_id/publication_approved, case-study depth/problem/constraints/...).
    """
    from apps.api.admin_content import _build_full_content_snapshot
    from apps.content.models import (
        Project,
        ProjectCaseStudyDetails,
        ProjectCollaborator,
        ProjectEvidence,
        ProjectFunding,
    )

    proj = Project.objects.create(
        locale="en",
        slug="proj-snap-repro",
        title="Snapshot Repro Project",
        status=LifecycleStatus.DRAFT,
    )
    ProjectCaseStudyDetails.objects.create(
        project=proj,
        depth="standard",
        problem="Repro problem",
        constraints="Repro constraints",
        technical_decisions="Repro decisions",
        trade_offs="Repro tradeoffs",
        outcomes_summary="Repro outcomes",
        lessons_learned="Repro lessons",
        testing_summary="Repro testing",
    )
    ProjectEvidence.objects.create(
        project=proj,
        label="Repro evidence",
        value="42",
        source="https://example.com/repro",
        visibility="public",
    )
    ProjectCollaborator.objects.create(
        project=proj,
        name="Repro Collaborator",
        role="Researcher",
        publication_approved=True,
    )
    ProjectFunding.objects.create(
        project=proj,
        funder="Repro Funder",
        grant_id="GRANT-001",
        publication_approved=True,
    )
    # Must not raise; must map real fields only.
    snap = _build_full_content_snapshot(proj, "project")
    pcs = snap["project_case_study"]
    assert pcs["details"]["problem"] == "Repro problem"
    assert pcs["details"]["constraints"] == "Repro constraints"
    assert "solution" not in pcs["details"]
    assert pcs["evidence"][0]["label"] == "Repro evidence"
    assert pcs["evidence"][0]["source"] == "https://example.com/repro"
    assert "evidenceType" not in pcs["evidence"][0]
    assert pcs["collaborators"][0]["name"] == "Repro Collaborator"
    assert pcs["collaborators"][0]["publication_approved"] is True
    assert "affiliation" not in pcs["collaborators"][0]
    assert pcs["funding"][0]["funder"] == "Repro Funder"
    assert pcs["funding"][0]["grant_id"] == "GRANT-001"
    assert "grantName" not in pcs["funding"][0]

    # HTTP snapshot path must also succeed with nonempty relations.
    res = admin_api_client.post(
        f"/api/v1/admin/content/project/{proj.pk}/revisions",
        data=json.dumps({"note": "repro nonempty"}),
        content_type="application/json",
    )
    assert res.status_code == 201, res.content[:1000]


def test_all_family_preview_links(admin_api_client):
    """All publishable entities can generate signed expiring preview share links."""
    # Create sample instances across various families
    art = Article.objects.create(locale="en", slug="prev-art", title="Preview Article")
    pub = Publication.objects.create(locale="en", slug="prev-pub", title="Preview Pub")
    book = Book.objects.create(locale="en", slug="prev-book", title="Preview Book")
    talk = Talk.objects.create(locale="en", slug="prev-talk", title="Preview Talk")

    media = Media.objects.create(
        file=SimpleUploadedFile("doc.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
        title="Preview Doc",
        mime="application/pdf",
        is_active=True,
    )
    dl = Download.objects.create(locale="en", slug="prev-dl", title="Preview Download", media=media)
    course = Course.objects.create(locale="en", slug="prev-course", title="Preview Course")
    cw = CreativeWork.objects.create(locale="en", slug="prev-cw", title="Preview Creative")
    lesson = Lesson.objects.create(
        course=course, locale="en", slug="prev-lesson", title="Preview Lesson"
    )
    col = Collection.objects.create(locale="en", slug="prev-col", title="Preview Collection")
    series = Series.objects.create(locale="en", slug="prev-series", title="Preview Series")

    entities = [
        ("article", art.pk),
        ("publication", pub.pk),
        ("book", book.pk),
        ("talk", talk.pk),
        ("download", dl.pk),
        ("course", course.pk),
        ("creative-work", cw.pk),
        ("lesson", lesson.pk),
        ("collection", col.pk),
        ("series", series.pk),
    ]

    client = Client()
    for entity, pk in entities:
        res = admin_api_client.post(f"/api/v1/admin/content/{entity}/{pk}/preview-link")
        assert res.status_code == 200, f"Failed preview-link for {entity}"
        data = res.json()
        assert "url" in data
        assert "path" in data
        assert data["ttlSeconds"] > 0

        # Verify preview share link resolves (stateless signed token)
        share_res = client.get(data["path"])
        assert share_res.status_code == 200, f"Failed public preview share for {entity}"
        assert share_res.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
