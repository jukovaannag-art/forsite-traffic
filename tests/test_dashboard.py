"""Тесты дашборда: знаменатель полноты сбора и выбор периода.

Запуск: python -m pytest tests -q
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.app import expected_slots, preset_range, slice_period  # noqa: E402


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


def _ten_days() -> pd.DataFrame:
    return _frame([(date(2026, 8, day), 12) for day in range(17, 27)])


def test_неделя_отсчитывается_от_последнего_замера_и_включает_его() -> None:
    # Не от «сегодня»: если сбор встал, показываем последнюю живую неделю.
    assert preset_range(_ten_days(), "Неделя") == (date(2026, 8, 20), date(2026, 8, 26))


def test_период_длиннее_истории_не_уезжает_за_первый_замер() -> None:
    frame = _frame([(date(2026, 8, 25), 12), (date(2026, 8, 26), 12)])
    assert preset_range(frame, "Месяц") == (date(2026, 8, 25), date(2026, 8, 26))


def test_всё_время_берёт_всю_историю() -> None:
    assert preset_range(_ten_days(), "Всё время") == (
        date(2026, 8, 17),
        date(2026, 8, 26),
    )


def test_свой_период_включает_обе_границы() -> None:
    part = slice_period(_ten_days(), date(2026, 8, 19), date(2026, 8, 21))
    assert sorted(part["date"].unique()) == [
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 21),
    ]


def test_перепутанные_границы_не_дают_пустой_период() -> None:
    # Календарь Streamlit такого не отдаёт, но подмена дат в URL - отдаёт.
    forward = slice_period(_ten_days(), date(2026, 8, 19), date(2026, 8, 21))
    backward = slice_period(_ten_days(), date(2026, 8, 21), date(2026, 8, 19))
    assert len(backward) == len(forward) == 3
