"""Canonical CMS-record reference strategy — the closed allow-list and its resolution.

A canonical source key (``profile``, ``research_topic``, ``project``,
``publication``, ``method``, ``technology``) binds a canonical CMS model to the
Atlas node types that reference it (spec §5.3/§6.1). This module owns:

* :data:`CANONICAL_SOURCES` — the **closed and ordered** allow-list of the six
  canonical models (an unlisted family raises ``KeyError``; that is a
  deliberate hard failure, never a silent skip);
* :data:`SUMMARY_FIELDS` — the per-family summary precedence of spec §5.4.2
  ("``summary``, ``short_description``, ``abstract`` or ``description``, in that
  precedence order, whichever the model defines");
* :func:`resolve_canonical` / :func:`resolve_canonical_pair` — exact-locale,
  publish-gated resolution returning the undecorated facts of a record instead
  of a raw ORM row;
* :class:`CanonicalResolution` — one resolved record for one locale, carrying
  the two different family spellings **explicitly** (see below).

**Naming collision — read before importing.** ``apps/atlas/models.py`` also
defines a name ``CANONICAL_SOURCES``: a ``TextChoices`` vocabulary for the
``AtlasNodeType.canonical_source`` / ``AtlasNode.canonical_model`` *columns*.
The dict here is a different object with the same name: its **keys** are that
same canonical-source vocabulary and its values are Django model classes. Keep
both — one is a column vocabulary, the other a resolver registry — and never
import one where the other is meant. The preflight command imports this one
(the plan's "single import-line change"); ``models.py`` keeps the choices.

Resolution rules (spec §5.4, plan Task 8):

1. resolution is **exact-locale**: for locale ``L`` the lookup is restricted to
   ``objects.public()`` in ``L``. Cross-locale fallback is **forbidden**
   (spec §5.3) — the honest outcomes are the per-locale override, the canonical
   row for that exact locale, or ``None`` (which the publish gate turns into a
   blocking ``MISSING_LOCALE_PROJECTION``);
2. an unpublished/draft row (or one whose ``published_at`` is in the future)
   resolves to ``None`` — ``objects.public()`` is the only gate used;
3. more than one published row for the same
   ``(canonical_model, translation_key, locale)`` raises
   :class:`AmbiguousCanonicalRef` — ambiguity is **detected, never guessed**;
4. a ``None`` translation key resolves to ``None`` without a query.

``CanonicalResolution`` carries two family strings because they differ and
Tasks 14/15 need both: ``source_key`` is the registry key of this module
(``research_topic``) and ``family`` is the public/record-resolver family slug
(the lowercase Django model name, ``researchtopic``) that the payload's
``canonical.family`` uses (spec §10.2's example). ``route_family`` comes from
the existing ``apps.api.record_resolver.ROUTE_FAMILY_MAP`` — the table is
imported, never duplicated. The two entities this plan adds have no public
route in v1 (spec §22), so their ``route_family`` is ``None``: the Task 14 wire
mapping is ``{"family": family, "id": id, "slug": slug, "title": title,
"routeFamily": route_family, "href": f"/{locale}/{route_family}/{slug}/"}``
with ``routeFamily``/``href`` omitted where ``route_family`` is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.db import models

from apps.api.record_resolver import ROUTE_FAMILY_MAP
from apps.content.models import (
    Method,
    Profile,
    Project,
    Publication,
    ResearchTopic,
    Technology,
)

#: The closed, ordered allow-list of canonical sources (plan Task 8). Its keys
#: are the canonical-source vocabulary of ``AtlasNodeType.canonical_source``;
#: its values are the models a node may reference. **Closed** means an unlisted
#: family raises ``KeyError`` — deliberately: a typo or a not-yet-supported
#: family must fail loudly instead of publishing an unresolvable node.
#:
#: Distinct object, same name as ``apps.atlas.models.CANONICAL_SOURCES`` (the
#: ``TextChoices`` behind the ``canonical_model`` column) — see the module
#: docstring before importing either one.
CANONICAL_SOURCES: dict[str, type[models.Model]] = {
    "profile": Profile,
    "research_topic": ResearchTopic,
    "project": Project,
    "publication": Publication,
    "method": Method,
    "technology": Technology,
}

#: The allow-list keys in registry order, for admin pickers (Plan B) that need
#: the vocabulary without the model classes. ``tuple`` on purpose: order is
#: part of the contract, and a mutable list would let one caller reorder it.
CANONICAL_SOURCE_KEYS: tuple[str, ...] = tuple(CANONICAL_SOURCES)

#: Per-family summary precedence (spec §5.4.2). The first field a row populates
#: wins; a field the model does not define contributes nothing, so a tuple may
#: name the spec's earlier option even where the model starts at a later one
#: (``Project`` defines ``objective``, not ``summary``; ``Profile`` defines
#: ``short_bio``, not ``summary``). Every family owns at least one real field.
SUMMARY_FIELDS: dict[str, tuple[str, ...]] = {
    "profile": ("short_bio", "summary"),
    "research_topic": ("summary",),
    "project": ("summary", "objective"),
    "publication": ("abstract",),
    "method": ("short_description", "description"),
    "technology": ("short_description", "description"),
}

#: Locales a topology is projected into (spec §10.2). Resolution is per locale;
#: this tuple only spares callers repeating the pair.
DEFAULT_LOCALES: tuple[str, ...] = ("en", "fa")


class AmbiguousCanonicalRef(Exception):
    """More than one published row matched ``(canonical_model, translation_key, locale)``.

    Raised by :func:`resolve_canonical` instead of picking a row: a canonical
    reference that points at two rows is an authoring defect the publish gate
    must report (validation code ``AMBIGUOUS_CANONICAL_REF``, spec §20.1), not a
    choice the resolver may make. The attributes carry the offending triple so
    the validator can name the node it came from without re-parsing the message.
    """

    def __init__(self, source: str, translation_key: UUID, locale: str, *, count: int) -> None:
        self.source = source
        self.translation_key = translation_key
        self.locale = locale
        self.count = count
        super().__init__(
            f"Ambiguous canonical reference: {source}:{translation_key}:{locale} "
            f"matched {count} published rows."
        )


@dataclass(frozen=True)
class CanonicalResolution:
    """One canonical record resolved for one exact locale (spec §5.4).

    ``model``/``row`` are the *record*; ``title``/``summary``/``slug`` are the
    fields the projection borrows from it (spec §5.3: "nodes are references,
    never copies"); ``family``/``source_key`` are the two family spellings;
    ``route_family`` is the record's public route family, ``None`` when v1 has
    no public route for it (spec §22).
    """

    model: type[models.Model]
    row: models.Model
    locale: str
    title: str
    summary: str
    route_family: str | None
    slug: str
    source_key: str
    family: str

    @property
    def id(self) -> str:
        """The record's id as the wire carries it (a string, spec §10.2)."""
        return str(self.row.pk)


def _summary(source: str, row: models.Model) -> str:
    """The first populated field of the family's precedence tuple, stripped."""
    for field in SUMMARY_FIELDS[source]:
        value = getattr(row, field, "")  # absent field → contributes nothing
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _as_resolution(source: str, row: models.Model, locale: str) -> CanonicalResolution:
    """Wrap a resolved row with the facts the projection borrows from it."""
    family = row._meta.model_name
    return CanonicalResolution(
        model=type(row),
        row=row,
        locale=locale,
        title=str(row.title or ""),
        summary=_summary(source, row),
        route_family=ROUTE_FAMILY_MAP.get(family),
        slug=str(row.slug or ""),
        source_key=source,
        family=family,
    )


def resolve_canonical(
    source: str, translation_key: UUID | None, locale: str
) -> CanonicalResolution | None:
    """Resolve one canonical reference in exactly one locale (spec §5.4).

    ``None`` means "this locale has no canonical row for this reference" — the
    value that becomes a publish blocker unless a per-locale override covers it.
    ``KeyError`` means the family is not allow-listed; ``AmbiguousCanonicalRef``
    means more than one row matched. Never falls back to another locale, and
    never returns a row outside ``objects.public()``.
    """
    if translation_key is None:
        return None
    model = CANONICAL_SOURCES[source]
    rows = list(
        model.objects.public().filter(translation_key=translation_key, locale=locale)[:2]
    )
    if not rows:
        return None
    if len(rows) > 1:
        raise AmbiguousCanonicalRef(source, translation_key, locale, count=len(rows))
    return _as_resolution(source, rows[0], locale)


def resolve_canonical_pair(
    source: str,
    translation_key: UUID | None,
    *,
    locales: tuple[str, ...] = DEFAULT_LOCALES,
) -> dict[str, CanonicalResolution | None]:
    """Resolve one canonical reference for several locales — ``{locale: resolution|None}``.

    The shape the locale-parity gate needs (plan Task 10): a per-locale answer
    with no aggregation, so "EN resolves, FA does not" stays visible in both
    directions. ``AmbiguousCanonicalRef`` from any locale propagates.
    """
    return {locale: resolve_canonical(source, translation_key, locale) for locale in locales}
