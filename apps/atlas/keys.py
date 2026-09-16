"""Public-key grammar and generators for Atlas nodes, groups and relations.

Node keys are ``<node-type-key>-<8 hex>``, group keys are ``group-<8 hex>`` and
relation keys compose ``<source>~<relation-type>~<target>``. For undirected
relation types the two endpoints are ordered lexicographically, so the same
pair always produces the same key. Node and group keys never contain ``~``
(``new_node_key`` rejects a type key that does, and ``new_group_key`` mints a
fixed prefix), so a ``~`` in a URL key unambiguously means "relation".
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
    """Mint a node key. The type key must not contain the relation separator.

    ``PUBLIC_KEY_RE`` permits ``~`` (relation keys need it), so the "a ``~`` in a
    URL key means relation" property is only true if node/group keys never carry
    one. Node types are validated to ``[a-z0-9-]`` elsewhere, but this generator
    is the last line of defence: minting ``new_node_key("a~b")`` would produce a
    key that every URL codec reads as a relation key.
    """
    if "~" in node_type_key:
        raise ValueError(f"node type key must not contain '~': {node_type_key!r}")
    return f"{node_type_key}-{_suffix()}"


def new_group_key() -> str:
    return f"group-{_suffix()}"


def relation_public_key(source: str, relation_type: str, target: str, *, directed: bool) -> str:
    first, second = (source, target) if directed or source <= target else (target, source)
    return f"{first}~{relation_type}~{second}"


def is_valid_public_key(value: str) -> bool:
    """True when ``value`` matches the whole key grammar.

    ``fullmatch`` means the whole string must match; the pattern's ``\\Z`` anchor
    additionally protects the raw ``PUBLIC_KEY_RE.match(...)`` callers the plan
    requires (``$`` would let ``"ab\\n"`` through under ``match``). The grammar's
    length bounds are fully expressed by the pattern, so no separate length check
    is needed.

    Note: this validates a *single* key segment (2–80 chars). A composed relation
    key can legitimately exceed 80 characters, so relation keys must be validated
    per segment rather than with this helper.
    """
    return isinstance(value, str) and PUBLIC_KEY_RE.fullmatch(value) is not None
