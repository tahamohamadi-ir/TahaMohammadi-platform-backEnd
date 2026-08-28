"""P6 case study model tests — OneToOne, featured publish gate, diagram/screenshot redact."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
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
    ProjectEvidence,
    ProjectScreenshot,
    ProjectType,
)


def past():
    return timezone.now() - timedelta(days=1)


def make_project(**overrides) -> Project:
    defaults = {
        "locale": Locale.EN,
        "slug": "edge-system",
        "title": "Edge System",
        "project_type": ProjectType.ENGINEERING,
        "objective": "Build edge pipeline",
        "role": "Lead engineer",
        "license": "cc-by-4",
        "code_availability": Availability.PUBLIC,
        "data_availability": Availability.NOT_AVAILABLE,
        "demo_availability": Availability.NOT_APPLICABLE,
        "status": LifecycleStatus.DRAFT,
    }
    defaults.update(overrides)
    return Project.objects.create(**defaults)


def make_case_study(project: Project, **overrides) -> ProjectCaseStudyDetails:
    defaults = {
        "project": project,
        "depth": CaseStudyDepth.STANDARD,
        "problem": "Latency at the edge",
        "trade_offs": "Simplicity over feature breadth",
        "outcomes_summary": "Stable deployment",
    }
    defaults.update(overrides)
    return ProjectCaseStudyDetails.objects.create(**defaults)


@pytest.mark.django_db
def test_case_study_one_to_one_integrity():
    project = make_project()
    make_case_study(project)
    with pytest.raises(IntegrityError):
        make_case_study(project)


@pytest.mark.django_db
def test_featured_publish_gate_blocks_incomplete_baseline():
    project = make_project(status=LifecycleStatus.PUBLISHED, published_at=past())
    case_study = ProjectCaseStudyDetails(
        project=project,
        depth=CaseStudyDepth.FEATURED,
        problem="",
        trade_offs="",
        outcomes_summary="",
    )
    with pytest.raises(ValidationError) as exc:
        case_study.full_clean()
    assert "problem" in exc.value.message_dict
    project.role = ""
    case_study.problem = "Defined problem"
    case_study.trade_offs = "Trade-offs"
    case_study.outcomes_summary = "Outcomes"
    with pytest.raises(ValidationError) as exc2:
        case_study.full_clean()
    assert "role" in exc2.value.message_dict


@pytest.mark.django_db
def test_featured_publish_gate_passes_with_complete_baseline():
    project = make_project(
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
        role="Lead",
        license="cc-by-4",
        code_availability=Availability.PUBLIC,
        data_availability=Availability.PRIVATE,
        demo_availability=Availability.NOT_APPLICABLE,
    )
    case_study = ProjectCaseStudyDetails(
        project=project,
        depth=CaseStudyDepth.FEATURED,
        problem="Problem statement",
        trade_offs="Trade-offs documented",
        outcomes_summary="Shipped on schedule",
    )
    case_study.full_clean()
    case_study.save()
    assert case_study.pk is not None


@pytest.mark.django_db
def test_standard_depth_skips_featured_gate():
    project = make_project(
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
        role="",
    )
    case_study = ProjectCaseStudyDetails(
        project=project,
        depth=CaseStudyDepth.STANDARD,
        problem="",
        trade_offs="",
        outcomes_summary="",
    )
    case_study.full_clean()
    case_study.save()


@pytest.mark.django_db
def test_diagram_public_requires_alt_version_date():
    project = make_project()
    public_ok = ProjectDiagram.objects.create(
        project=project,
        title="Architecture",
        version="1.0",
        diagram_date=date(2024, 3, 1),
        alt_text="Boxes and arrows",
        visibility=EvidenceVisibility.PUBLIC,
    )
    missing_alt = ProjectDiagram.objects.create(
        project=project,
        title="Hidden",
        version="1.0",
        diagram_date=date(2024, 3, 1),
        alt_text="",
        visibility=EvidenceVisibility.PUBLIC,
    )
    internal = ProjectDiagram.objects.create(
        project=project,
        title="Internal",
        version="1.0",
        diagram_date=date(2024, 3, 1),
        alt_text="Alt",
        visibility=EvidenceVisibility.INTERNAL,
    )
    assert public_ok.is_publicly_projectable() is True
    assert missing_alt.is_publicly_projectable() is False
    assert internal.is_publicly_projectable() is False


@pytest.mark.django_db
def test_screenshot_public_requires_alt_and_caption():
    project = make_project()
    public_ok = ProjectScreenshot.objects.create(
        project=project,
        caption="Dashboard",
        alt_text="Metrics dashboard",
        visibility=EvidenceVisibility.PUBLIC,
        external_url="https://example.com/demo",
    )
    missing_caption = ProjectScreenshot.objects.create(
        project=project,
        caption="",
        alt_text="Alt only",
        visibility=EvidenceVisibility.PUBLIC,
    )
    assert public_ok.is_publicly_projectable() is True
    assert public_ok.public_external_url() == "https://example.com/demo"
    assert missing_caption.is_publicly_projectable() is False


@pytest.mark.django_db
def test_evidence_redact_unchanged():
    project = make_project()
    public_row = ProjectEvidence.objects.create(
        project=project,
        label="Latency",
        value="120ms p95",
        source="load test log",
        visibility=EvidenceVisibility.PUBLIC,
    )
    internal_row = ProjectEvidence.objects.create(
        project=project,
        label="Secret",
        value="x",
        source="notes",
        visibility=EvidenceVisibility.INTERNAL,
    )
    assert public_row.is_publicly_projectable() is True
    assert internal_row.is_publicly_projectable() is False
