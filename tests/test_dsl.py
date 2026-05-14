"""Tests for gantt_lib.dsl — predecessor DSL parser.

Grammar (compact):
    entry  := wbs_id relation [lag]
    wbs_id := one or more digits/dots: 1, 1.2, 1.2.3
    relation := FS | SS | FF | SF
    lag    := signed integer working-days offset, e.g. +3, -2

Multiple entries are comma-separated. Whitespace anywhere is ignored.
Invalid input raises PredecessorParseError with a useful message.
"""
from __future__ import annotations

import pytest

from gantt_lib.dsl import Predecessor, PredecessorParseError, parse_predecessors


def test_empty_string_yields_no_predecessors():
    assert parse_predecessors("") == []


def test_whitespace_only_yields_no_predecessors():
    assert parse_predecessors("   \t  ") == []


def test_single_fs_with_positive_lag():
    assert parse_predecessors("1.2FS+3") == [Predecessor(id="1.2", rel="FS", lag=3)]


def test_lag_zero_when_omitted():
    assert parse_predecessors("1.2FS") == [Predecessor(id="1.2", rel="FS", lag=0)]


def test_negative_lag_parses():
    assert parse_predecessors("1.2FS-2") == [Predecessor(id="1.2", rel="FS", lag=-2)]


def test_multiple_predecessors_comma_separated():
    result = parse_predecessors("1.2FS+3, 1.3SS")
    assert result == [
        Predecessor(id="1.2", rel="FS", lag=3),
        Predecessor(id="1.3", rel="SS", lag=0),
    ]


def test_whitespace_tolerated_everywhere():
    result = parse_predecessors("  1.2 FS + 3  ,  1.3 SS  ")
    assert result == [
        Predecessor(id="1.2", rel="FS", lag=3),
        Predecessor(id="1.3", rel="SS", lag=0),
    ]


@pytest.mark.parametrize("rel", ["FS", "SS", "FF", "SF"])
def test_all_four_relation_types(rel):
    assert parse_predecessors(f"1.2{rel}+1") == [Predecessor(id="1.2", rel=rel, lag=1)]


def test_deep_wbs_id_supported():
    assert parse_predecessors("1.2.3.4FS") == [Predecessor(id="1.2.3.4", rel="FS", lag=0)]


def test_top_level_id_supported():
    assert parse_predecessors("3FS") == [Predecessor(id="3", rel="FS", lag=0)]


# ---------- error cases ----------

def test_unknown_relation_raises():
    with pytest.raises(PredecessorParseError) as exc:
        parse_predecessors("1.2XX")
    assert "1.2XX" in str(exc.value)


def test_garbage_in_lag_raises():
    with pytest.raises(PredecessorParseError) as exc:
        parse_predecessors("1.2FS+abc")
    assert "abc" in str(exc.value) or "1.2FS+abc" in str(exc.value)


def test_missing_relation_raises():
    with pytest.raises(PredecessorParseError):
        parse_predecessors("1.2")


def test_only_relation_no_id_raises():
    with pytest.raises(PredecessorParseError):
        parse_predecessors("FS+3")


def test_one_bad_entry_in_a_list_raises():
    # If any entry is invalid, the whole parse fails — fail loud, fail fast.
    with pytest.raises(PredecessorParseError):
        parse_predecessors("1.2FS, 9.9XX")


def test_predecessor_is_hashable_for_dedup():
    # Cascade may want to put predecessors in a set.
    p1 = Predecessor(id="1.2", rel="FS", lag=3)
    p2 = Predecessor(id="1.2", rel="FS", lag=3)
    assert {p1, p2} == {p1}
