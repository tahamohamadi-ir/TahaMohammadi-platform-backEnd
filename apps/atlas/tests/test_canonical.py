"""Canonical CMS-record reference strategy — plan Task 8 (``apps/atlas/canonical.py``).

The five resolution rules the fixture family and Tasks 10/14 depend on:

1. resolution is **exact-locale** — locale ``L`` resolves through
   ``objects.public()`` in ``L`` only; there is no cross-locale fallback
   (spec §5.3: "Fallback is forbidden");
2. an unpublished/draft canonical row resolves to ``None``;
3. ambiguity (more than one row for the same
   ``(canonical_model, translation_key, locale)``) raises
   ``AmbiguousCanonicalRef`` — detected, never guessed;
4. the allow-list is closed and its order is asserted literally — an unlisted
   family raises ``KeyError``;
5. the summary source is the first populated field of the family's
   ``SUMMARY_FIELDS`` tuple.

Fixtures (``seed_pairs``, ``atlas_v1``, …) live in
``apps/atlas/tests/factories.py`` (ruling R5) and are **imported by name** —
``factories.py`` is not a conftest, so a module that wants them binds them
explicitly::

    from apps.atlas.tests.factories import atlas_v1, seed_pairs

The public family slug on the wire (``canonical.family``, spec §10.2) is the
record-resolver family — the lowercase model name (``researchtopic``), never
the canonical-source registry key (``research_topic``). ``CanonicalResolution``
carries both spellings unambiguously: ``family`` is the wire slug,
``source_key`` is the registry key.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.api.record_resolver import RESOLVER_FAMILIES, ROUTE_FAMILY_MAP
from apps.atlas.canonical import (
    CANONICAL_SOURCE_KEYS,
    CANONICAL_SOURCES,
    SUMMARY_FIELDS,
    AmbiguousCanonicalRef,
    resolve_canonical,
    resolve_canonical_pair,
)
from apps.atlas.tests.factories import seed_pairs
from apps.content.models import (
    Method,
    Profile,
    Project,
    Publication,
    ResearchTopic,
    Technology,
)

#: ``seed_pairs`` is a pytest fixture this module consumes. ``__all__`` marks the
#: import as used-by-design: pyflakes cannot see a test parameter as a use of a
#: module-level binding, so a bare import would be reported as unused (F401) and
#: the parameter as a redefinition of it (F811) — a false pair, not a defect.
__all__ = ["seed_pairs"]

pytestmark = pytest.mark.django_db

#: The plan's literal allow-list order (Tasks 9–15 rely on the order).
ALLOW_LIST_ORDER = ["profile", "research_topic", "project", "publication", "method", "technology"]


def test_allow_list_is_closed_and_ordered():
    assert list(CANONICAL_SOURCES) == ALLOW_LIST_ORDER
    assert CANONICAL_SOURCE_KEYS == tuple(ALLOW_LIST_ORDER)
    assert len(CANONICAL_SOURCE_KEYS) == len(set(CANONICAL_SOURCE_KEYS))
    with pytest.raises(KeyError):
        resolve_canonical("researchstatement", uuid4(), "en")  # deliberately not allow-listed


def test_the_summary_table_covers_exactly_the_allow_list():
    """A family without a summary tuple, or a stray extra key, is a defect."""
    assert set(SUMMARY_FIELDS) == set(CANONICAL_SOURCES)
    for source, fields in SUMMARY_FIELDS.items():
        assert fields, source
        model = CANONICAL_SOURCES[source]
        defined = {field.name for field in model._meta.get_fields()}
        assert defined & set(fields), source
        # The plan's tuples also name fields the model does not define yet
        # (``profile.summary``, ``project.summary``); the skip is deliberate —
        # no other tuple may name more than one undefined field.
        assert len(set(fields) - defined) <= 1, source


def test_record_resolver_family_slugs_agree_with_the_model_names():
    """``canonical.family`` is the existing resolver slug (spec §10.2 example).

    For the families the record resolver already serves, its slug is the model's
    lowercase name — that agreement is what licenses using the same convention
    for the two entities the resolver does not know yet (spec §5.2; §25
    "Explicit deferred features": ``method`` and ``technology`` are published
    entities without public routes in v1).
    """
    for family, model in RESOLVER_FAMILIES.items():
        assert model._meta.model_name == family, family
    families = {model._meta.model_name for model in CANONICAL_SOURCES.values()}
    assert families - set(RESOLVER_FAMILIES) == {"method", "technology"}


def test_resolution_is_exact_locale_and_publish_gated(seed_pairs):
    topic_en, topic_fa = seed_pairs.research_topic
    key = topic_en.translation_key
    assert resolve_canonical("research_topic", key, "en").row.pk == topic_en.pk
    assert resolve_canonical("research_topic", key, "fa").row.pk == topic_fa.pk

    topic_fa.status = "draft"
    topic_fa.save(update_fields=["status"])
    assert resolve_canonical("research_topic", key, "fa") is None  # unpublished → None
    assert resolve_canonical("research_topic", key, "en") is not None  # never falls back

    topic_fa.status = "published"
    topic_fa.save(update_fields=["status"])
    assert resolve_canonical("research_topic", key, "fa").row.pk == topic_fa.pk


def test_resolution_is_publish_gated_from_the_start(seed_pairs):
    """A row that never went live is equally invisible — no ``status``-only rule."""
    method_en, _ = seed_pairs.method
    method_en.published_at = timezone.now() + timedelta(days=1)
    method_en.save(update_fields=["published_at"])
    assert resolve_canonical("method", method_en.translation_key, "en") is None


def test_a_translation_key_is_required():
    assert resolve_canonical("profile", None, "en") is None


def test_ambiguity_is_detected_not_guessed(seed_pairs):
    topic_en, _ = seed_pairs.research_topic
    ResearchTopic.objects.create(
        locale="en",
        slug="twin",
        title="Twin",
        status="published",
        published_at=timezone.now(),
        translation_key=topic_en.translation_key,
    )
    with pytest.raises(AmbiguousCanonicalRef):
        resolve_canonical("research_topic", topic_en.translation_key, "en")
    # The other locale is untouched, and the pair helper surfaces the same error.
    assert resolve_canonical("research_topic", topic_en.translation_key, "fa") is not None
    with pytest.raises(AmbiguousCanonicalRef):
        resolve_canonical_pair("research_topic", topic_en.translation_key)


def test_ambiguity_count_reports_every_twin_not_a_slice(seed_pairs):
    """Three published rows for one key report ``count == 3``, not the ``[:2]`` slice."""
    topic_en, _ = seed_pairs.research_topic
    for _ in range(2):
        ResearchTopic.objects.create(
            locale="en",
            slug=f"twin-{uuid4().hex[:8]}",
            title="Twin",
            status="published",
            published_at=timezone.now(),
            translation_key=topic_en.translation_key,
        )
    with pytest.raises(AmbiguousCanonicalRef) as excinfo:
        resolve_canonical("research_topic", topic_en.translation_key, "en")
    assert excinfo.value.count == 3
    assert "3 published rows" in str(excinfo.value)


def test_summary_precedence_uses_the_first_populated_field(seed_pairs):
    # Deviation from the plan's snippet (plan error, proven in the RED run):
    # ``Project`` has no ``summary`` field, so the snippet's
    # ``save(update_fields=["summary", "objective"])`` raises ``ValueError``.
    # The rule under test is unchanged: the family's summary field is used.
    project_en, _ = seed_pairs.project
    project_en.objective = "Objective text"
    project_en.save(update_fields=["objective"])
    assert resolve_canonical("project", project_en.translation_key, "en").summary == (
        "Objective text"
    )


def test_summary_precedence_prefers_the_earlier_field_and_falls_through(seed_pairs):
    """The earlier *populated* field of the tuple wins; an empty one falls through.

    The two-field families populate **both** fields in the "wins" case: with
    only one field populated, an inverted iteration order (or a "keep the last
    populated field" rule) returns the same answer and the documented precedence
    stays unpinned — a mutation that flips the order must fail here.
    """
    topic_en, _ = seed_pairs.research_topic
    assert resolve_canonical("research_topic", topic_en.translation_key, "en").summary == (
        "Research topic summary"
    )

    topic_en.summary = ""
    topic_en.save(update_fields=["summary"])
    assert resolve_canonical("research_topic", topic_en.translation_key, "en").summary == ""

    for source, earlier, later in (
        ("method", "Method short description", "Method long description"),
        ("technology", "Technology short description", "Technology long description"),
    ):
        en_row, _ = getattr(seed_pairs, source)
        en_row.short_description = earlier
        en_row.description = later
        en_row.save(update_fields=["short_description", "description"])
        assert resolve_canonical(source, en_row.translation_key, "en").summary == earlier, source

        en_row.short_description = ""
        en_row.save(update_fields=["short_description"])
        assert resolve_canonical(source, en_row.translation_key, "en").summary == later, source


def test_resolution_carries_the_wire_family_slug_and_the_route_family(seed_pairs):
    """``canonical.family`` is the resolver slug, ``routeFamily`` its mapped route."""
    topic_en, _ = seed_pairs.research_topic
    resolved = resolve_canonical("research_topic", topic_en.translation_key, "en")
    assert resolved.source_key == "research_topic"  # the registry key, plan vocabulary
    assert resolved.family == "researchtopic"  # the wire slug (spec §10.2 example)
    assert resolved.route_family == "research"  # ROUTE_FAMILY_MAP["researchtopic"]
    assert resolved.route_family == ROUTE_FAMILY_MAP[resolved.family]

    profile_en, _ = seed_pairs.profile
    assert resolve_canonical("profile", profile_en.translation_key, "en").route_family == "about"

    # `method`/`technology` are published entities without public routes in v1
    # (spec §5.2; §25), so the existing map has no entry — None, never an
    # invented route.
    for source in ("method", "technology"):
        en_row, _ = getattr(seed_pairs, source)
        resolved = resolve_canonical(source, en_row.translation_key, "en")
        assert resolved.family == source
        assert resolved.family not in ROUTE_FAMILY_MAP
        assert resolved.route_family is None


def test_resolution_identity_fields(seed_pairs):
    project_en, _ = seed_pairs.project
    resolved = resolve_canonical("project", project_en.translation_key, "en")
    assert resolved.model is Project
    assert resolved.row.pk == project_en.pk
    assert resolved.id == str(project_en.pk)  # the wire carries the id as a string
    assert resolved.locale == "en"
    assert resolved.slug == project_en.slug
    assert resolved.title == project_en.title


def test_pair_resolution_returns_both_locales_and_none_where_unresolved(seed_pairs):
    topic_en, topic_fa = seed_pairs.research_topic
    pair = resolve_canonical_pair("research_topic", topic_en.translation_key)
    assert set(pair) == {"en", "fa"}
    assert pair["en"].row.pk == topic_en.pk
    assert pair["fa"].row.pk == topic_fa.pk
    assert pair["en"].locale == "en" and pair["fa"].locale == "fa"

    topic_fa.status = "draft"
    topic_fa.save(update_fields=["status"])
    partial = resolve_canonical_pair("research_topic", topic_en.translation_key)
    assert partial["en"] is not None and partial["fa"] is None

    assert resolve_canonical_pair("research_topic", None) == {"en": None, "fa": None}


def test_every_family_resolves_its_seeded_pair(seed_pairs):
    """All six families are wired end-to-end, both locales (Tasks 9–15 consume them)."""
    checked = 0
    for source, model in CANONICAL_SOURCES.items():
        en_row, fa_row = getattr(seed_pairs, source)
        assert isinstance(en_row, model)
        pair = resolve_canonical_pair(source, en_row.translation_key)
        assert pair["en"].row.pk == en_row.pk, source
        assert pair["fa"].row.pk == fa_row.pk, source
        assert pair["en"].title == en_row.title
        assert pair["fa"].title == fa_row.title
        checked += 1
    assert checked == len(ALLOW_LIST_ORDER)
    assert set(CANONICAL_SOURCES.values()) >= {
        Profile,
        ResearchTopic,
        Project,
        Publication,
        Method,
        Technology,
    }
