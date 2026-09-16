"""Public-key grammar and generators for Atlas nodes, groups and relations.

Node keys are ``<node-type-key>-<8 hex>``, group keys are ``group-<8 hex>`` and
relation keys compose ``<source>~<relation-type>~<target>``. For undirected
relation types the two endpoints are ordered lexicographically, so the same
pair always produces the same key. Node and group keys never contain ``~``
(they are built by ``new_node_key``/``new_group_key``), so a ``~`` in a URL key
unambiguously means "relation".
"""

from __future__ import annotations

import re
import uuid

# The spec's key grammar: 2–80 characters, first character alphanumeric, the
# rest alphanumeric plus ``. _ ~ -``. A leading ``~`` is impossible, which is
# what keeps a relation key distinguishable from a node key. The pattern ends
# with ``\Z`` rather than ``$``: ``$`` also matches *before a final newline*, so
# a ``$``-anchored pattern accepts ``"ab\n"`` under both ``match`` and
# ``fullmatch`` — keys are persisted identifiers, so every caller (including
# model ``clean()``) must reject that.
PUBLIC_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._~-]{1,79}\Z")
_SUFFIX_BYTES = 4


def _suffix() -> str:
    return uuid.uuid4().hex[:_SUFFIX_BYTES * 2]  # 8 lowercase hex characters


def new_node_key(node_type_key: str) -> str:
    return f"{node_type_key}-{_suffix()}"


def new_group_key() -> str:
    return f"group-{_suffix()}"


def relation_public_key(source: str, relation_type: str, target: str, *, directed: bool) -> str:
    first, second = (source, target) if directed or source <= target else (target, source)
    return f"{first}~{relation_type}~{second}"


def is_valid_public_key(value: str) -> bool:
    """True when ``value`` matches the whole key grammar.

    ``fullmatch`` plus the pattern's ``\\Z`` anchor means a trailing newline can
    never be accepted; the grammar's length bounds are fully expressed by the
    pattern, so no separate length check is needed.
    """
    return isinstance(value, str) and PUBLIC_KEY_RE.fullmatch(value) is not None
