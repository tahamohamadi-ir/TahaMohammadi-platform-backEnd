"""Task 4: dry-run migration is exact and writes nothing (Plan D).

The tests build their own ACTIVE legacy graph from the paired canonical
rows (``seed_pairs``) — the migration reads storage, never the network.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command

from apps.atlas.models import AtlasVersion
from apps.atlas.tests.factories import seed_pairs  # noqa: F401
from apps.content.models import (
    GraphEdge,
    GraphNode,
    GraphNodeRelated,
    GraphVersion,
    GraphVersionStatus,
)


def _build_legacy_graph(seed_pairs):
    """Build one ACTIVE legacy graph per locale from the paired rows.

    Each locale's graph references its OWN locale rows (EN graph → EN
    profile/topics, FA graph → FA rows): that is what the live
    `/api/graph/{locale}` payloads show (FA ids 4/5/6, EN ids 1/2/3), and it
    exercises the translation_key pairing across locales instead of one
    shared EN row.

    NOTE: ``seed_pairs`` is shared per TEST (function scope), but the
    taxonomy seed is idempotent — the same (key) rows are reused, never
    duplicated. Each test gets a fresh database, so no cross-test leakage.
    """
    from django.core.management import call_command as _call

    from apps.content.models import Profile, ResearchTopic

    # The taxonomy must exist before migration resolves type keys.
    _call('atlas_seed_taxonomy')
    profile_type = ContentType.objects.get_for_model(Profile)
    topic_type = ContentType.objects.get_for_model(ResearchTopic)
    pairs = seed_pairs.as_dict()
    en_profile, fa_profile = pairs['profile']
    en_topic, fa_topic = pairs['research_topic']
    topics_by_locale = {'en': [en_topic], 'fa': [fa_topic]}
    profile_by_locale = {'en': en_profile, 'fa': fa_profile}
    for locale in ('en', 'fa'):
        version = GraphVersion.objects.create(locale=locale, status='active')
        identity = GraphNode.objects.create(
            version=version,
            node_id='identity',
            label='Taha Mohammadi' if locale == 'en' else 'طه محمدی',
            type='identity',
            weight=1,
            pos_x=0,
            pos_y=0,
            pos_z=8,
        )
        GraphNodeRelated.objects.create(
            node=identity,
            content_type=profile_type,
            object_id=profile_by_locale[locale].id,
        )
        for topic in topics_by_locale[locale]:
            node = GraphNode.objects.create(
                version=version,
                node_id='research-topic-%d' % topic.id,
                label=topic.title,
                type='research-topic',
                weight=1,
                pos_x=1.0,
                pos_y=2.0,
                pos_z=3.0,
            )
            GraphNodeRelated.objects.create(
                node=node,
                content_type=topic_type,
                object_id=topic.id,
            )
            GraphEdge.objects.create(
                version=version,
                source=identity,
                target=node,
                relation_type='research-focus',
                directed=True,
                weight=1,
            )
    return GraphVersion.objects.filter(status='active').count()


@pytest.mark.django_db
def test_dry_run_writes_nothing():
    AtlasVersion.objects.all().delete()
    call_command('atlas_migrate_graph', '--dry-run')
    assert AtlasVersion.objects.count() == 0


@pytest.mark.django_db
def test_mapping_is_exactly_the_published_graph(seed_pairs):  # noqa: F811
    _build_legacy_graph(seed_pairs)
    call_command('atlas_migrate_graph', '--label', 'Research Universe v1')
    version = AtlasVersion.objects.get(status='draft')
    assert version.nodes.count() == 2
    assert version.relations.count() == 1
    assert set(
        version.nodes.values_list('node_type__key', flat=True)
    ) == {'identity', 'research-area'}
    assert set(
        version.relations.values_list('relation_type__key', flat=True)
    ) == {'research-focus'}


@pytest.mark.django_db
def test_unresolvable_canonical_reference_is_reported_not_guessed(
    seed_pairs,
):  # noqa: F811
    from apps.content.models import ResearchTopic

    _build_legacy_graph(seed_pairs)
    # Break one topic's pairing: null its translation_key so the migration
    # reports instead of guessing.
    victim = ResearchTopic.objects.filter(locale='en').order_by('id').first()
    victim.translation_key = None
    victim.save(update_fields=['translation_key'])
    with pytest.raises(SystemExit, match='unresolved'):
        call_command('atlas_migrate_graph', '--label', 'Broken')
    assert AtlasVersion.objects.count() == 0


@pytest.mark.django_db
def test_migration_is_idempotent(seed_pairs):  # noqa: F811
    _build_legacy_graph(seed_pairs)
    call_command('atlas_migrate_graph', '--label', 'A')
    call_command('atlas_migrate_graph', '--label', 'B')
    assert AtlasVersion.objects.count() == 1, (
        'a second run must update the existing migrated draft, not duplicate it'
    )
