"""Task 3: taxonomy seed matches spec §6.1/§6.2 (Plan D)."""

import pytest
from django.core.management import call_command

from apps.atlas.models import AtlasNodeType, AtlasRelationType


@pytest.mark.django_db
def test_seed_creates_all_17_rows_with_spec_values():
    call_command('atlas_seed_taxonomy')
    assert AtlasNodeType.objects.count() == 6
    assert AtlasRelationType.objects.count() == 11
    identity = AtlasNodeType.objects.get(key='identity')
    assert identity.semantic_role == 'anchor'
    assert identity.filter_visible is False
    assert identity.default_importance == 100
    focus = AtlasRelationType.objects.get(key='research-focus')
    assert focus.directed_default is True
    assert focus.hierarchy_role is False
    assert focus.visual_priority == 95
    assert set(
        focus.allowed_source_types.values_list('key', flat=True)
    ) == {'identity'}
    assert set(
        focus.allowed_target_types.values_list('key', flat=True)
    ) == {'research-area'}
    assert not AtlasNodeType.objects.filter(key='experience').exists()


@pytest.mark.django_db
def test_seed_is_idempotent_and_updates_single_rows():
    call_command('atlas_seed_taxonomy')
    area = AtlasNodeType.objects.get(key='research-area')
    area.label_en = 'MUTATED'
    area.save()
    call_command('atlas_seed_taxonomy')
    assert AtlasNodeType.objects.count() == 6
    assert AtlasRelationType.objects.count() == 11
    area.refresh_from_db()
    assert area.label_en == 'Research area'
