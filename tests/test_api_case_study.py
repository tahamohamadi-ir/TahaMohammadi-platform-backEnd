"""P6 case study API tests — has_case_study filter, redact, forbidden fields."""

from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Availability,
    CaseStudyDepth,
    EvidenceVisibility,
    LifecycleStatus,
    Locale,
    Project,
    ProjectCaseStudyDetails,
    ProjectDiagram,
    ProjectScreenshot,
    ProjectType,
)

CASE_STUDY_FORBIDDEN = {
    "status",
    "created_at",
    "visibility",
    "diagram_image",
    "screenshot_image",
    "publication_approved",
}


@pytest.fixture
def api_client():
    return Client()


def past():
    return timezone.now() - timedelta(days=1)


@pytest.fixture
def case_study_content(db):
    project = Project.objects.create(
        locale=Locale.EN,
        slug="edge-pipeline",
        title="Edge Pipeline",
        project_type=ProjectType.ENGINEERING,
        objective="Reduce latency",
        methods_summary="Edge ML",
        role="Lead engineer",
        license="cc-by-4",
        code_availability=Availability.PUBLIC,
        data_availability=Availability.PRIVATE,
        demo_availability=Availability.NOT_APPLICABLE,
        code_url="https://example.com/code",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    ProjectCaseStudyDetails.objects.create(
        project=project,
        depth=CaseStudyDepth.FEATURED,
        problem="Latency at the edge",
        constraints="Limited device memory",
        technical_decisions="<p>Use ONNX</p><script>alert(1)</script>",
        trade_offs="Accuracy vs latency",
        outcomes_summary="Stable rollout",
        lessons_learned="Start with baseline metrics",
        testing_summary="Load tests on device farm",
    )
    Project.objects.create(
        locale=Locale.EN,
        slug="no-case-study",
        title="Plain project",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Project.objects.create(
        locale=Locale.EN,
        slug="draft-case-study",
        title="Draft",
        status=LifecycleStatus.DRAFT,
    )
    ProjectDiagram.objects.create(
        project=project,
        title="Architecture",
        version="2.1",
        diagram_date=date(2024, 5, 1),
        alt_text="Service boxes",
        long_description="Detailed flow",
        visibility=EvidenceVisibility.PUBLIC,
    )
    ProjectDiagram.objects.create(
        project=project,
        title="Internal",
        version="1.0",
        diagram_date=date(2024, 4, 1),
        alt_text="Hidden",
        visibility=EvidenceVisibility.INTERNAL,
    )
    ProjectScreenshot.objects.create(
        project=project,
        caption="Dashboard",
        alt_text="Metrics dashboard",
        external_url="https://example.com/demo",
        visibility=EvidenceVisibility.PUBLIC,
    )
    ProjectScreenshot.objects.create(
        project=project,
        caption="",
        alt_text="Missing caption",
        visibility=EvidenceVisibility.PUBLIC,
    )
    return {"project": project}


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


def _items(payload):
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    return payload


def test_list_projects_includes_published_without_case_study(
    api_client, case_study_content
):
    data = assert_json(api_client.get("/api/projects/en"), 200)
    items = _items(data)
    assert {i["slug"] for i in items} == {"edge-pipeline", "no-case-study"}
    by_slug = {i["slug"]: i for i in items}
    assert by_slug["edge-pipeline"]["has_case_study"] is True
    assert by_slug["edge-pipeline"]["case_study_depth"] == CaseStudyDepth.FEATURED
    assert by_slug["no-case-study"]["has_case_study"] is False


def test_list_projects_has_case_study_filter(api_client, case_study_content):
    data = assert_json(api_client.get("/api/projects/en?has_case_study=true"), 200)
    items = _items(data)
    assert [i["slug"] for i in items] == ["edge-pipeline"]


def test_list_projects_hides_unlisted(api_client, case_study_content):
    Project.objects.filter(slug="no-case-study").update(show_on_projects=False)
    data = assert_json(api_client.get("/api/projects/en"), 200)
    assert [i["slug"] for i in _items(data)] == ["edge-pipeline"]
    hidden = api_client.get("/api/projects/en/no-case-study")
    assert hidden.status_code == 404


def test_project_without_case_study_returns_detail(api_client, case_study_content):
    data = assert_json(api_client.get("/api/projects/en/no-case-study"), 200)
    assert data["slug"] == "no-case-study"
    assert data["has_case_study"] is False
    assert data["case_study"] is None


def test_project_detail_includes_case_study_and_redacts_media(
    api_client, case_study_content
):
    data = assert_json(api_client.get("/api/projects/en/edge-pipeline"), 200)
    assert data["case_study"]["problem"] == "Latency at the edge"
    assert "<script>" not in data["case_study"]["technical_decisions"]
    assert [d["title"] for d in data["diagrams"]] == ["Architecture"]
    assert [s["caption"] for s in data["screenshots"]] == ["Dashboard"]
    assert data["screenshots"][0]["external_url"] == "https://example.com/demo"
    assert "diagram_image" not in str(data)
    assert "screenshot_image" not in str(data)
    assert CASE_STUDY_FORBIDDEN.isdisjoint(data)
    assert CASE_STUDY_FORBIDDEN.isdisjoint(data["case_study"])


def test_draft_project_404(api_client, case_study_content):
    response = api_client.get("/api/projects/en/draft-case-study")
    assert response.status_code == 404


def test_research_project_includes_has_case_study(api_client, case_study_content):
    data = assert_json(api_client.get("/api/research/projects/en/edge-pipeline"), 200)
    assert data["has_case_study"] is True
    assert data["case_study"]["depth"] == CaseStudyDepth.FEATURED
