"""Tests for PU-04-project-evidence: atomic admin editing of project case-study,
evidence, collaborators, and funding with parent If-Match.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-project-evidence.md
"""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.content.models import (
    CaseStudyDepth,
    ContentRevision,
    EvidenceVisibility,
    Project,
    ProjectCaseStudyDetails,
    ProjectCollaborator,
    ProjectEvidence,
    ProjectFunding,
)
from apps.security.models import AuditLog


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


def _create_test_project(
    slug: str = "project-alpha", locale: str = "fa", status: str = "published"
) -> Project:
    return Project.objects.create(
        locale=locale,
        slug=slug,
        title="پروژه تست آلفا",
        objective="اهداف پروژه تست",
        status=status,
        published_at=timezone.now() if status == "published" else None,
    )


def test_admin_get_project_case_study(admin_api_client):
    """GET /api/v1/admin/content/project/{id}/case-study returns aggregate state."""
    project = _create_test_project()

    # 1. Project without case-study rows returns defaults/empty lists
    res = admin_api_client.get(f"/api/v1/admin/content/project/{project.pk}/case-study")
    assert res.status_code == 200
    data = res.json()
    assert data["projectId"] == project.pk
    assert data["evidence"] == []
    assert data["collaborators"] == []
    assert data["funding"] == []
    assert "updatedAt" in data

    # 2. Add existing rows
    cs = ProjectCaseStudyDetails.objects.create(
        project=project,
        depth=CaseStudyDepth.STANDARD,
        problem="مسئله پیچیده سیستم",
        constraints="محدودیت‌های منابع",
        technical_decisions="انتخاب معماری میکروسرویس",
        trade_offs="پیچیدگی در برابر مقیاس‌پذیری",
        outcomes_summary="افزایش ۵۰ درصدی کارایی",
        lessons_learned="نیاز به مانیتورینگ دقیق",
        testing_summary="تست‌های بار و فشار",
    )
    ev = ProjectEvidence.objects.create(
        project=project,
        label="بنچمارک تاخیر",
        value="12ms p99",
        source="گزارش لود تست داخلی",
        last_verified=datetime.date(2026, 7, 1),
        visibility=EvidenceVisibility.PUBLIC,
    )
    collab = ProjectCollaborator.objects.create(
        project=project,
        name="سارا حسینی",
        role="مهندس ارشد داده",
        publication_approved=True,
    )
    funding = ProjectFunding.objects.create(
        project=project,
        funder="بنیاد ملی علم",
        grant_id="GR-2026-001",
        publication_approved=True,
    )

    res2 = admin_api_client.get(f"/api/v1/admin/content/project/{project.pk}/case-study")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["details"]["problem"] == cs.problem
    assert data2["details"]["technical_decisions"] == cs.technical_decisions
    assert len(data2["evidence"]) == 1
    assert data2["evidence"][0]["id"] == ev.pk
    assert data2["evidence"][0]["label"] == "بنچمارک تاخیر"
    assert data2["evidence"][0]["visibility"] == "public"
    assert len(data2["collaborators"]) == 1
    assert data2["collaborators"][0]["id"] == collab.pk
    assert data2["collaborators"][0]["name"] == "سارا حسینی"
    assert data2["collaborators"][0]["publication_approved"] is True
    assert len(data2["funding"]) == 1
    assert data2["funding"][0]["id"] == funding.pk
    assert data2["funding"][0]["funder"] == "بنیاد ملی علم"

    # 3. 404 for unknown project
    res_404 = admin_api_client.get("/api/v1/admin/content/project/999999/case-study")
    assert res_404.status_code == 404


def test_admin_put_project_case_study_if_match_and_atomic_update(admin_api_client):
    """PUT /project/{id}/case-study enforces parent If-Match and updates atomically."""
    project = _create_test_project()

    # 1. Missing If-Match raises 409 STALE_REVISION (or 428)
    payload = {
        "details": {
            "depth": "standard",
            "problem": "مسئله جدید",
            "constraints": "محدودیت جدید",
        },
        "evidence": [
            {
                "label": "آمار جدید",
                "value": "99.9%",
                "source": "تست سیستم",
                "visibility": "public",
            }
        ],
        "collaborators": [
            {"name": "علی رضایی", "role": "توسعه‌دهنده", "publication_approved": True}
        ],
        "funding": [
            {"funder": "حامی مالی", "grant_id": "GRANT-1", "publication_approved": True}
        ],
    }
    res_no_if_match = admin_api_client.put(
        f"/api/v1/admin/content/project/{project.pk}/case-study",
        payload,
        content_type="application/json",
    )
    assert res_no_if_match.status_code in (409, 428)

    # 2. Stale If-Match raises 409
    res_stale = admin_api_client.put(
        f"/api/v1/admin/content/project/{project.pk}/case-study",
        payload,
        content_type="application/json",
        HTTP_IF_MATCH="2020-01-01T00:00:00.000Z",
    )
    assert res_stale.status_code == 409

    # 3. Valid If-Match succeeds and creates rows
    get_res = admin_api_client.get(f"/api/v1/admin/content/project/{project.pk}/case-study")
    up_to_date_revision = get_res.json()["updatedAt"]

    res_ok = admin_api_client.put(
        f"/api/v1/admin/content/project/{project.pk}/case-study",
        payload,
        content_type="application/json",
        HTTP_IF_MATCH=up_to_date_revision,
    )
    assert res_ok.status_code == 200
    res_data = res_ok.json()
    assert res_data["details"]["problem"] == "مسئله جدید"
    assert len(res_data["evidence"]) == 1
    ev_id = res_data["evidence"][0]["id"]
    assert ev_id > 0
    assert len(res_data["collaborators"]) == 1
    collab_id = res_data["collaborators"][0]["id"]
    assert len(res_data["funding"]) == 1
    funding_id = res_data["funding"][0]["id"]

    # Verify rows in DB
    assert ProjectEvidence.objects.filter(pk=ev_id, project=project).exists()
    assert ProjectCollaborator.objects.filter(pk=collab_id, project=project).exists()
    assert ProjectFunding.objects.filter(pk=funding_id, project=project).exists()

    # 4. Verify Revision and AuditLog created
    rev = (
        ContentRevision.objects.filter(entity_key="project", object_id=project.pk)
        .order_by("-id")
        .first()
    )
    assert rev is not None
    assert rev.snapshot is not None
    assert AuditLog.objects.filter(model_name="project", object_id=str(project.pk)).exists()

    # 5. Full aggregate update: update existing, add new, remove omitted
    next_revision = res_data["updatedAt"]
    payload_update = {
        "details": {
            "depth": "standard",
            "problem": "مسئله به‌روزشده",
        },
        "evidence": [
            # Update existing
            {
                "id": ev_id,
                "label": "آمار به‌روزشده",
                "value": "99.99%",
                "source": "تست دوم",
                "visibility": "internal",
            },
            # Add new
            {
                "label": "شواهد دوم",
                "value": "10x",
                "source": "منبع دوم",
                "visibility": "public",
            },
        ],
        # Omit collaborator -> deleted!
        "collaborators": [],
        # Keep funding
        "funding": [
            {
                "id": funding_id,
                "funder": "حامی مالی ۲",
                "grant_id": "GRANT-2",
                "publication_approved": False,
            }
        ],
    }
    res_update = admin_api_client.put(
        f"/api/v1/admin/content/project/{project.pk}/case-study",
        payload_update,
        content_type="application/json",
        HTTP_IF_MATCH=next_revision,
    )
    assert res_update.status_code == 200
    updated_json = res_update.json()
    assert len(updated_json["evidence"]) == 2
    assert updated_json["evidence"][0]["label"] == "آمار به‌روزشده"
    assert updated_json["evidence"][0]["visibility"] == "internal"
    assert len(updated_json["collaborators"]) == 0
    assert len(updated_json["funding"]) == 1

    # In DB: collaborator was deleted
    assert not ProjectCollaborator.objects.filter(pk=collab_id).exists()
    # Funding updated
    f_db = ProjectFunding.objects.get(pk=funding_id)
    assert f_db.funder == "حامی مالی ۲"
    assert f_db.publication_approved is False


def test_admin_put_project_case_study_rejects_foreign_ids_and_invalid_enums(admin_api_client):
    """PUT /case-study rejects IDs belonging to other projects and invalid enum values."""
    proj1 = _create_test_project("project-one")
    proj2 = _create_test_project("project-two")

    ev_other = ProjectEvidence.objects.create(
        project=proj2,
        label="Evidence other",
        visibility=EvidenceVisibility.PUBLIC,
    )
    collab_other = ProjectCollaborator.objects.create(
        project=proj2,
        name="Collab other",
    )
    funding_other = ProjectFunding.objects.create(
        project=proj2,
        funder="Funder other",
    )

    get_res = admin_api_client.get(f"/api/v1/admin/content/project/{proj1.pk}/case-study")
    rev = get_res.json()["updatedAt"]

    # 1. Reject foreign evidence ID
    res_ev = admin_api_client.put(
        f"/api/v1/admin/content/project/{proj1.pk}/case-study",
        {"evidence": [{"id": ev_other.pk, "label": "Label"}]},
        content_type="application/json",
        HTTP_IF_MATCH=rev,
    )
    assert res_ev.status_code == 400
    assert res_ev.json()["code"] == "VALIDATION"

    # 2. Reject foreign collaborator ID
    res_collab = admin_api_client.put(
        f"/api/v1/admin/content/project/{proj1.pk}/case-study",
        {"collaborators": [{"id": collab_other.pk, "name": "Name"}]},
        content_type="application/json",
        HTTP_IF_MATCH=rev,
    )
    assert res_collab.status_code == 400
    assert res_collab.json()["code"] == "VALIDATION"

    # 3. Reject foreign funding ID
    res_funding = admin_api_client.put(
        f"/api/v1/admin/content/project/{proj1.pk}/case-study",
        {"funding": [{"id": funding_other.pk, "funder": "Funder"}]},
        content_type="application/json",
        HTTP_IF_MATCH=rev,
    )
    assert res_funding.status_code == 400
    assert res_funding.json()["code"] == "VALIDATION"

    # 4. Reject invalid depth enum
    res_depth = admin_api_client.put(
        f"/api/v1/admin/content/project/{proj1.pk}/case-study",
        {"details": {"depth": "invalid_depth"}},
        content_type="application/json",
        HTTP_IF_MATCH=rev,
    )
    assert res_depth.status_code == 400
    assert res_depth.json()["code"] == "VALIDATION"

    # 5. Reject invalid visibility enum
    res_vis = admin_api_client.put(
        f"/api/v1/admin/content/project/{proj1.pk}/case-study",
        {"evidence": [{"label": "Label", "visibility": "super_secret"}]},
        content_type="application/json",
        HTTP_IF_MATCH=rev,
    )
    assert res_vis.status_code == 400
    assert res_vis.json()["code"] == "VALIDATION"


def test_public_project_visibility_preserves_privacy(admin_api_client):
    """Public GET /api/projects/{locale}/{slug} hides internal/restricted rows."""
    client = Client()
    project = _create_test_project("public-project-gates")

    get_res = admin_api_client.get(f"/api/v1/admin/content/project/{project.pk}/case-study")
    rev = get_res.json()["updatedAt"]

    # Populate mixed visibility
    payload = {
        "details": {
            "depth": "standard",
            "problem": "مسئله عمومی",
            "technical_decisions": "تصمیمات فنی عمومی",
        },
        "evidence": [
            {
                "label": "شواهد عمومی",
                "value": "100%",
                "source": "گزارش شفاف",
                "visibility": "public",
            },
            {
                "label": "شواهد داخلی",
                "value": "محرمانه ۱",
                "source": "لاگ سرور",
                "visibility": "internal",
            },
            {
                "label": "شواهد محرمانه",
                "value": "محرمانه ۲",
                "source": "اسناد سری",
                "visibility": "restricted",
            },
        ],
        "collaborators": [
            {"name": "همکار تایید شده", "role": "مهندس", "publication_approved": True},
            {"name": "همکار تایید نشده", "role": "مشاور", "publication_approved": False},
        ],
        "funding": [
            {"funder": "بنیاد عمومی", "grant_id": "PUB-1", "publication_approved": True},
            {"funder": "حامی خصوصی", "grant_id": "PRIV-1", "publication_approved": False},
        ],
    }
    put_res = admin_api_client.put(
        f"/api/v1/admin/content/project/{project.pk}/case-study",
        payload,
        content_type="application/json",
        HTTP_IF_MATCH=rev,
    )
    assert put_res.status_code == 200, put_res.json()

    # Query public endpoint
    pub_res = client.get(f"/api/projects/fa/{project.slug}")
    assert pub_res.status_code == 200
    pub_data = pub_res.json()

    # 1. Case study details exposed
    assert pub_data["case_study"] is not None
    assert pub_data["case_study"]["problem"] == "مسئله عمومی"

    # 2. Evidence: ONLY public evidence with non-empty source is included
    assert len(pub_data["evidence"]) == 1
    assert pub_data["evidence"][0]["label"] == "شواهد عمومی"
    assert not any(e["label"] in ("شواهد داخلی", "شواهد محرمانه") for e in pub_data["evidence"])

    # 3. Collaborators: ONLY publication_approved is included
    assert len(pub_data["collaborators"]) == 1
    assert pub_data["collaborators"][0]["name"] == "همکار تایید شده"

    # 4. Funding: ONLY publication_approved is included
    assert len(pub_data["funding"]) == 1
    assert pub_data["funding"][0]["funder"] == "بنیاد عمومی"
