"""Lifecycle and locale-uniqueness tests for content models (P3)."""

from datetime import timedelta

import pytest
from django.db import IntegrityError
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


def make_landing(**overrides) -> Landing:
    defaults = {
        "locale": Locale.EN,
        "slug": "home",
        "title": "Home",
        "body": "Home body",
        "status": LifecycleStatus.PUBLISHED,
        "published_at": timezone.now() - timedelta(days=1),
    }
    defaults.update(overrides)
    return Landing.objects.create(**defaults)


def past() -> timezone.datetime:
    return timezone.now() - timedelta(days=1)


def future() -> timezone.datetime:
    return timezone.now() + timedelta(days=1)


@pytest.mark.django_db
def test_public_returns_published_only():
    make_landing(slug="draft-page", status=LifecycleStatus.DRAFT)
    published = make_landing(slug="published-page")
    assert list(Landing.objects.public()) == [published]


@pytest.mark.django_db
def test_public_excludes_future_published_at():
    make_landing(slug="scheduled", published_at=future())
    assert list(Landing.objects.public()) == []


@pytest.mark.django_db
def test_public_excludes_review_and_archived():
    make_landing(slug="in-review", status=LifecycleStatus.REVIEW, published_at=past())
    make_landing(slug="old-archive", status=LifecycleStatus.ARCHIVED, published_at=past())
    assert list(Landing.objects.public()) == []


@pytest.mark.django_db
def test_public_works_on_filtered_queryset():
    make_landing(slug="draft-page", status=LifecycleStatus.DRAFT)
    published = make_landing(slug="published-page")
    assert list(Landing.objects.filter(locale=Locale.EN).public()) == [published]


@pytest.mark.django_db
def test_public_never_leaks_draft_with_blank_published_at():
    make_landing(slug="blank-date", status=LifecycleStatus.DRAFT, published_at=None)
    assert list(Landing.objects.public()) == []


@pytest.mark.django_db
def test_same_slug_allowed_across_locales():
    fa = make_landing(locale=Locale.FA, slug="about", title="Ø¯Ø±Ø¨Ø§Ø±Ù‡")
    en = make_landing(locale=Locale.EN, slug="about", title="About")
    assert Landing.objects.filter(slug="about").count() == 2
    assert {item.locale for item in Landing.objects.filter(slug="about")} == {
        fa.locale,
        en.locale,
    }


@pytest.mark.django_db
def test_duplicate_slug_same_locale_rejected():
    make_landing(locale=Locale.EN, slug="about")
    with pytest.raises(IntegrityError):
        make_landing(locale=Locale.EN, slug="about")


@pytest.mark.django_db
def test_profile_public_shares_lifecycle():
    published = Profile.objects.create(
        locale=Locale.FA,
        slug="profile",
        title="Profile",
        body="Profile body",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Profile.objects.create(
        locale=Locale.FA,
        slug="draft-profile",
        title="Draft",
        status=LifecycleStatus.DRAFT,
    )
    assert list(Profile.objects.public()) == [published]


@pytest.mark.django_db
def test_profile_duplicate_slug_same_locale_rejected():
    Profile.objects.create(
        locale=Locale.FA, slug="profile", title="Profile", status=LifecycleStatus.DRAFT
    )
    with pytest.raises(IntegrityError):
        Profile.objects.create(
            locale=Locale.FA, slug="profile", title="Profile", status=LifecycleStatus.DRAFT
        )


@pytest.mark.django_db
def test_article_shell_public_only_published():
    published = Article.objects.create(
        locale=Locale.EN,
        slug="first-post",
        title="First post",
        body="Short body",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Article.objects.create(
        locale=Locale.EN,
        slug="draft-post",
        title="Draft",
        status=LifecycleStatus.DRAFT,
    )
    assert list(Article.objects.public()) == [published]

# --- P4 Article / Series / TopicTag ---


@pytest.mark.django_db
def test_article_reading_time_computed_on_save():
    words = " ".join([f"w{i}" for i in range(250)])
    article = Article.objects.create(
        locale=Locale.EN,
        slug="long-post",
        title="Long",
        body=f"<p>{words}</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    assert article.reading_time_minutes == 2


@pytest.mark.django_db
def test_article_empty_body_reading_time_zero():
    article = Article.objects.create(
        locale=Locale.EN,
        slug="empty-body",
        title="Empty",
        body="",
        status=LifecycleStatus.DRAFT,
    )
    assert article.reading_time_minutes == 0


def test_reading_wpm_for_locale():
    from apps.content.models import reading_wpm_for_locale

    assert reading_wpm_for_locale("fa") == 180
    assert reading_wpm_for_locale("en") == 230
    assert reading_wpm_for_locale(None) == 200
    assert reading_wpm_for_locale("xx") == 200


@pytest.mark.django_db
def test_article_reading_time_uses_per_locale_wpm():
    words = " ".join(f"w{i}" for i in range(460))
    fa = Article.objects.create(
        locale=Locale.FA,
        slug="fa-post",
        title="نوشته",
        body=f"<p>{words}</p>",
        status=LifecycleStatus.DRAFT,
    )
    en = Article.objects.create(
        locale=Locale.EN,
        slug="en-post",
        title="Post",
        body=f"<p>{words}</p>",
        status=LifecycleStatus.DRAFT,
    )
    assert fa.reading_time_minutes == 3  # 460 / 180 → ceil 2.56
    assert en.reading_time_minutes == 2  # 460 / 230 = 2 exactly


def test_compute_reading_time_explicit_wpm_overrides_locale():
    from apps.content.models import compute_reading_time_minutes

    body = "<p>" + " ".join(["word"] * 400) + "</p>"
    assert compute_reading_time_minutes(body, locale="fa", wpm=200) == 2
    assert compute_reading_time_minutes(body, locale="fa") == 3
    assert compute_reading_time_minutes("", locale="fa") == 0


@pytest.mark.django_db
def test_recompute_reading_time_backfill_command():
    from django.core.management import call_command

    words = " ".join(f"w{i}" for i in range(460))
    stale = Article.objects.create(
        locale=Locale.FA,
        slug="stale-reading-time",
        title="Stale",
        body=f"<p>{words}</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    fresh_en = Article.objects.create(
        locale=Locale.EN,
        slug="fresh-reading-time",
        title="Fresh",
        body=f"<p>{words}</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Article.objects.filter(pk=stale.pk).update(reading_time_minutes=99)

    call_command("recompute_reading_time", verbosity=0)

    stale.refresh_from_db()
    fresh_en.refresh_from_db()
    assert stale.reading_time_minutes == 3
    assert fresh_en.reading_time_minutes == 2


@pytest.mark.django_db
def test_series_locale_slug_unique():
    Series.objects.create(
        locale=Locale.EN, slug="core", title="Core", status=LifecycleStatus.DRAFT
    )
    with pytest.raises(IntegrityError):
        Series.objects.create(
            locale=Locale.EN, slug="core", title="Core 2", status=LifecycleStatus.DRAFT
        )


@pytest.mark.django_db
def test_topic_tag_slug_globally_unique():
    TopicTag.objects.create(locale=Locale.EN, slug="ai", name="AI")
    with pytest.raises(IntegrityError):
        TopicTag.objects.create(locale=Locale.FA, slug="ai", name="Ù‡ÙˆØ´")


@pytest.mark.django_db
def test_article_tag_and_series_relationships():
    tag = TopicTag.objects.create(locale=Locale.EN, slug="systems", name="Systems")
    series = Series.objects.create(
        locale=Locale.EN,
        slug="foundations",
        title="Foundations",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
        ordering=1,
    )
    article = Article.objects.create(
        locale=Locale.EN,
        slug="intro",
        title="Intro",
        body="<p>Hello world</p>",
        excerpt="Hello",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    article.topic_tags.add(tag)
    article.series.add(series)
    assert list(article.topic_tags.all()) == [tag]
    assert list(Article.objects.public().filter(series=series)) == [article]


@pytest.mark.django_db
def test_series_public_excludes_draft():
    Series.objects.create(
        locale=Locale.EN, slug="draft-series", title="Draft", status=LifecycleStatus.DRAFT
    )
    published = Series.objects.create(
        locale=Locale.EN,
        slug="live-series",
        title="Live",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    assert list(Series.objects.public()) == [published]


@pytest.mark.django_db
def test_article_slug_redirect_unique_per_locale():
    ArticleSlugRedirect.objects.create(locale=Locale.EN, old_slug="old", new_slug="new")
    with pytest.raises(IntegrityError):
        ArticleSlugRedirect.objects.create(
            locale=Locale.EN, old_slug="old", new_slug="other"
        )
