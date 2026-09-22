"""Task 6: migration parity against the frozen legacy payloads (Plan D).

Builds the Atlas projection from a draft migrated IN THE TEST (the same
`atlas_migrate_graph` command Task 5 runs on development) and compares it
against ``tests/fixtures/legacy-graph/*.json`` through ``compare_parity``.

Why the test migrates instead of reading a committed draft: the draft lives
in a database, not in git — no test database contains it. The test seeds
the same fixture rows the frozen payloads describe (three topics + profile
pairs with the payloads' verbatim titles, one ACTIVE legacy graph per
locale), runs the REAL command (not a reimplementation), then compares the
REAL projection. A regression in the command or the projection fails here,
not just in a manual evidence step.

Fixture-backed by default; ``--live`` re-checks the same assertions against
the live `/api/graph/*` payloads as an opt-in extra (network failures skip,
never fail — the frozen fixtures are the gate).
"""

import json

import pytest

from apps.atlas.migration_parity import compare_parity

FIXTURE_DIR = 'apps/atlas/tests/fixtures/legacy-graph'

#: Verbatim (label, summary/title) rows of the frozen legacy payloads, keyed
#: by locale. The migration maps titles through untouched; the parity test
#: asserts the projection reproduces these EXACT strings.
LEGACY_ROWS = {
    'en': (
        ('Taha Mohammadi', None),
        ('PARS-SQL / VTD-Edge', None),
        ('Story-Driven Dashboard Design Framework', None),
        ('Visual Political Communication Research', None),
    ),
    'fa': (
        ('طه محمدی', None),
        ('PARS-SQL / VTD-Edge', None),
        ('چارچوب طراحی داشبورد روایت‌محور', None),
        ('پژوهش ارتباطات سیاسی بصری', None),
    ),
}


def _load_fixture(locale):
    with open(f'{FIXTURE_DIR}/{locale}.json', encoding='utf-8') as fh:
        return json.load(fh)


def _checks_report(report):
    return ', '.join(
        '{}={}'.format(name, 'PASS' if passed else 'FAIL')
        for name, passed in report['checks'].items()
    )


def _seed_migrated_draft():
    """Seed legacy graphs with the fixtures' verbatim titles; run the command.

    One `_seed_pair` per family, SHARED across locales (not one pair per
    locale): the pair's two rows are the EN and FA halves of ONE logical
    record, so the EN graph references the pair's EN row and the FA graph
    the pair's FA row. Seeding per-locale pairs would create two logical
    profiles and six logical topics — a 7-node draft that fails parity for
    the right reason (this exact bug was caught red here and fixed).
    """
    from uuid import uuid4

    from django.contrib.contenttypes.models import ContentType
    from django.core.management import call_command

    from apps.atlas.tests.factories import _seed_pair
    from apps.content.models import (
        GraphEdge,
        GraphNode,
        GraphNodeRelated,
        GraphVersion,
        Profile,
        ResearchTopic,
    )

    call_command('atlas_seed_taxonomy')
    profile_type = ContentType.objects.get_for_model(Profile)
    topic_type = ContentType.objects.get_for_model(ResearchTopic)
    # NOTE: `_seed_pair` creates published rows EXCEPT the title override we
    # apply below — Profile/ResearchTopic both carry `title`, so setting it
    # keeps the rows inside `objects.public()` (`status=published`,
    # `published_at<=now`); any other field name would silently drop the
    # node from the projection and fail parity for the wrong reason.
    #
    # One pair per family, shared across locales: the pair's EN row feeds
    # the EN graph, its FA row the FA graph.
    en_titles = [label for label, _ in LEGACY_ROWS['en']]
    fa_titles = [label for label, _ in LEGACY_ROWS['fa']]
    profile_pair = _seed_pair(
        Profile, uuid4(), en={'short_bio': 'x'}, fa={'short_bio': 'x'}
    )
    profile_pair[0].title = en_titles[0]
    profile_pair[0].save(update_fields=['title'])
    profile_pair[1].title = fa_titles[0]
    profile_pair[1].save(update_fields=['title'])
    topic_pairs = []
    for index in range(1, 4):
        pair = _seed_pair(
            ResearchTopic, uuid4(), en={'summary': 'x'}, fa={'summary': 'x'}
        )
        pair[0].title = en_titles[index]
        pair[0].save(update_fields=['title'])
        pair[1].title = fa_titles[index]
        pair[1].save(update_fields=['title'])
        topic_pairs.append(pair)
    locale_row = {
        'en': (profile_pair[0], [p[0] for p in topic_pairs]),
        'fa': (profile_pair[1], [p[1] for p in topic_pairs]),
    }
    for locale in ('en', 'fa'):
        profile, topics = locale_row[locale]
        titles = en_titles if locale == 'en' else fa_titles
        version = GraphVersion.objects.create(locale=locale, status='active')
        identity = GraphNode.objects.create(
            version=version,
            node_id='identity',
            label=titles[0],
            type='identity',
            weight=1,
            pos_x=0,
            pos_y=0,
            pos_z=8,
        )
        GraphNodeRelated.objects.create(
            node=identity,
            content_type=profile_type,
            object_id=profile.id,
        )
        for title, topic in zip(titles[1:], topics, strict=False):
            node = GraphNode.objects.create(
                version=version,
                node_id='research-topic-{}'.format(  # noqa: UP032
                    topic.id
                ),
                label=title,
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
    call_command('atlas_migrate_graph', '--label', 'Research Universe v1')


@pytest.mark.django_db
def test_migration_parity_against_frozen_payloads():
    """Every §23.4 row passes per locale; the report names each check."""
    from apps.atlas.models import AtlasVersion
    from apps.atlas.projection import build_locale_projection

    _seed_migrated_draft()
    version = AtlasVersion.objects.get(status='draft')
    legacy = {locale: _load_fixture(locale) for locale in ('en', 'fa')}
    for locale in ('en', 'fa'):
        projection = build_locale_projection(version, locale)
        report = compare_parity(legacy, projection)
        assert report['ok'] is True, (
            f'{locale} parity FAILED: {_checks_report(report)}'
        )
        for name in (
            'nodes',
            'relations',
            'labels',
            'canonicalTargets',
            'relationTypes',
            'relationEndpoints',
            'localeParity',
        ):
            assert report['checks'][name] is True, (
                f'{locale} check {name!r} failed'
            )


@pytest.mark.django_db
def test_parity_rejects_a_tampered_projection():
    """The comparator is a real gate: a dropped node must fail loudly."""
    from apps.atlas.models import AtlasVersion
    from apps.atlas.projection import build_locale_projection

    _seed_migrated_draft()
    version = AtlasVersion.objects.get(status='draft')
    legacy = {locale: _load_fixture(locale) for locale in ('en', 'fa')}
    projection = build_locale_projection(version, 'en')
    tampered = dict(projection, nodes=projection['nodes'][:-1])
    report = compare_parity(legacy, tampered)
    assert report['ok'] is False
    assert report['checks']['nodes'] is False
