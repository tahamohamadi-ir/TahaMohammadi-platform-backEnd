"""Public API tests — published-only projection, exact field sets, JSON 404s."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Article,
    ArticleSlugRedirect,
    Landing,
    LifecycleStatus,
    Locale,
    Profile,
    Series,
    TopicTag,
)

PUBLIC_FIELDS = {
    "locale",
    "slug",
    "title",
    "body",
    "seo_title",
    "seo_description",
    "published_at",
}
PROFILE_SUMMARY_FIELDS = {
    "locale",
    "slug",
    "title",
    "seoTitle",
    "seoDescription",
    "shortBio",
    "longBio",
    "availability",
    "publishedAt",
    "availableLocales",
}
PROFILE_DETAIL_FIELDS = PROFILE_SUMMARY_FIELDS | {
    "skills",
    "experience",
    "education",
    "publications",
    "researchProjects",
    "certificates",
    "socials",
}
FORBIDDEN_FIELDS = {"status", "created_at", "allow_comments"}
ARTICLE_FORBIDDEN = FORBIDDEN_FIELDS

ARTICLE_LIST_FIELDS = {
    "locale",
    "slug",
    "title",
    "excerpt",
    "featured_image",
    "license",
    "reading_time_minutes",
    "published_at",
    "updated_at",
    "topic_tags",
    "series",
}
ARTICLE_DETAIL_FIELDS = ARTICLE_LIST_FIELDS | {"body", "accessibility_notes", "story"}


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def published_content(db):
    landing_fa = Landing.objects.create(
        locale=Locale.FA,
        slug="home",
        title="خانه",
        body="متن خانه",
        seo_title="خانه",
        seo_description="توضیح",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=2),
    )
    Landing.objects.create(
        locale=Locale.FA,
        slug="draft-page",
        title="پیش‌نویس",
        status=LifecycleStatus.DRAFT,
    )
    Landing.objects.create(
        locale=Locale.EN,
        slug="home",
        title="Home",
        body="Home body",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=2),
    )
    profile_fa = Profile.objects.create(
        locale=Locale.FA,
        slug="profile",
        title="Profile",
        body="Profile body",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=2),
    )
    Profile.objects.create(
        locale=Locale.FA,
        slug="draft-profile",
        title="Draft profile",
        status=LifecycleStatus.DRAFT,
    )
    tag = TopicTag.objects.create(locale=Locale.EN, slug="systems", name="Systems")
    series = Series.objects.create(
        locale=Locale.EN,
        slug="foundations",
        title="Foundations",
        description="Series intro",
        ordering=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=3),
    )
    Series.objects.create(
        locale=Locale.EN,
        slug="draft-series",
        title="Draft series",
        status=LifecycleStatus.DRAFT,
    )
    article = Article.objects.create(
        locale=Locale.EN,
        slug="first-post",
        title="First post",
        body="<p>Hello published</p>",
        excerpt="Hello",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    )
    article.topic_tags.add(tag)
    article.series.add(series)
    Article.objects.create(
        locale=Locale.EN,
        slug="draft-post",
        title="Draft post",
        body="<p>Secret</p>",
        status=LifecycleStatus.DRAFT,
    )
    ArticleSlugRedirect.objects.create(
        locale=Locale.EN, old_slug="legacy-post", new_slug="first-post"
    )
    return {
        "landing_fa": landing_fa,
        "profile_fa": profile_fa,
        "article": article,
        "tag": tag,
        "series": series,
    }


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


def test_list_landings_returns_only_published_for_locale(api_client, published_content):
    data = assert_json(api_client.get("/api/landings/fa"), 200)
    assert [item["slug"] for item in data] == ["home"]
    assert all(set(item) == PUBLIC_FIELDS for item in data)
    assert all(item["locale"] == Locale.FA for item in data)


def test_list_landings_locale_isolation(api_client, published_content):
    data = assert_json(api_client.get("/api/landings/en"), 200)
    assert [item["slug"] for item in data] == ["home"]
    assert data[0]["title"] == "Home"


def test_detail_landing_by_slug(api_client, published_content):
    data = assert_json(api_client.get("/api/landings/fa/home"), 200)
    assert set(data) == PUBLIC_FIELDS
    assert data["slug"] == "home"
    assert data["locale"] == Locale.FA
    assert data["published_at"] is not None
    assert FORBIDDEN_FIELDS.isdisjoint(data)


def test_detail_draft_slug_404(api_client, published_content):
    response = api_client.get("/api/landings/fa/draft-page")
    assert response.status_code == 404
    assert response["content-type"].startswith("application/json")
    assert "detail" in response.json()
    assert "Traceback" not in response.text


def test_detail_unknown_slug_404(api_client, published_content):
    response = api_client.get("/api/landings/fa/does-not-exist")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_unknown_locale_returns_empty_list(api_client, published_content):
    data = assert_json(api_client.get("/api/landings/xx"), 200)
    assert data == []


def test_list_profiles_returns_only_published(api_client, published_content):
    data = assert_json(api_client.get("/api/profiles/fa"), 200)
    assert [item["slug"] for item in data] == ["profile"]
    assert all(set(item) == PROFILE_SUMMARY_FIELDS for item in data)
    assert all("status" not in item for item in data)


def test_detail_profile_by_slug(api_client, published_content):
    data = assert_json(api_client.get("/api/profiles/fa/profile"), 200)
    assert set(data) == PROFILE_DETAIL_FIELDS
    assert data["slug"] == "profile"
    assert data["availableLocales"] == [Locale.FA]
    assert FORBIDDEN_FIELDS.isdisjoint(data)


def test_detail_profile_draft_404(api_client, published_content):
    response = api_client.get("/api/profiles/fa/draft-profile")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_detail_profile_translation_unavailable(api_client, published_content):
    data = assert_json(api_client.get("/api/profiles/en/profile"), 404)
    assert data["code"] == "TRANSLATION_UNAVAILABLE"
    assert data["availableLocales"] == [Locale.FA]
    assert data["slug"] == "profile"


def _article_items(payload):
    """Normalize paginated or bare list article responses."""
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    return payload


def test_list_articles_published_only_paginated(api_client, published_content):
    data = assert_json(api_client.get("/api/articles/en"), 200)
    items = _article_items(data)
    assert [item["slug"] for item in items] == ["first-post"]
    assert set(items[0]) == ARTICLE_LIST_FIELDS
    assert FORBIDDEN_FIELDS.isdisjoint(items[0])
    assert "status" not in items[0]
    assert ARTICLE_FORBIDDEN.isdisjoint(items[0])
    assert items[0]["topic_tags"][0]["slug"] == "systems"
    assert items[0]["series"][0]["slug"] == "foundations"
    assert "status" not in items[0]["series"][0]


def test_list_articles_tag_filter(api_client, published_content):
    data = assert_json(api_client.get("/api/articles/en?tag=systems"), 200)
    assert [item["slug"] for item in _article_items(data)] == ["first-post"]
    empty = assert_json(api_client.get("/api/articles/en?tag=missing"), 200)
    assert _article_items(empty) == []


def test_list_articles_series_filter_excludes_draft_series(api_client, published_content):
    data = assert_json(api_client.get("/api/articles/en?series=foundations"), 200)
    assert [item["slug"] for item in _article_items(data)] == ["first-post"]
    empty = assert_json(api_client.get("/api/articles/en?series=draft-series"), 200)
    assert _article_items(empty) == []


def test_detail_article_by_slug(api_client, published_content):
    data = assert_json(api_client.get("/api/articles/en/first-post"), 200)
    assert set(data) == ARTICLE_DETAIL_FIELDS
    assert data["body"] == "<p>Hello published</p>"
    assert data["story"] is None
    assert FORBIDDEN_FIELDS.isdisjoint(data)


def test_detail_article_draft_404(api_client, published_content):
    response = api_client.get("/api/articles/en/draft-post")
    assert response.status_code == 404
    assert "detail" in response.json()
    assert b"Secret" not in response.content


def test_list_series_published_only(api_client, published_content):
    data = assert_json(api_client.get("/api/series/en"), 200)
    assert [item["slug"] for item in data] == ["foundations"]
    assert "status" not in data[0]


def test_list_tags_locale(api_client, published_content):
    data = assert_json(api_client.get("/api/tags/en"), 200)
    assert [item["slug"] for item in data] == ["systems"]


def test_list_article_redirects(api_client, published_content):
    data = assert_json(api_client.get("/api/article-redirects/en"), 200)
    assert data == [
        {"locale": "en", "old_slug": "legacy-post", "new_slug": "first-post"}
    ]


def test_list_article_redirects_hides_targets_not_public(api_client, published_content):
    ArticleSlugRedirect.objects.create(
        locale=Locale.EN, old_slug="points-to-draft", new_slug="draft-post"
    )
    data = assert_json(api_client.get("/api/article-redirects/en"), 200)
    assert all(item["new_slug"] != "draft-post" for item in data)


def test_detail_article_body_is_sanitized(api_client, db):
    Article.objects.create(
        locale=Locale.EN,
        slug="xss-post",
        title="XSS",
        body='<p>ok</p><script>alert(1)</script>',
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    )
    data = assert_json(api_client.get("/api/articles/en/xss-post"), 200)
    assert "<script>" not in data["body"]
    assert "ok" in data["body"]


def test_snippet_modules_retired():
    """Wagtail snippet ViewSets are no longer registered (SPA owns CRUD)."""
    import apps.content.admin as content_admin

    assert not hasattr(content_admin, "ArticleViewSet")
