"""Working-days date arithmetic.

Convention:
- A working day is Mon–Fri minus any date in `holidays`.
- `add_working_days(d, n)` advances n working days from d. n=0 anchors to
  d if it is a working day; otherwise advances to the next working day.
- `working_days_between(a, b)` is the inverse:
      add_working_days(a, working_days_between(a, b)) == b
  Returns negative when b precedes a.
"""
from __future__ import annotations

from datetime import date, timedelta


def is_working_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def add_working_days(start: date, n: int, holidays: set[date]) -> date:
    if n == 0:
        d = start
        while not is_working_day(d, holidays):
            d += timedelta(days=1)
        return d

    direction = 1 if n > 0 else -1
    remaining = abs(n)
    d = start
    while remaining > 0:
        d += timedelta(days=direction)
        if is_working_day(d, holidays):
            remaining -= 1
    return d


def working_days_between(a: date, b: date, holidays: set[date]) -> int:
    if a == b:
        return 0
    direction = 1 if b > a else -1
    count = 0
    d = a
    while d != b:
        d += timedelta(days=direction)
        if is_working_day(d, holidays):
            count += direction
    return count
