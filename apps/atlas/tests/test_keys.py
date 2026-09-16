"""Public-key generator — node/group shape, relation composition, key grammar."""

from apps.atlas.keys import (
    PUBLIC_KEY_RE,
    is_valid_public_key,
    new_group_key,
    new_node_key,
    relation_public_key,
)


def test_node_key_shape_and_uniqueness():
    keys = {new_node_key("research-area") for _ in range(200)}
    assert len(keys) == 200
    for key in keys:
        assert key.startswith("research-area-")
        assert PUBLIC_KEY_RE.match(key)
        assert is_valid_public_key(key)


def test_group_key_shape():
    assert new_group_key().startswith("group-")
    assert is_valid_public_key(new_group_key())


def test_relation_key_is_composed_and_direction_ordered():
    directed = relation_public_key(
        "identity-2b3c4d5e", "research-focus", "research-area-1a2b3c4d", directed=True
    )
    assert directed == "identity-2b3c4d5e~research-focus~research-area-1a2b3c4d"

    forward = relation_public_key("a-11111111", "related-to", "b-22222222", directed=False)
    reverse = relation_public_key("b-22222222", "related-to", "a-11111111", directed=False)
    assert forward == reverse == "a-11111111~related-to~b-22222222"
    assert is_valid_public_key(directed) and is_valid_public_key(forward)


def test_key_grammar_rejects_unsafe_characters():
    for bad in ("UPPER-case", "has space", "slash/key", "colon:key", "", "x" * 81):
        assert not is_valid_public_key(bad)
