"""Tests for gantt_lib.dates — working-days arithmetic.

Working days = Mon–Fri minus holidays. Convention: add_working_days(d, n)
counts n working days forward from d (advancing first if needed). The
inverse holds: add_working_days(a, working_days_between(a, b)) == b.

Named constants are used liberally so failures read like real dates, not
arithmetic puzzles. Anchor week is 2026-05-11 (Monday).
"""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib.dates import add_working_days, is_working_day, working_days_between

# --- Calendar anchors -------------------------------------------------------
# Week of 2026-05-11.
MON = date(2026, 5, 11)
TUE = date(2026, 5, 12)
WED = date(2026, 5, 13)
THU = date(2026, 5, 14)
FRI = date(2026, 5, 15)
SAT = date(2026, 5, 16)
SUN = date(2026, 5, 17)
NEXT_MON = date(2026, 5, 18)
NEXT_TUE = date(2026, 5, 19)
NEXT_WED = date(2026, 5, 20)
PREV_FRI = date(2026, 5, 8)


# ---------- is_working_day ----------

def test_is_working_day_weekday():
    assert is_working_day(MON, set()) is True
    assert is_working_day(FRI, set()) is True


def test_is_working_day_weekend():
    assert is_working_day(SAT, set()) is False
    assert is_working_day(SUN, set()) is False


def test_is_working_day_holiday():
    assert is_working_day(WED, {WED}) is False


# ---------- add_working_days, n > 0 ----------

def test_add_one_workday_within_week():
    assert add_working_days(MON, 1, set()) == TUE


def test_add_one_workday_crosses_weekend():
    assert add_working_days(FRI, 1, set()) == NEXT_MON


def test_add_five_workdays_crosses_one_weekend():
    # Wed + 5 working days = next Wed (Thu, Fri, Mon, Tue, Wed).
    assert add_working_days(WED, 5, set()) == NEXT_WED


def test_holidays_are_skipped():
    # Mon + 2 working days, but Tue is a holiday → land on Thu.
    assert add_working_days(MON, 2, {TUE}) == THU


def test_starting_on_friday_with_holiday_monday():
    # Fri + 1 working day, but Mon is a holiday → next Tue.
    assert add_working_days(FRI, 1, {NEXT_MON}) == NEXT_TUE


# ---------- add_working_days, n < 0 ----------

def test_subtract_one_workday_crosses_weekend():
    assert add_working_days(MON, -1, set()) == PREV_FRI


def test_subtract_five_workdays():
    # Next Mon - 5 = Mon.
    assert add_working_days(NEXT_MON, -5, set()) == MON


def test_subtract_with_holiday():
    # Next Mon - 1, but Fri is a holiday → Thu.
    assert add_working_days(NEXT_MON, -1, {FRI}) == THU


# ---------- add_working_days, n == 0 ----------

def test_zero_workdays_on_working_day_returns_same_day():
    assert add_working_days(MON, 0, set()) == MON


def test_zero_workdays_on_weekend_advances_to_monday():
    assert add_working_days(SAT, 0, set()) == NEXT_MON
    assert add_working_days(SUN, 0, set()) == NEXT_MON


def test_zero_workdays_on_holiday_advances_to_next_working_day():
    assert add_working_days(WED, 0, {WED}) == THU


# ---------- working_days_between ----------

def test_between_same_day_is_zero():
    assert working_days_between(MON, MON, set()) == 0


def test_between_mon_and_next_mon_is_five():
    assert working_days_between(MON, NEXT_MON, set()) == 5


def test_between_mon_and_fri_is_four():
    assert working_days_between(MON, FRI, set()) == 4


def test_between_is_inverse_of_add():
    # The defining contract: round-trip.
    n = working_days_between(MON, NEXT_WED, set())
    assert add_working_days(MON, n, set()) == NEXT_WED


def test_between_with_holiday_skips_it():
    # Mon to Thu spans 3 working days normally; Wed holiday drops to 2.
    assert working_days_between(MON, THU, {WED}) == 2


def test_between_b_before_a_is_negative():
    assert working_days_between(NEXT_MON, MON, set()) == -5


def test_between_anchor_on_weekend_to_weekday():
    # Sat → Mon: must advance 1 working day.
    assert working_days_between(SAT, NEXT_MON, set()) == 1


# ---------- parametric stress: round-trip identity ----------

@pytest.mark.parametrize("days", [1, 2, 5, 10, 22, -1, -5, -22])
def test_round_trip_identity(days):
    holidays = {date(2026, 7, 3), date(2026, 9, 7)}  # 4th of July observed, Labor Day
    target = add_working_days(MON, days, holidays)
    assert working_days_between(MON, target, holidays) == days
