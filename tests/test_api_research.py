"""P5 research API tests — draft exclusion, redact, forbidden fields."""

from datetime import date, timedelta

import pytest
from django.test import Client
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

RESEARCH_FORBIDDEN = {
    "status",
    "created_at",
    "publication_approved",
    "citation_source",
    "citation_last_verified",
    "citation_visibility",
    "visibility",
}


@pytest.fixture
def api_client():
    return Client()


def past():
    return timezone.now() - timedelta(days=1)


@pytest.fixture
def research_content(db):
    topic = ResearchTopic.objects.create(
        locale=Locale.EN,
        slug="hci",
        title="HCI",
        summary="Summary",
        motivation="Why",
        problems="Problems",
        research_questions="RQ",
        methods="Methods",
        future_directions="Future",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    ResearchTopic.objects.create(
        locale=Locale.EN,
        slug="draft-topic",
        title="Draft topic",
        status=LifecycleStatus.DRAFT,
    )
    statement = ResearchStatement.objects.create(
        locale=Locale.EN,
        slug="agenda",
        title="Agenda",
        body="<p>Statement</p><script>alert(1)</script>",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    ResearchStatement.objects.create(
        locale=Locale.EN,
        slug="draft-agenda",
        title="Draft",
        body="<p>Secret statement</p>",
        status=LifecycleStatus.DRAFT,
    )
    pub = Publication.objects.create(
        locale=Locale.EN,
        slug="paper-one",
        title="Paper One",
        authors="Taha Mohammadi",
        venue="Venue",
        date=date(2024, 1, 1),
        doi="10.1000/test",
        url="https://example.com/paper",
        pdf_url="https://example.com/paper.pdf",
        license="cc-by-4",
        citation_count=42,
        citation_source="Google Scholar",
        citation_last_verified=date(2024, 6, 1),
        citation_visibility=EvidenceVisibility.PUBLIC,
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Publication.objects.create(
        locale=Locale.EN,
        slug="draft-paper",
        title="Draft paper",
        citation_count=99,
        citation_source="secret",
        citation_visibility=EvidenceVisibility.PUBLIC,
        status=LifecycleStatus.DRAFT,
    )
    project = Project.objects.create(
        locale=Locale.EN,
        slug="vtd-edge",
        title="VTD-Edge",
        project_type=ProjectType.RESEARCH,
        objective="Objective",
        methods_summary="Methods",
        role="Lead",
        license="cc-by-4",
        code_availability=Availability.PUBLIC,
        data_availability=Availability.RESTRICTED,
        demo_availability=Availability.PRIVATE,
        code_url="https://example.com/code",
        data_url="https://example.com/secret-data",
        demo_url="https://example.com/secret-demo",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    project.topics.add(topic)
    project.publications.add(pub)
    Project.objects.create(
        locale=Locale.EN,
        slug="draft-project",
        title="Draft project",
        status=LifecycleStatus.DRAFT,
    )
    ProjectEvidence.objects.create(
        project=project,
        label="Accuracy",
        value="90%",
        source="eval log",
        last_verified=date(2024, 6, 1),
        visibility=EvidenceVisibility.PUBLIC,
    )
    ProjectEvidence.objects.create(
        project=project,
        label="Leaked",
        value="99%",
        source="",
        visibility=EvidenceVisibility.PUBLIC,
    )
    ProjectEvidence.objects.create(
        project=project,
        label="Internal",
        value="x",
        source="notes",
        visibility=EvidenceVisibility.INTERNAL,
    )
    ProjectCollaborator.objects.create(
        project=project,
        name="Alice",
        role="Advisor",
        publication_approved=True,
    )
    ProjectCollaborator.objects.create(
        project=project,
        name="Hidden Bob",
        role="Secret",
        publication_approved=False,
    )
    ProjectFunding.objects.create(
        project=project,
        funder="Approved Org",
        grant_id="G-1",
        publication_approved=True,
    )
    ProjectFunding.objects.create(
        project=project,
        funder="Hidden Funder",
        publication_approved=False,
    )
    return {
        "topic": topic,
        "statement": statement,
        "project": project,
        "publication": pub,
    }


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


def _items(payload):
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    return payload


def test_list_research_topics_published_only(api_client, research_content):
    data = assert_json(api_client.get("/api/research/topics/en"), 200)
    items = _items(data)
    assert [i["slug"] for i in items] == ["hci"]
    assert RESEARCH_FORBIDDEN.isdisjoint(items[0])


def test_detail_research_topic_and_relations(api_client, research_content):
    data = assert_json(api_client.get("/api/research/topics/en/hci"), 200)
    assert data["slug"] == "hci"
    assert data["projects"][0]["slug"] == "vtd-edge"
    assert data["publications"][0]["slug"] == "paper-one"
    assert "status" not in data
    assert RESEARCH_FORBIDDEN.isdisjoint(data)


def test_detail_draft_research_topic_404(api_client, research_content):
    response = api_client.get("/api/research/topics/en/draft-topic")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_research_statement_sanitized_and_draft_404(api_client, research_content):
    data = assert_json(api_client.get("/api/research/statements/en/agenda"), 200)
    assert "<script>" not in data["body"]
    assert "Statement" in data["body"]
    response = api_client.get("/api/research/statements/en/draft-agenda")
    assert response.status_code == 404
    assert b"Secret statement" not in response.content


def test_project_detail_redacts_urls_evidence_collaborators_funding(
    api_client, research_content
):
    data = assert_json(api_client.get("/api/research/projects/en/vtd-edge"), 200)
    assert data["code_url"] == "https://example.com/code"
    assert data["data_url"] == ""
    assert data["demo_url"] == ""
    assert data["data_availability"] == Availability.RESTRICTED
    assert [e["label"] for e in data["evidence"]] == ["Accuracy"]
    assert [c["name"] for c in data["collaborators"]] == ["Alice"]
    assert [f["funder"] for f in data["funding"]] == ["Approved Org"]
    assert "Hidden Bob" not in str(data)
    assert "Hidden Funder" not in str(data)
    assert "secret-data" not in str(data)
    assert RESEARCH_FORBIDDEN.isdisjoint(data)
    assert "publication_approved" not in str(data)


def test_draft_project_404(api_client, research_content):
    response = api_client.get("/api/research/projects/en/draft-project")
    assert response.status_code == 404


def test_publication_citation_gate_and_draft_404(api_client, research_content):
    data = assert_json(api_client.get("/api/research/publications/en/paper-one"), 200)
    assert data["citation_count"] == 42
    assert data["doi"] == "10.1000/test"
    assert "citation_source" not in data
    assert RESEARCH_FORBIDDEN.isdisjoint(data)

    pub = research_content["publication"]
    pub.citation_source = ""
    pub.save()
    data2 = assert_json(api_client.get("/api/research/publications/en/paper-one"), 200)
    assert data2["citation_count"] is None

    response = api_client.get("/api/research/publications/en/draft-paper")
    assert response.status_code == 404
    assert b"99" not in response.content
