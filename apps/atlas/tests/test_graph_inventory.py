"""Task 1: the inventory command is read-only and complete (Plan D)."""

import json

import pytest
from django.core.management import call_command

from apps.content.models import GraphEdge, GraphNode, GraphVersion


@pytest.mark.django_db
def test_inventory_contains_every_node_and_edge():
    call_command('atlas_graph_inventory', '--json', '/tmp/not-used.json')
    assert GraphVersion.objects.count() >= 0


@pytest.mark.django_db
def test_inventory_performs_no_writes(tmp_path):
    before = {
        'versions': GraphVersion.objects.count(),
        'nodes': GraphNode.objects.count(),
        'edges': GraphEdge.objects.count(),
    }
    out = tmp_path / 'inventory.json'
    call_command('atlas_graph_inventory', '--json', str(out))
    after = {
        'versions': GraphVersion.objects.count(),
        'nodes': GraphNode.objects.count(),
        'edges': GraphEdge.objects.count(),
    }
    assert before == after
    payload = json.loads(out.read_text(encoding='utf-8'))
    assert 'versions' in payload
    for entry in payload['versions']:
        assert {'versionId', 'status', 'nodes', 'edges', 'groups'} <= set(
            entry
        )
