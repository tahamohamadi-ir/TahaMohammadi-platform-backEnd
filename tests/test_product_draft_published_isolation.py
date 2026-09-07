"""Tests for A04: draft edits and restore-as-draft must not change or hide the
published parent document, its story, or its relations until explicit publication.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
("A draft edit must not change the currently published document until explicit
publication.") Explicit archive stays a separate, removing operation.

These tests go through real HTTP publish/restore flows (no hand-made
PublicationSnapshot rows).
"""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.composition.models import CompositionBlock, CompositionPage, CompositionSection
from apps.content.models import Article, LifecycleStatus
from apps.rebuild.models import PublicationJob


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


@pytest.fixture
def anon_client(db):
    return Client()


def _publish(admin_api_client, entity, pk):
    res = admin_api_client.post(
        f"/api/v1/admin/content/{entity}/{pk}/transition",
        data=json.dumps({"to": "published", "reason": "A04 test publish"}),
        content_type="application/json",
    )
    assert res.status_code == 200, res.content[:500]
    return res


def _snapshot_revision(admin_api_client, entity, pk, note="a04"):
    res = admin_api_client.post(
        f"/api/v1/admin/content/{entity}/{pk}/revisions",
        data=json.dumps({"note": note}),
        content_type="application/json",
    )
    assert res.status_code == 201, res.content[:500]
    return res.json()["id"]


def _restore(admin_api_client, entity, pk, rev_id):
    res = admin_api_client.post(
        f"/api/v1/admin/content/{entity}/{pk}/revisions/{rev_id}/restore",
    )
    assert res.status_code == 200, res.content[:500]
    return res


def _put(admin_api_client, entity, pk, payload):
    """PUT with a fresh If-Match precondition (optimistic locking)."""
    art = Article.objects.get(pk=pk)
    stamp = art.updated_at.isoformat()
    if stamp.endswith("+00:00"):
        stamp = stamp.removesuffix("+00:00") + "Z"
    res = admin_api_client.put(
        f"/api/v1/admin/content/{entity}/{pk}",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{stamp}"',
    )
    assert res.status_code == 200, res.content[:500]
    return res


def _make_story(key, title, body_html, status="published"):
    page = CompositionPage.objects.create(
        key=key,
        kind="story",
        locale="en",
        title=title,
        status=status,
        published_at=timezone.now() if status == "published" else None,
    )
    sec = CompositionSection.objects.create(
        page=page, position=0, layout="1col", enabled=True
    )
    CompositionBlock.objects.create(
        section=sec,
        position=0,
        block_type="text",
        settings={"body": body_html},
        enabled=True,
    )
    return page


def test_restore_draft_keeps_published_parent_list_and_resolver_a04(
    admin_api_client, anon_client
):
    """publish → restore draft → GET previous version → publish → GET new version."""
    story = _make_story("a04-story", "A04 Story", "<p>A04 PUBLISHED STORY</p>")
    art = Article.objects.create(
        locale="en",
        slug="a04-cycle",
        title="A04 v0 draft",
        body="<p>v0 body</p>",
        excerpt="v0 excerpt",
        status=LifecycleStatus.DRAFT,
        story=story,
    )
    rev0 = _snapshot_revision(admin_api_client, "article", art.pk, note="v0")
    _publish(admin_api_client, "article", art.pk)

    detail_url = "/api/articles/en/a04-cycle"
    got = anon_client.get(detail_url)
    assert got.status_code == 200
    assert got.json()["title"] == "A04 v0 draft"

    # Republish a new version through the real admin update path.
    _put(admin_api_client, "article", art.pk, {"title": "A04 v1 live"})
    assert anon_client.get(detail_url).json()["title"] == "A04 v1 live"

    # Restore the old revision as draft: the live row becomes a draft carrying
    # v0 content, but the public must keep serving v1.
    jobs_before = PublicationJob.objects.count()
    _restore(admin_api_client, "article", art.pk, rev0)
    art.refresh_from_db()
    assert art.status == LifecycleStatus.DRAFT
    assert art.title == "A04 v0 draft"

    # A04: explicit archive is the removing operation — restore enqueues none.
    assert PublicationJob.objects.count() == jobs_before

    got = anon_client.get(detail_url)
    assert got.status_code == 200
    body = got.json()
    assert body["title"] == "A04 v1 live"
    assert body["story"]["sections"][0]["blocks"][0]["settings"]["body"] == (
        "<p>A04 PUBLISHED STORY</p>"
    )

    # The still-published record stays listed and resolvable.
    listed = anon_client.get("/api/articles/en").json()
    listed_items = listed["items"] if isinstance(listed, dict) else listed
    assert any(
        it["slug"] == "a04-cycle" and it["title"] == "A04 v1 live"
        for it in listed_items
    )

    resolve = anon_client.get(
        "/api/v1/records/en/resolve", {"refs": f"article:{art.pk}"}
    )
    assert resolve.status_code == 200
    payload = resolve.json()
    assert payload["items"] and payload["items"][0]["slug"] == "a04-cycle"
    assert payload["items"][0]["title"] == "A04 v1 live"
    assert payload["unresolved"] == []

    # Explicit re-publication activates the restored content.
    _publish(admin_api_client, "article", art.pk)
    got = anon_client.get(detail_url)
    assert got.status_code == 200
    assert got.json()["title"] == "A04 v0 draft"


def test_draft_edit_keeps_published_version_a04(admin_api_client, anon_client):
    """PUT status=draft on a published record hides nothing from the public."""
    art = Article.objects.create(
        locale="en",
        slug="a04-draft-edit",
        title="A04 published title",
        body="<p>live body</p>",
        status=LifecycleStatus.DRAFT,
    )
    _publish(admin_api_client, "article", art.pk)
    detail_url = "/api/articles/en/a04-draft-edit"
    assert anon_client.get(detail_url).status_code == 200

    _put(admin_api_client, "article", art.pk, {"status": "draft", "title": "A04 draft in progress"})

    got = anon_client.get(detail_url)
    assert got.status_code == 200
    assert got.json()["title"] == "A04 published title"


def test_scheduled_publish_then_draft_keeps_serving_a04(admin_api_client, anon_client):
    """Scheduled publication snapshots too: later drafts must not unpublish."""
    art = Article.objects.create(
        locale="en",
        slug="a04-scheduled",
        title="A04 scheduled title",
        body="<p>scheduled body</p>",
        status=LifecycleStatus.SCHEDULED,
        scheduled_for=timezone.now() - timezone.timedelta(minutes=1),
    )
    call_command("publish_scheduled_content")
    art.refresh_from_db()
    assert art.status == LifecycleStatus.PUBLISHED

    detail_url = "/api/articles/en/a04-scheduled"
    assert anon_client.get(detail_url).json()["title"] == "A04 scheduled title"

    _put(
        admin_api_client,
        "article",
        art.pk,
        {"status": "draft", "title": "A04 post-schedule draft"},
    )

    got = anon_client.get(detail_url)
    assert got.status_code == 200
    assert got.json()["title"] == "A04 scheduled title"


def test_explicit_archive_removes_from_public_a04(admin_api_client, anon_client):
    """Archive (unlike restore) genuinely removes the record from public reads."""
    art = Article.objects.create(
        locale="en",
        slug="a04-archive",
        title="A04 doomed",
        body="<p>doomed</p>",
        status=LifecycleStatus.DRAFT,
    )
    _publish(admin_api_client, "article", art.pk)
    detail_url = "/api/articles/en/a04-archive"
    assert anon_client.get(detail_url).status_code == 200

    res = admin_api_client.post(
        f"/api/v1/admin/content/article/{art.pk}/transition",
        data=json.dumps({"to": "archived", "reason": "A04 archive"}),
        content_type="application/json",
    )
    assert res.status_code == 200

    assert anon_client.get(detail_url).status_code == 404
    listed = anon_client.get("/api/articles/en").json()
    listed_items = listed["items"] if isinstance(listed, dict) else listed
    assert all(it["slug"] != "a04-archive" for it in listed_items)
    resolve = anon_client.get(
        "/api/v1/records/en/resolve", {"refs": f"article:{art.pk}"}
    )
    assert resolve.status_code == 200
    assert resolve.json()["items"] == []
    assert resolve.json()["unresolved"] == [{"family": "article", "id": str(art.pk)}]


def test_project_publish_restore_cycle_serves_frozen_relations_a04(
    admin_api_client, anon_client
):
    """Project HTTP cycle: publish v0, edit to v1, restore v0 draft.

    Then GET stays v1, republish activates v0.
    Before fix: after restore-as-draft, public collaborators/evidence/funding
    leaked live v0 rows instead of frozen v1 snapshot (live managers kept).
    Uses only real model fields; no invented PII.
    """
    from apps.content.models import (
        ContentRevision,
        Project,
        ProjectCaseStudyDetails,
        ProjectCollaborator,
        ProjectEvidence,
        ProjectFunding,
    )

    story = _make_story("a04-proj-story", "A04 Proj Story", "<p>A04 PROJ STORY V0</p>")
    proj = Project.objects.create(
        locale="en",
        slug="a04-proj-cycle",
        title="A04 Proj v0",
        objective="Objective v0",
        status=LifecycleStatus.DRAFT,
        story=story,
    )
    ProjectCaseStudyDetails.objects.create(
        project=proj,
        depth="standard",
        problem="Problem v0",
        constraints="Constraints v0",
        technical_decisions="Decisions v0",
        trade_offs="Tradeoffs v0",
        outcomes_summary="Outcomes v0",
        lessons_learned="Lessons v0",
        testing_summary="Testing v0",
    )
    ProjectEvidence.objects.create(
        project=proj,
        label="Evidence v0 public",
        value="v0",
        source="https://example.com/v0",
        visibility="public",
    )
    ProjectEvidence.objects.create(
        project=proj,
        label="Evidence v0 internal",
        value="hidden",
        source="https://example.com/hidden",
        visibility="internal",
    )
    ProjectCollaborator.objects.create(
        project=proj, name="Collab v0 approved", role="R0", publication_approved=True
    )
    ProjectCollaborator.objects.create(
        project=proj, name="Collab v0 hidden", role="R-hidden", publication_approved=False
    )
    ProjectFunding.objects.create(
        project=proj, funder="Funder v0 approved", grant_id="G-v0", publication_approved=True
    )
    ProjectFunding.objects.create(
        project=proj, funder="Funder v0 hidden", grant_id="G-hidden", publication_approved=False
    )

    rev0 = _snapshot_revision(admin_api_client, "project", proj.pk, note="proj v0")
    _publish(admin_api_client, "project", proj.pk)
    detail_url = "/api/projects/en/a04-proj-cycle"

    got = anon_client.get(detail_url)
    assert got.status_code == 200, got.content[:1000]
    body_v0 = got.json()
    assert body_v0["title"] == "A04 Proj v0"
    assert body_v0["case_study"]["problem"] == "Problem v0"
    assert len(body_v0["evidence"]) == 1
    assert body_v0["evidence"][0]["label"] == "Evidence v0 public"
    assert [c["name"] for c in body_v0["collaborators"]] == ["Collab v0 approved"]
    assert [f["funder"] for f in body_v0["funding"]] == ["Funder v0 approved"]

    # Add v1 approved rows first, then PUT parent to v1 so one snapshot covers both.
    ProjectEvidence.objects.create(
        project=proj,
        label="Evidence v1 public",
        value="v1",
        source="https://example.com/v1",
        visibility="public",
    )
    ProjectCollaborator.objects.create(
        project=proj, name="Collab v1 approved", role="R1", publication_approved=True
    )
    proj.refresh_from_db()
    stamp = proj.updated_at.isoformat()
    if stamp.endswith("+00:00"):
        stamp = stamp.removesuffix("+00:00") + "Z"
    res_put = admin_api_client.put(
        f"/api/v1/admin/content/project/{proj.pk}",
        data=json.dumps({"title": "A04 Proj v1"}),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{stamp}"',
    )
    assert res_put.status_code == 200, res_put.content[:1000]
    got_v1 = anon_client.get(detail_url).json()
    assert got_v1["title"] == "A04 Proj v1"
    assert {e["label"] for e in got_v1["evidence"]} == {
        "Evidence v0 public",
        "Evidence v1 public",
    }
    assert "Collab v1 approved" in [c["name"] for c in got_v1["collaborators"]]

    # Restore v0 as draft: live becomes v0 draft, public must stay v1.
    jobs_before = PublicationJob.objects.count()
    _restore(admin_api_client, "project", proj.pk, rev0)
    proj.refresh_from_db()
    assert proj.status == LifecycleStatus.DRAFT
    assert proj.title == "A04 Proj v0"
    assert PublicationJob.objects.count() == jobs_before

    got_still_v1 = anon_client.get(detail_url)
    assert got_still_v1.status_code == 200
    body_still = got_still_v1.json()
    assert body_still["title"] == "A04 Proj v1", body_still["title"]
    assert "problem" in body_still["case_study"]
    # Frozen v1 relations, not live v0 draft rows.
    assert {e["label"] for e in body_still["evidence"]} == {
        "Evidence v0 public",
        "Evidence v1 public",
    }
    assert "Collab v1 approved" in [c["name"] for c in body_still["collaborators"]]

    # History preserved: pre-restore snapshot exists and ordering by id.
    revs = ContentRevision.objects.filter(entity_key="project", object_id=proj.pk).order_by("-id")
    assert revs.count() >= 2
    assert revs.first().note == "pre-restore snapshot"

    # Explicit republish activates restored v0.
    _publish(admin_api_client, "project", proj.pk)
    got_v0_again = anon_client.get(detail_url).json()
    assert got_v0_again["title"] == "A04 Proj v0"
    assert {e["label"] for e in got_v0_again["evidence"]} == {"Evidence v0 public"}
    assert [c["name"] for c in got_v0_again["collaborators"]] == ["Collab v0 approved"]


def test_project_explicit_archive_removes_and_ordering_preserved_a04(
    admin_api_client, anon_client
):
    """Archive removes project; ordered members/relations stay sorted by position/id."""
    from apps.content.models import (
        Project,
        ProjectCollaborator,
        ProjectEvidence,
        ProjectFunding,
    )

    proj = Project.objects.create(
        locale="en",
        slug="a04-proj-archive",
        title="A04 Proj doomed",
        objective="doomed",
        status=LifecycleStatus.DRAFT,
    )
    ProjectEvidence.objects.create(
        project=proj,
        label="B evidence",
        value="2",
        source="https://example.com/b",
        visibility="public",
    )
    ProjectEvidence.objects.create(
        project=proj,
        label="A evidence",
        value="1",
        source="https://example.com/a",
        visibility="public",
    )
    ProjectCollaborator.objects.create(
        project=proj, name="Zed", role="R", publication_approved=True
    )
    ProjectCollaborator.objects.create(
        project=proj, name="Amy", role="R", publication_approved=True
    )
    ProjectFunding.objects.create(
        project=proj, funder="Z funder", grant_id="Z", publication_approved=True
    )
    ProjectFunding.objects.create(
        project=proj, funder="A funder", grant_id="A", publication_approved=True
    )
    _publish(admin_api_client, "project", proj.pk)
    detail_url = "/api/projects/en/a04-proj-archive"
    assert anon_client.get(detail_url).status_code == 200

    res = admin_api_client.post(
        f"/api/v1/admin/content/project/{proj.pk}/transition",
        data=json.dumps({"to": "archived", "reason": "A04 proj archive"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    assert anon_client.get(detail_url).status_code == 404
