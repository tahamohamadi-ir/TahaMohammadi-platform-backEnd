"""P5 research model tests — uniqueness, public(), redact gates, statement rule."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from apps.content.models import (
    Availability,
    EvidenceVisibility,
    LifecycleStatus,
    Locale,
    Project,
    ProjectCollaborator,
    ProjectEvidence,
    ProjectFunding,
    ProjectType,
    Publication,
    ResearchStatement,
    ResearchTopic,
)


def past():
    return timezone.now() - timedelta(days=1)


def make_topic(**overrides) -> ResearchTopic:
    defaults = {
        "locale": Locale.EN,
        "slug": "hci",
        "title": "HCI",
        "summary": "Human-computer interaction",
        "status": LifecycleStatus.PUBLISHED,
        "published_at": past(),
    }
    defaults.update(overrides)
    return ResearchTopic.objects.create(**defaults)


def make_project(**overrides) -> Project:
    defaults = {
        "locale": Locale.EN,
        "slug": "vtd-edge",
        "title": "VTD-Edge",
        "project_type": ProjectType.RESEARCH,
        "objective": "Study ADHD attention",
        "methods_summary": "Edge ML",
        "role": "Lead",
        "license": "cc-by-4",
        "code_availability": Availability.PUBLIC,
        "data_availability": Availability.RESTRICTED,
        "demo_availability": Availability.NOT_AVAILABLE,
        "code_url": "https://example.com/code",
        "data_url": "https://example.com/private-data",
        "status": LifecycleStatus.PUBLISHED,
        "published_at": past(),
    }
    defaults.update(overrides)
    return Project.objects.create(**defaults)


def make_publication(**overrides) -> Publication:
    defaults = {
        "locale": Locale.EN,
        "slug": "paper-one",
        "title": "Paper One",
        "authors": "Taha Mohammadi",
        "venue": "Venue",
        "date": date(2024, 1, 1),
        "doi": "10.1000/test",
        "license": "cc-by-4",
        "status": LifecycleStatus.PUBLISHED,
        "published_at": past(),
    }
    defaults.update(overrides)
    return Publication.objects.create(**defaults)


@pytest.mark.django_db
def test_research_topic_unique_per_locale():
    make_topic(slug="shared")
    make_topic(locale=Locale.FA, slug="shared", title="مشترک")
    with pytest.raises(IntegrityError):
        make_topic(slug="shared", title="Dup")


@pytest.mark.django_db
def test_research_topic_public_excludes_draft():
    make_topic(slug="draft", status=LifecycleStatus.DRAFT, published_at=None)
    published = make_topic(slug="live")
    assert list(ResearchTopic.objects.public()) == [published]


@pytest.mark.django_db
def test_project_public_urls_gated_by_availability():
    project = make_project(
        code_availability=Availability.PUBLIC,
        data_availability=Availability.RESTRICTED,
        demo_availability=Availability.PRIVATE,
        code_url="https://example.com/code",
        data_url="https://example.com/data",
        demo_url="https://example.com/demo",
    )
    assert project.public_code_url() == "https://example.com/code"
    assert project.public_data_url() == ""
    assert project.public_demo_url() == ""


@pytest.mark.django_db
def test_evidence_without_source_not_publicly_projectable():
    project = make_project()
    with_source = ProjectEvidence.objects.create(
        project=project,
        label="Accuracy",
        value="90%",
        source="internal eval log",
        last_verified=date(2024, 6, 1),
        visibility=EvidenceVisibility.PUBLIC,
    )
    no_source = ProjectEvidence.objects.create(
        project=project,
        label="Secret metric",
        value="99%",
        source="",
        visibility=EvidenceVisibility.PUBLIC,
    )
    restricted = ProjectEvidence.objects.create(
        project=project,
        label="Internal",
        value="x",
        source="lab notes",
        visibility=EvidenceVisibility.RESTRICTED,
    )
    assert with_source.is_publicly_projectable() is True
    assert no_source.is_publicly_projectable() is False
    assert restricted.is_publicly_projectable() is False


@pytest.mark.django_db
def test_collaborator_and_funding_approval_flag():
    project = make_project()
    approved = ProjectCollaborator.objects.create(
        project=project,
        name="Alice",
        publication_approved=True,
    )
    hidden = ProjectCollaborator.objects.create(
        project=project,
        name="Bob",
        publication_approved=False,
    )
    fund_ok = ProjectFunding.objects.create(
        project=project,
        funder="Org",
        publication_approved=True,
    )
    fund_no = ProjectFunding.objects.create(
        project=project,
        funder="Secret",
        publication_approved=False,
    )
    assert approved.publication_approved is True
    assert hidden.publication_approved is False
    assert fund_ok.publication_approved is True
    assert fund_no.publication_approved is False


@pytest.mark.django_db
def test_publication_citation_gate():
    pub = make_publication(
        citation_count=12,
        citation_source="",
        citation_last_verified=date(2024, 1, 1),
        citation_visibility=EvidenceVisibility.PUBLIC,
    )
    assert pub.public_citation_count() is None
    pub.citation_source = "Google Scholar"
    pub.save()
    assert pub.public_citation_count() == 12
    pub.citation_visibility = EvidenceVisibility.INTERNAL
    pub.save()
    assert pub.public_citation_count() is None


@pytest.mark.django_db
def test_project_topic_publication_m2m():
    topic = make_topic()
    pub = make_publication()
    project = make_project()
    project.topics.add(topic)
    project.publications.add(pub)
    assert topic.projects.filter(pk=project.pk).exists()
    assert pub.projects.filter(pk=project.pk).exists()


@pytest.mark.django_db
def test_research_statement_one_published_per_locale():
    ResearchStatement.objects.create(
        locale=Locale.EN,
        slug="agenda",
        title="Agenda",
        body="<p>Hello</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    second = ResearchStatement(
        locale=Locale.EN,
        slug="agenda-2",
        title="Agenda 2",
        body="<p>Other</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    with pytest.raises(ValidationError):
        second.full_clean()


@pytest.mark.django_db
def test_research_statement_draft_allowed_alongside_published():
    ResearchStatement.objects.create(
        locale=Locale.EN,
        slug="agenda",
        title="Agenda",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    draft = ResearchStatement(
        locale=Locale.EN,
        slug="agenda-draft",
        title="Draft agenda",
        status=LifecycleStatus.DRAFT,
    )
    draft.full_clean()
    draft.save()
    assert ResearchStatement.objects.public().count() == 1
