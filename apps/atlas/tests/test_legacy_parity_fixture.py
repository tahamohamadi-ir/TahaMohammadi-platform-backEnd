"""Task 2: the live legacy payloads are frozen for parity (Plan D).

The fixtures are the exact bytes ``https://tahamohamadi.ir/api/graph``
served at capture time. Any later change to the live graph fails this test
loudly instead of silently re-baselining.
"""

import json
from pathlib import Path

EXPECTED_LABELS = {
    'en': [
        'Taha Mohammadi',
        'PARS-SQL / VTD-Edge',
        'Story-Driven Dashboard Design Framework',
        'Visual Political Communication Research',
    ],
    'fa': [
        'طه محمدی',
        'PARS-SQL / VTD-Edge',
        'چارچوب طراحی داشبورد روایت‌محور',
        'پژوهش ارتباطات سیاسی بصری',
    ],
}


def load(locale):
    path = (
        Path(__file__).parent / 'fixtures' / 'legacy-graph' / f'{locale}.json'
    )
    return json.loads(path.read_text(encoding='utf-8'))


def test_legacy_payload_shape_per_locale():
    for locale in ('en', 'fa'):
        payload = load(locale)
        assert len(payload['nodes']) == 4
        assert len(payload['edges']) == 3
        assert [n['label'] for n in payload['nodes']] == EXPECTED_LABELS[
            locale
        ]
        node_ids = {n['id'] for n in payload['nodes']}
        for edge in payload['edges']:
            assert edge['relationType'] == 'research-focus'
            assert edge['source'] in node_ids
            assert edge['target'] in node_ids


def test_identity_references_profile_family():
    for locale in ('en', 'fa'):
        payload = load(locale)
        identity = next(n for n in payload['nodes'] if n['id'] == 'identity')
        assert len(identity['relatedRecords']) == 1
        assert identity['relatedRecords'][0]['family'] == 'profile'
        for node in payload['nodes']:
            if node['id'] == 'identity':
                continue
            assert node['relatedRecords'][0]['family'] == 'researchtopic'
