"""Read-only legacy graph inventory for the Atlas migration (Plan D Task 1).

Prints per-locale {versionId, status, nodes, edges, groups} from the CURRENT
``GraphVersion`` storage. Read-only: zero writes — row counts are identical
before and after.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.content.models import GraphVersion


def node_entry(node) -> dict:
    related = []
    for ref in node.related_records.all().order_by('id'):
        related.append(
            {
                'family': ref.content_type.model,
                'id': str(ref.object_id),
            }
        )
    return {
        'nodeId': node.node_id,
        'type': node.type,
        'label': node.label,
        'position': {'x': node.pos_x, 'y': node.pos_y, 'z': node.pos_z},
        'relatedRecords': related,
    }


def edge_entry(edge) -> dict:
    return {
        'id': f'{edge.source.node_id}->{edge.target.node_id}:{edge.relation_type}',
        'source': edge.source.node_id,
        'target': edge.target.node_id,
        'relationType': edge.relation_type,
        'directed': edge.directed,
        'weight': edge.weight,
    }


class Command(BaseCommand):
    help = 'Read-only inventory of the legacy graph versions (Plan D Task 1).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--json',
            dest='json_path',
            default=None,
            help='Write the full inventory to this path.',
        )

    def handle(self, *args, **options):
        versions = list(GraphVersion.objects.all().order_by('locale', '-id'))
        payload = {'versions': []}
        for version in versions:
            nodes = list(
                version.nodes.all().order_by('node_id').prefetch_related(
                    'related_records'
                )
            )
            edges = list(
                version.edges.all()
                .order_by('id')
                .select_related('source', 'target')
            )
            groups = list(version.groups.all().order_by('id'))
            payload['versions'].append(
                {
                    'versionId': version.id,
                    'locale': version.locale,
                    'status': version.status,
                    'nodes': [node_entry(n) for n in nodes],
                    'edges': [edge_entry(e) for e in edges],
                    'groups': [
                        {'id': g.id, 'label': g.label} for g in groups
                    ],
                }
            )
        if options['json_path']:
            Path(options['json_path']).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
        for entry in payload['versions']:
            message = (
                'locale={} version={} status={} nodes={} edges={} groups={}'
            )
            self.stdout.write(  # noqa: UP032
                message.format(
                    entry['locale'],
                    entry['versionId'],
                    entry['status'],
                    len(entry['nodes']),
                    len(entry['edges']),
                    len(entry['groups']),
                )
            )
        if not payload['versions']:
            self.stdout.write('atlas_graph_inventory: no GraphVersion rows')
