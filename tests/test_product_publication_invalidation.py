"""Tests for PU-23-invalidation: Enqueue all publish/archive/restore/schedule/graph/settings
changes with affected paths and removal status; no backend search engine.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I06 / §I07
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-23-invalidation.md
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.content.models import (
    Article,
    ContentRevision,
    Course,
    Download,
    GraphVersion,
    GraphVersionStatus,
    Lesson,
    LifecycleStatus,
    Series,
)
from apps.content.services.lifecycle import bulk_archive_items
from apps.media.models import Media
from apps.rebuild.models import PublicationJob
from apps.rebuild.services import compute_affected_paths
from apps.siteconfig.models import LocalizedSiteSettings


@pytest.fixture(autouse=True)
def db_access(db):
    """Ensure database access for all tests."""
    pass


@pytest.fixture
def admin_user():
    """Create superuser with verified OTP device."""
    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        username="admin-inval",
        email="admin-inval@example.com",
        password="ValidPassword123!",
    )
    TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    return user


@pytest.fixture
def admin_client(admin_user):
    """Client with staff session and verified OTP device."""
    device = TOTPDevice.objects.get(user=admin_user)
    client = Client()
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session["django_otp_device_id"] = device.persistent_id
    session.save()
    csrf_token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = csrf_token
    return client


def test_canonical_affected_paths_computation():
    """Verify compute_affected_paths computes canonical frontend routes per §I02."""
    # 1. Article with series
    series = Series.objects.create(locale="en", slug="deep-dive", title="Deep Dive")
    article = Article.objects.create(
        locale="en", slug="intro", title="Intro"
    )
    article.series.set([series])
    art_paths = compute_affected_paths("article", article)
    assert "/en/" in art_paths
    assert "/en/blog/" in art_paths
    assert "/en/blog/intro/" in art_paths
    assert "/en/blog/series/deep-dive/" in art_paths

    # 2. Lesson with parent course
    course = Course.objects.create(locale="fa", slug="cs101", title="CS 101")
    lesson = Lesson.objects.create(course=course, locale="fa", slug="lec1", title="Lecture 1")
    les_paths = compute_affected_paths("lesson", lesson)
    assert "/fa/" in les_paths
    assert "/fa/education/" in les_paths
    assert "/fa/education/cs101/" in les_paths
    assert "/fa/education/cs101/lessons/lec1/" in les_paths

    # 3. Gated download file
    media = Media.objects.create(title="Doc", is_active=True)
    dl = Download.objects.create(locale="en", slug="whitepaper", title="Whitepaper", media=media)
    dl_paths = compute_affected_paths("download", dl)
    assert "/en/" in dl_paths
    assert "/en/resources/" in dl_paths
    assert "/en/resources/whitepaper/" in dl_paths
    assert "/en/resources/whitepaper/file/" in dl_paths


def test_publish_transition_enqueues_publication_job(admin_client):
    """Transitioning an item to published enqueues job with removal_state='not_requested'."""
    PublicationJob.objects.all().delete()
    article = Article.objects.create(
        locale="en",
        slug="new-article",
        title="New Article",
        body="<p>draft</p>",
        status=LifecycleStatus.DRAFT,
    )

    res = admin_client.post(
        f"/api/v1/admin/content/article/{article.pk}/transition",
        data={"to": "published", "reason": "Going live"},
        content_type="application/json",
    )
    assert res.status_code == 200

    job = PublicationJob.objects.filter(locale="en").latest("created_at")
    assert job.removal_state == "not_requested"
    assert "/en/" in job.affected_paths
    assert "/en/blog/new-article/" in job.affected_paths


def test_archive_transition_enqueues_removal_job(admin_client):
    """Transitioning an item to archived enqueues job with removal_state='pending'."""
    PublicationJob.objects.all().delete()
    article = Article.objects.create(
        locale="en",
        slug="arch-article",
        title="Arch Article",
        body="<p>content</p>",
        status=LifecycleStatus.PUBLISHED,
    )

    res = admin_client.post(
        f"/api/v1/admin/content/article/{article.pk}/transition",
        data={"to": "archived", "reason": "Retired"},
        content_type="application/json",
    )
    assert res.status_code == 200

    job = PublicationJob.objects.filter(locale="en").latest("created_at")
    assert job.removal_state == "pending"
    assert "/en/blog/arch-article/" in job.affected_paths


def test_bulk_archive_enqueues_coalesced_removal_job(admin_user):
    """Bulk archiving multiple items coalesces affected paths with removal_state='pending'."""
    PublicationJob.objects.all().delete()
    a1 = Article.objects.create(
        locale="fa", slug="fa-art-1", title="Article 1", status=LifecycleStatus.PUBLISHED
    )
    a2 = Article.objects.create(
        locale="fa", slug="fa-art-2", title="Article 2", status=LifecycleStatus.PUBLISHED
    )

    res = bulk_archive_items(
        Article,
        entity="article",
        ids=[a1.pk, a2.pk],
        reason="Bulk unpublish",
        user=admin_user,
        ip="127.0.0.1",
    )
    assert res["archived"] == 2

    job = PublicationJob.objects.filter(locale="fa").latest("created_at")
    assert job.removal_state == "pending"
    assert "/fa/blog/fa-art-1/" in job.affected_paths
    assert "/fa/blog/fa-art-2/" in job.affected_paths


def test_bulk_archive_mixed_locales_enqueues_per_locale_jobs_a08(admin_user):
    """A08: mixed-locale bulk archive must not attribute paths to the wrong locale.

    Regression: all paths went into one job tagged with the last item's locale.
    """
    PublicationJob.objects.all().delete()
    fa_art = Article.objects.create(
        locale="fa", slug="a08-fa-art", title="FA Article", status=LifecycleStatus.PUBLISHED
    )
    en_art = Article.objects.create(
        locale="en", slug="a08-en-art", title="EN Article", status=LifecycleStatus.PUBLISHED
    )

    res = bulk_archive_items(
        Article,
        entity="article",
        ids=[fa_art.pk, en_art.pk],
        reason="A08 mixed locales",
        user=admin_user,
        ip="127.0.0.1",
    )
    assert res["archived"] == 2

    fa_jobs = list(PublicationJob.objects.filter(locale="fa").order_by("created_at"))
    en_jobs = list(PublicationJob.objects.filter(locale="en").order_by("created_at"))
    assert len(fa_jobs) == 1
    assert len(en_jobs) == 1

    fa_paths = fa_jobs[0].affected_paths
    en_paths = en_jobs[0].affected_paths
    assert "/fa/blog/a08-fa-art/" in fa_paths
    assert not [p for p in fa_paths if p.startswith("/en/")]
    assert "/en/blog/a08-en-art/" in en_paths
    assert not [p for p in en_paths if p.startswith("/fa/")]
    assert fa_jobs[0].removal_state == "pending"
    assert en_jobs[0].removal_state == "pending"
    # No null/multi-locale catch-all job may absorb the mixed paths.
    assert PublicationJob.objects.filter(locale__isnull=True).count() == 0


def test_scheduled_content_publishing_enqueues_publication_job():
    """Management command publish_scheduled_content publishes and enqueues jobs."""
    PublicationJob.objects.all().delete()
    past_time = timezone.now() - timezone.timedelta(minutes=5)
    article = Article.objects.create(
        locale="en",
        slug="sched-post",
        title="Scheduled Post",
        status=LifecycleStatus.SCHEDULED,
        scheduled_for=past_time,
    )

    out = StringIO()
    call_command("publish_scheduled_content", stdout=out)

    article.refresh_from_db()
    assert article.status == LifecycleStatus.PUBLISHED

    job = PublicationJob.objects.filter(locale="en").latest("created_at")
    assert job.removal_state == "not_requested"
    assert "/en/blog/sched-post/" in job.affected_paths


def test_graph_activation_enqueues_publication_job(admin_client):
    """Activating a draft graph version enqueues a publication job for that locale."""
    PublicationJob.objects.all().delete()
    gv = GraphVersion.objects.create(
        locale="en",
        status=GraphVersionStatus.DRAFT,
    )

    res = admin_client.post(f"/api/v1/admin/graph/versions/{gv.pk}/activate")
    assert res.status_code == 200

    job = PublicationJob.objects.filter(locale="en").latest("created_at")
    assert job.removal_state == "not_requested"
    assert "/en/" in job.affected_paths
    assert "/en/graph/" in job.affected_paths


def test_localized_site_settings_publish_enqueues_publication_job(admin_client):
    """Publishing localized site settings enqueues a publication job for that locale."""
    PublicationJob.objects.all().delete()
    LocalizedSiteSettings.objects.get_or_create(
        locale="fa",
        defaults={
            "brand_name": "طاها",
            "tagline": "پژوهشگر",
            "status": "draft",
        },
    )

    res = admin_client.post("/api/v1/admin/site/fa/publish")
    assert res.status_code == 200

    job = PublicationJob.objects.filter(locale="fa").latest("created_at")
    assert job.removal_state == "not_requested"
    assert "/fa/" in job.affected_paths
    assert "/fa/site/" in job.affected_paths


def test_home_modules_update_enqueues_publication_job(admin_client):
    """Updating home composition modules enqueues a publication job."""
    PublicationJob.objects.all().delete()
    # GET current revision first
    get_res = admin_client.get("/api/v1/admin/home-modules/en")
    assert get_res.status_code == 200
    rev = get_res.json()["revision"]

    put_res = admin_client.put(
        "/api/v1/admin/home-modules/en",
        data={
            "modules": [
                {
                    "key": "identity",
                    "visible": True,
                    "order": 1,
                    "selection_mode": "manual",
                    "provenance_note": "",
                }
            ]
        },
        content_type="application/json",
        HTTP_IF_MATCH=rev,
    )
    assert put_res.status_code == 200

    job = PublicationJob.objects.filter(locale="en").latest("created_at")
    assert job.removal_state == "not_requested"
    assert "/en/" in job.affected_paths


def test_content_revisions_restore_previously_published_keeps_public_no_removal(
    admin_client,
):
    """A04: restoring a revision of a published item is a draft edit, not an archive.

    It forces draft and preserves history, but enqueues no removal job: the
    published snapshot keeps serving until explicit re-publication.
    """
    PublicationJob.objects.all().delete()
    article = Article.objects.create(
        locale="en",
        slug="restore-art",
        title="Restore Art",
        body="<p>live</p>",
        status=LifecycleStatus.PUBLISHED,
    )
    rev = ContentRevision.objects.create(
        entity_key="article",
        object_id=article.pk,
        snapshot={"fields": {"title": "Old Art", "slug": "restore-art"}},
        note="first version",
    )

    res = admin_client.post(
        f"/api/v1/admin/content/article/{article.pk}/revisions/{rev.pk}/restore"
    )
    assert res.status_code == 200

    article.refresh_from_db()
    assert article.status == LifecycleStatus.DRAFT

    assert PublicationJob.objects.count() == 0


def test_archive_splits_rebuild_and_revoke_paths_a01(admin_client):
    """A01: archiving one article must rebuild home/blog but revoke only its detail.

    Regression: affectedPaths doubled as the deny list, so an archive could 404
    home and the blog index. revokedPaths must exclude every shared page.
    """
    PublicationJob.objects.all().delete()
    article = Article.objects.create(
        locale="en",
        slug="a01-article",
        title="A01 Article",
        body="<p>live</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )

    res = admin_client.post(
        f"/api/v1/admin/content/article/{article.pk}/transition",
        data={"to": "archived", "reason": "A01 retire"},
        content_type="application/json",
    )
    assert res.status_code == 200

    job = PublicationJob.objects.filter(locale="en").latest("created_at")
    assert job.removal_state == "pending"
    wire = job.to_dict()
    # Rebuild set still refreshes shared pages (index/search pick up the removal).
    assert "/en/" in wire["affectedPaths"]
    assert "/en/blog/" in wire["affectedPaths"]
    assert "/en/blog/a01-article/" in wire["affectedPaths"]
    # Revoke set is the single detail URL only.
    assert wire["revokedPaths"] == ["/en/blog/a01-article/"]
    assert "/en/" not in wire["revokedPaths"]
    assert "/en/blog/" not in wire["revokedPaths"]

    # Admin wire shape exposes the split as well.
    detail = admin_client.get(f"/api/v1/admin/publication-jobs/{job.id}")
    assert detail.status_code == 200
    assert detail.json()["revokedPaths"] == ["/en/blog/a01-article/"]
