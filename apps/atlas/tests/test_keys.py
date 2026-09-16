"""Public-key generator — node/group shape, relation composition, key grammar."""

import pytest

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


def test_key_grammar_matches_the_whole_string():
    # `$` would also match before a final newline; keys are persisted identifiers,
    # so a trailing newline must be rejected rather than silently accepted — and
    # direct `PUBLIC_KEY_RE.match(...)` callers must not accept it either.
    for bad in ("ab\n", "research-area-1a2b3c4d\n", " a-11111111", "a-11111111 "):
        assert not is_valid_public_key(bad), bad
        assert not PUBLIC_KEY_RE.match(bad), bad


def test_key_grammar_length_bounds():
    assert is_valid_public_key("ab")                       # shortest legal key
    assert not is_valid_public_key("a")                    # one character is too short
    assert is_valid_public_key("x" * 80)                   # longest legal key
    assert not is_valid_public_key("x" * 81)


def test_generated_node_and_group_keys_never_contain_the_separator():
    # The "a `~` in a URL key means relation" property depends on this: a node key
    # carrying `~` would be read as a relation key by every URL codec.
    for _ in range(50):
        assert "~" not in new_node_key("research-area")
        assert "~" not in new_group_key()


def test_node_key_generator_rejects_a_type_key_containing_the_separator():
    with pytest.raises(ValueError):
        new_node_key("a~b")
