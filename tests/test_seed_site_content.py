"""Tests for seed_site_content management command."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Article,
    HomeModule,
    HomeModuleKey,
    Landing,
    LifecycleStatus,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
)


@pytest.mark.django_db
def test_seed_site_content_populates_public_api():
    call_command("seed_site_content")

    assert Landing.objects.public().count() == 2
    assert Profile.objects.public().count() == 2
    assert ResearchStatement.objects.public().count() == 2
    assert ResearchTopic.objects.public().count() == 6
    assert Publication.objects.public().count() == 6
    assert Project.objects.public().count() == 6
    assert Article.objects.public().count() == 4
    assert HomeModule.objects.visible_for_locale("en").count() == len(
        HomeModuleKey.values
    )
    assert HomeModule.objects.visible_for_locale("fa").count() == len(
        HomeModuleKey.values
    )

    client = Client()
    topics = client.get("/api/research/topics/en").json()
    assert len(topics["items"]) == 3
    articles = client.get("/api/articles/en").json()
    assert len(articles["items"]) == 2
    statements = client.get("/api/research/statements/en").json()
    assert len(statements) == 1
    assert statements[0]["slug"] == "statement"

    project = client.get("/api/research/projects/en/pars-sql-vtd-edge").json()
    assert project["code_url"] == "https://github.com/tahamohamadi-ir/ADHD-VTD"
    assert project["code_availability"] == "public"

    composition = client.get("/api/home-composition/en")
    assert composition.status_code == 200
    assert [module["key"] for module in composition.json()["modules"]] == list(
        HomeModuleKey.values
    )


@pytest.mark.django_db
def test_seed_site_content_preserves_existing_home_composition():
    existing = HomeModule.objects.create(
        locale="en",
        key=HomeModuleKey.GRAPH,
        visible=False,
        order=7,
        status=LifecycleStatus.DRAFT,
        provenance_note="owner configuration",
    )

    call_command("seed_site_content")

    assert list(HomeModule.objects.filter(locale="en")) == [existing]
    existing.refresh_from_db()
    assert existing.visible is False
    assert existing.order == 7
    assert existing.status == LifecycleStatus.DRAFT
    assert existing.provenance_note == "owner configuration"
    assert HomeModule.objects.filter(locale="fa").count() == len(
        HomeModuleKey.values
    )


@pytest.mark.django_db
def test_seed_site_content_is_idempotent_without_force():
    call_command("seed_site_content")
    first_counts = {
        "landing": Landing.objects.count(),
        "profile": Profile.objects.count(),
        "topic": ResearchTopic.objects.count(),
    }
    call_command("seed_site_content")
    assert Landing.objects.count() == first_counts["landing"]
    assert Profile.objects.count() == first_counts["profile"]
    assert ResearchTopic.objects.count() == first_counts["topic"]


@pytest.mark.django_db
def test_seed_site_content_force_updates_existing():
    Landing.objects.create(
        locale="en",
        slug="home",
        title="Placeholder",
        body="old",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=2),
    )
    call_command("seed_site_content", force=True)
    landing = Landing.objects.get(locale="en", slug="home")
    assert landing.title == "Taha Mohammadi"
    assert "human-centered intelligent systems" in landing.body
