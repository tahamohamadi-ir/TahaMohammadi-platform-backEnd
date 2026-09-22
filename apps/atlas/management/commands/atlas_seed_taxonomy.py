"""Idempotent seed of the Atlas v1 taxonomy vocabulary (Plan D Task 3).

The vocabulary is a literal table in this command — the single source for
the seed — applied with ``update_or_create`` keyed by ``key``. ``--dry-run``
prints the diff without writing.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.atlas.models import AtlasNodeType, AtlasRelationType

NODE_TYPES = [
    {
        'key': 'identity',
        'label_en': 'Identity',
        'label_fa': 'هویت',
        'semantic_role': 'anchor',
        'visual_role': 'anchor',
        'canonical_source': 'profile',
        'allow_as_root': True,
        'allow_children': True,
        'default_importance': 100,
        'filter_visible': False,
    },
    {
        'key': 'research-area',
        'label_en': 'Research area',
        'label_fa': 'دامنهٔ پژوهشی',
        'semantic_role': 'area',
        'visual_role': 'domain',
        'canonical_source': 'research_topic',
        'allow_as_root': True,
        'allow_children': True,
        'default_importance': 80,
        'filter_visible': True,
    },
    {
        'key': 'project',
        'label_en': 'Project',
        'label_fa': 'پروژه',
        'semantic_role': 'record',
        'visual_role': 'record',
        'canonical_source': 'project',
        'allow_as_root': False,
        'allow_children': False,
        'default_importance': 60,
        'filter_visible': True,
    },
    {
        'key': 'publication',
        'label_en': 'Publication',
        'label_fa': 'انتشار',
        'semantic_role': 'record',
        'visual_role': 'record',
        'canonical_source': 'publication',
        'allow_as_root': False,
        'allow_children': False,
        'default_importance': 60,
        'filter_visible': True,
    },
    {
        'key': 'method',
        'label_en': 'Method',
        'label_fa': 'روش',
        'semantic_role': 'utility',
        'visual_role': 'fine',
        'canonical_source': 'method',
        'allow_as_root': False,
        'allow_children': False,
        'default_importance': 45,
        'filter_visible': True,
    },
    {
        'key': 'technology',
        'label_en': 'Technology',
        'label_fa': 'فناوری',
        'semantic_role': 'utility',
        'visual_role': 'fine',
        'canonical_source': 'technology',
        'allow_as_root': False,
        'allow_children': False,
        'default_importance': 45,
        'filter_visible': True,
    },
]

RELATION_TYPES = [
    # (key, label_en, label_fa, inverse_en, inverse_fa, directed,
    #  hierarchy, priority, self_loop, allowed pairs)
    ('specializes', 'specializes', 'تخصیص دارد به', 'generalizes',  # noqa: E501
     'تعمیم می‌دهد', True, True, 90, 'forbid', [('area', 'area')]),
    ('related-to', 'related to', 'مرتبط با', 'related to',  # noqa: E501
     'مرتبط با', False, False, 40, 'forbid', []),
    ('uses', 'uses', 'استفاده می‌کند از', 'used by',  # noqa: E501
     'استفاده شده در', True, False, 70, 'forbid',
     [('project', 'method'), ('project', 'technology'),
      ('method', 'technology')]),
    ('implements', 'implements', 'پیاده‌سازی می‌کند',  # noqa: E501
     'implemented by', 'پیاده‌سازی شده با', True, False, 75, 'forbid',
     [('project', 'method'), ('project', 'technology'),
      ('technology', 'method')]),
    ('applies', 'applies', 'به‌کار می‌گیرد', 'applied in',  # noqa: E501
     'به‌کار رفته در', True, False, 70, 'forbid',
     [('project', 'research-area'), ('publication', 'research-area')]),
    ('produces', 'produces', 'تولید می‌کند', 'produced by',  # noqa: E501
     'تولید شده توسط', True, False, 80, 'forbid',
     [('project', 'publication'), ('research-area', 'publication')]),
    ('published-as', 'published as', 'منتشر شده به‌صورت',  # noqa: E501
     'publishes', 'منتشر می‌کند', True, False, 80, 'forbid',
     [('project', 'publication')]),
    ('supports', 'supports', 'پشتیبانی می‌کند', 'supported by',  # noqa: E501
     'به‌کار رفته در', True, False, 60, 'forbid',
     [('method', 'research-area'), ('technology', 'research-area'),
      ('method', 'project'), ('technology', 'project')]),
    ('informed-by', 'informed by', 'برآمده از', 'informs',  # noqa: E501
     'جهت می‌دهد به', True, False, 50, 'forbid',
     [('project', 'publication'), ('research-area', 'publication'),
      ('method', 'research-area')]),
    ('evaluated-with', 'evaluated with', 'ارزیابی شده با',  # noqa: E501
     'evaluates', 'ارزیابی می‌کند', True, False, 65, 'forbid',
     [('project', 'method'), ('publication', 'method'),
      ('method', 'technology')]),
    ('research-focus', 'research focus', 'تمرکز پژوهشی',  # noqa: E501
     'research focus of', 'تمرکز پژوهشی برای', True, False, 95, 'forbid',
     [('identity', 'research-area')]),
]

# Allowed pairs name node types by their SEMANTIC_ROLE vocabulary position:
# the table below maps the spec's `area → area` style to type keys.
# NOTE: the spec writes pairs as source → target TYPE keys except for the
# `area → area` shorthand (both endpoints are `research-area`); `research-focus`
# is `identity → research-area` in type keys already.
PAIR_ROLE_TO_KEYS = {
    'area': ['research-area'],
    'research-area': ['research-area'],
    'identity': ['identity'],
    'project': ['project'],
    'publication': ['publication'],
    'method': ['method'],
    'technology': ['technology'],
    'any': [],
}


class Command(BaseCommand):
    help = 'Idempotent seed of the Atlas v1 taxonomy (Plan D Task 3).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the planned diff without writing.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        created = updated = 0
        for row in NODE_TYPES:
            _, was_created = self._upsert_node_type(row, dry_run)
            created += was_created
            updated += not was_created
        for spec in RELATION_TYPES:
            _, was_created = self._upsert_relation_type(spec, dry_run)
            created += was_created
            updated += not was_created
        self.stdout.write(  # noqa: UP032
            'atlas_seed_taxonomy: created={} updated={}{}'.format(
                created, updated, ' (dry-run)' if dry_run else ''
            )
        )

    def _upsert_node_type(self, row, dry_run):
        key = row['key']
        try:
            existing = AtlasNodeType.objects.get(key=key)
            changed = any(
                getattr(existing, field) != value
                for field, value in row.items()
                if field != 'key'
            )
            if changed and not dry_run:
                for field, value in row.items():
                    if field != 'key':
                        setattr(existing, field, value)
                existing.save()
            status = 'would-update' if (changed and dry_run) else (
                'update' if changed else 'ok'
            )
            message = 'node-type {}: {}'
            self.stdout.write(  # noqa: UP032
                message.format(key, status)
            )
            return existing, False
        except AtlasNodeType.DoesNotExist:
            if not dry_run:
                return AtlasNodeType.objects.create(**row), True
            message = 'node-type {}: would-create'
            self.stdout.write(  # noqa: UP032
                message.format(key)
            )
            return None, True

    def _upsert_relation_type(self, spec, dry_run):
        (
            key, label_en, label_fa, inverse_en, inverse_fa,
            directed, hierarchy, priority, self_loop, pairs,
        ) = spec
        values = {
            'label_en': label_en,
            'label_fa': label_fa,
            'inverse_label_en': inverse_en,
            'inverse_label_fa': inverse_fa,
            'directed_default': directed,
            'hierarchy_role': hierarchy,
            'visual_priority': priority,
            'self_loop_policy': self_loop,
        }
        try:
            existing = AtlasRelationType.objects.get(key=key)
            changed = any(
                getattr(existing, field) != value
                for field, value in values.items()
            )
            if changed and not dry_run:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.save()
            if not dry_run:
                self._set_pairs(existing, pairs)
            status = 'would-update' if (changed and dry_run) else (
                'update' if changed else 'ok'
            )
            message = 'relation-type {}: {}'
            self.stdout.write(  # noqa: UP032
                message.format(key, status)
            )
            return existing, False
        except AtlasRelationType.DoesNotExist:
            if dry_run:
                message = 'relation-type {}: would-create'
                self.stdout.write(  # noqa: UP032
                    message.format(key)
                )
                return None, True
            row = AtlasRelationType.objects.create(key=key, **values)
            self._set_pairs(row, pairs)
            return row, True

    def _set_pairs(self, row, pairs):
        sources, targets = set(), set()
        for source_role, target_role in pairs:
            sources.update(PAIR_ROLE_TO_KEYS.get(source_role, []))
            targets.update(PAIR_ROLE_TO_KEYS.get(target_role, []))
        if sources:
            row.allowed_source_types.set(
                AtlasNodeType.objects.filter(key__in=sorted(sources))
            )
        else:
            row.allowed_source_types.clear()
        if targets:
            row.allowed_target_types.set(
                AtlasNodeType.objects.filter(key__in=sorted(targets))
            )
        else:
            row.allowed_target_types.clear()
