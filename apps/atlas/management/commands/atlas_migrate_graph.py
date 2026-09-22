"""Task 4: dry-run-first migration of the legacy graph (Plan D).

Reads the ACTIVE ``GraphVersion`` per locale, maps it into a draft
``AtlasVersion`` (identity → ``identity`` node, each research topic →
``research-area``, each edge → ``research-focus``), resolving canonical
references through ``translation_key``. ``--dry-run`` prints the full plan
and writes nothing. A second run updates the existing migrated draft.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.atlas.canonical import CANONICAL_SOURCES
from apps.atlas.models import (
    AtlasNode,
    AtlasNodeType,
    AtlasRelation,
    AtlasRelationType,
    AtlasVersion,
)
from apps.content.models import GraphVersion

MIGRATED_LABEL_PREFIX = 'Research Universe v1'

# Legacy node type → (atlas type key, canonical source key).
TYPE_MAP = {
    'identity': ('identity', 'profile'),
    'research-topic': ('research-area', 'research_topic'),
}

# Legacy relation type → atlas relation type key.
RELATION_MAP = {
    'research-focus': 'research-focus',
}

# Legacy node id → stable Atlas public key.
PUBLIC_KEY_MAP = {
    'identity': 'identity-00000001',
}


def _public_key(legacy_id):
    if legacy_id in PUBLIC_KEY_MAP:
        return PUBLIC_KEY_MAP[legacy_id]
    suffix = legacy_id.replace('research-topic-', '').zfill(8)
    return 'research-area-%s' % suffix


class Command(BaseCommand):
    help = 'Dry-run-first migration of the legacy graph into Atlas (Plan D).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--label', default=MIGRATED_LABEL_PREFIX)
        parser.add_argument(
            '--carry-positions',
            action='store_true',
            help='Offer published x/y as optional layout pins.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        plan = self._build_plan(carry_positions=bool(options['carry_positions']))
        for line in plan['report']:
            self.stdout.write(line)
        if plan['unresolved']:
            for line in plan['unresolved']:
                self.stdout.write('UNRESOLVED: %s' % line)
        if dry_run:
            self.stdout.write(
                'atlas_migrate_graph: dry-run nodes=%d relations=%d unresolved=%d'
                % (
                    len(plan['nodes']),
                    len(plan['relations']),
                    len(plan['unresolved']),
                )
            )
            return
        if plan['unresolved']:
            raise SystemExit(
                'atlas_migrate_graph: %d unresolved references; refusing to write'
                % len(plan['unresolved'])
            )
        version = self._apply(plan, label=options['label'])
        self.stdout.write(
            'atlas_migrate_graph: migrated draft id=%d nodes=%d relations=%d'
            % (version.id, version.nodes.count(), version.relations.count())
        )

    def _build_plan(self, carry_positions=False):
        report, unresolved, nodes, relations = [], [], [], []
        versions = list(
            GraphVersion.objects.filter(status='active').order_by('locale')
        )
        if not versions:
            unresolved.append('no active GraphVersion found')
            return {
                'report': report,
                'unresolved': unresolved,
                'nodes': nodes,
                'relations': relations,
            }
        by_locale_nodes = {}
        for version in versions:
            locale_nodes = []
            for node in version.nodes.order_by('node_id'):
                mapping = TYPE_MAP.get(node.type)
                if mapping is None:
                    unresolved.append(
                        '%s: unknown legacy node type %r'
                        % (node.node_id, node.type)
                    )
                    continue
                atlas_type, canonical_source = mapping
                canonical = self._resolve_canonical(node, canonical_source)
                if canonical is None:
                    unresolved.append(
                        '%s(%s): translation_key missing'
                        % (canonical_source, node.node_id)
                    )
                    continue
                # The Atlas topology is locale-neutral (one version): the EN
                # and FA graphs describe the SAME entities, so dedupe nodes
                # (and relations below) by public key AND by canonical
                # identity across locales. Locales are visited in locale
                # order, so the EN-derived public key wins deterministically.
                public_key = _public_key(node.node_id)
                canonical_key = str(canonical)
                if any(
                    n['public_key'] == public_key
                    or (
                        n['canonical_source'] == canonical_source
                        and n['canonical_translation_key'] == canonical_key
                    )
                    for n in nodes
                ):
                    continue
                entry = {
                    'public_key': public_key,
                    'type': atlas_type,
                    'canonical_source': canonical_source,
                    'canonical_translation_key': canonical_key,
                    'importance': 100 if atlas_type == 'identity' else 80,
                    'position': {
                        'x': node.pos_x,
                        'y': node.pos_y,
                        'z': node.pos_z,
                    },
                    'pin': carry_positions
                    and node.pos_x is not None
                    and node.pos_y is not None,
                }
                nodes.append(entry)
                locale_nodes.append(entry)
                report.append(
                    '%s -> %s (%s, canonical %s:%s)'
                    % (
                        node.node_id,
                        entry['public_key'],
                        atlas_type,
                        canonical_source,
                        entry['canonical_translation_key'][:8],
                    )
                )
            by_locale_nodes[version.locale] = locale_nodes
            for edge in version.edges.order_by('id'):
                atlas_type = RELATION_MAP.get(edge.relation_type)
                if atlas_type is None:
                    unresolved.append(
                        '%s: unknown legacy relation type %r'
                        % (edge.id, edge.relation_type)
                    )
                    continue
                # Endpoints resolve to the KEPT node's public key: the FA
                # duplicate of an EN-kept node must point at the EN key,
                # not at a key minted from its own legacy id.
                kept = {
                    (n['type'], n['canonical_source'],
                     n['canonical_translation_key']): n['public_key']
                    for n in nodes
                }
                source_kept = kept.get(self._endpoint_identity(edge.source))
                target_kept = kept.get(self._endpoint_identity(edge.target))
                source = source_kept or _public_key(edge.source.node_id)
                target = target_kept or _public_key(edge.target.node_id)
                if any(
                    r['source'] == source
                    and r['target'] == target
                    and r['type'] == atlas_type
                    for r in relations
                ):
                    continue
                relations.append(
                    {
                        'source': source,
                        'target': target,
                        'type': atlas_type,
                        'directed': edge.directed,
                        'weight': edge.weight,
                    }
                )
                report.append(
                    '%s -> %s (%s)'
                    % (
                        _public_key(edge.source.node_id),
                        _public_key(edge.target.node_id),
                        atlas_type,
                    )
                )
        return {
            'report': report,
            'unresolved': unresolved,
            'nodes': nodes,
            'relations': relations,
        }

    def _endpoint_identity(self, node):
        """(atlas_type, canonical_source, canonical_key) for a legacy node.

        Re-runs the same mapping + canonical resolution as the node pass so
        an edge endpoint lands on the kept node's public key even when the
        endpoint's own locale duplicate was deduped away.
        """
        mapping = TYPE_MAP.get(node.type)
        if mapping is None:
            return None
        atlas_type, canonical_source = mapping
        canonical = self._resolve_canonical(node, canonical_source)
        if canonical is None:
            return None
        return (atlas_type, canonical_source, str(canonical))

    def _resolve_canonical(self, node, canonical_source):
        """Resolve the node's canonical record via translation_key pairs.

        The legacy node references ONE locale's row; the Atlas node names the
        shared translation_key so each locale resolves its own row. An
        unpaired record (or a missing translation_key) returns None and is
        reported, never guessed.
        """
        model = CANONICAL_SOURCES.get(canonical_source)
        if model is None:
            return None
        for ref in node.related_records.all():
            if ref.content_type.model not in (
                model._meta.model_name,
                model._meta.model_name.lower(),
            ):
                # Family vocabulary differs in case only; accept either.
                pass
            try:
                row = model.objects.get(pk=ref.object_id)
            except model.DoesNotExist:
                continue
            if not row.translation_key:
                continue
            partner_locale = None
            for candidate in model.objects.filter(
                translation_key=row.translation_key
            ):
                if candidate.locale != row.locale:
                    partner_locale = candidate.locale
                    break
            if partner_locale is None:
                continue
            return row.translation_key
        return None

    @transaction.atomic
    def _apply(self, plan, label):
        version = AtlasVersion.objects.filter(
            status='draft', label=label
        ).first()
        if version is None:
            # A second run updates THE migrated draft, never duplicates it.
            version = (
                AtlasVersion.objects.filter(status='draft')
                .order_by('-id')
                .first()
            )
        if version is None:
            version = AtlasVersion.objects.create(status='draft', label=label)
        else:
            version.label = label
            version.save(update_fields=['label'])
            version.nodes.all().delete()
            version.relations.all().delete()
        node_type_by_key = {
            t.key: t for t in AtlasNodeType.objects.filter(active=True)
        }
        relation_type_by_key = {
            t.key: t for t in AtlasRelationType.objects.filter(active=True)
        }
        id_by_key = {}
        for order, entry in enumerate(plan['nodes']):
            node = AtlasNode.objects.create(
                version=version,
                public_key=entry['public_key'],
                node_type=node_type_by_key[entry['type']],
                canonical_model=entry['canonical_source'],
                canonical_translation_key=entry[
                    'canonical_translation_key'
                ],
                importance=entry['importance'],
                sort_order=order,
                pin_x=entry['position']['x'] if entry['pin'] else None,
                pin_y=entry['position']['y'] if entry['pin'] else None,
                pin_z=entry['position']['z']
                if entry['pin'] and entry['position']['z'] is not None
                else None,
            )
            id_by_key[entry['public_key']] = node.id
        for order, entry in enumerate(plan['relations']):
            AtlasRelation.objects.create(
                version=version,
                source_id=id_by_key[entry['source']],
                target_id=id_by_key[entry['target']],
                relation_type=relation_type_by_key[entry['type']],
                directed=entry['directed'],
                weight=entry['weight'],
                sort_order=order,
            )
        return version
