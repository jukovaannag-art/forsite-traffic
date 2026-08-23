"""Тесты дашборда: знаменатель полноты сбора.

Запуск: python -m pytest tests -q
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.app import expected_slots  # noqa: E402


def _frame(rows: list[tuple[date, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": day, "hour": hour, "score": 3} for day, hour in rows]
    )


def test_первый_день_считается_от_первого_замера() -> None:
    # Сбор стартовал в 15:00 и шёл до 22:00: часов 15..22 = 8, два источника.
    # Полные сутки дали бы 34 и штрафовали за время до старта.
    frame = _frame([(date(2026, 8, 19), hour) for hour in range(15, 23)])
    assert expected_slots(frame, sources=2) == 16


def test_полные_сутки_внутри_периода_считаются_целиком() -> None:
    rows = [(date(2026, 8, 19), 7), (date(2026, 8, 19), 23)]
    rows += [(date(2026, 8, 20), 7), (date(2026, 8, 20), 23)]
    assert expected_slots(_frame(rows), sources=2) == 68


def test_дыра_внутри_дня_не_уменьшает_знаменатель() -> None:
    # Знаменатель - сколько можно было снять, а не сколько сняли.
    full = _frame([(date(2026, 8, 20), hour) for hour in range(7, 24)])
    holey = _frame([(date(2026, 8, 20), hour) for hour in (7, 12, 23)])
    assert expected_slots(holey, sources=1) == expected_slots(full, sources=1) == 17


def test_средний_день_без_замеров_остаётся_в_знаменателе() -> None:
    # 20.08 пропущено целиком - полнота должна за это штрафовать.
    rows = [(date(2026, 8, 19), 7), (date(2026, 8, 21), 23)]
    assert expected_slots(_frame(rows), sources=1) == 17 * 3

