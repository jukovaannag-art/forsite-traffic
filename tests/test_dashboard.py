"""Тесты дашборда: знаменатель полноты сбора и выбор периода.

Запуск: python -m pytest tests -q
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.app import (  # noqa: E402
    complete_days,
    expected_slots,
    period_windows,
    plural_days,
    preset_range,
    slice_period,
    window_average,
)


def _frame(rows: list[tuple[date, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": day, "hour": hour, "score": 3} for day, hour in rows]
    )


def _days(
    days: list[date], sources: tuple[str, ...] = ("yandex", "2gis"), score: float = 3
) -> pd.DataFrame:
    """Полностью собранные сутки: все 17 часов окна по каждому источнику."""
    return pd.DataFrame(
        [
            {"date": day, "hour": hour, "source": source, "score": score}
            for day in days
            for hour in range(7, 24)
            for source in sources
        ]
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


def test_незаконченный_день_не_считается_полным() -> None:
    frame = _days([date(2026, 9, 12), date(2026, 9, 13)])
    # Сегодня собрано только до полудня - в неделю такой день не идёт.
    today = frame[frame["date"] == date(2026, 9, 13)].copy()
    today["date"] = date(2026, 9, 14)
    frame = pd.concat([frame, today[today["hour"] <= 12]])
    assert complete_days(frame, ["yandex", "2gis"]) == [
        date(2026, 9, 12),
        date(2026, 9, 13),
    ]


def test_дыра_у_одного_источника_убирает_день_целиком() -> None:
    frame = _days([date(2026, 9, 12)])
    holey = frame[~((frame["source"] == "2gis") & (frame["hour"] == 15))]
    assert complete_days(holey, ["yandex", "2gis"]) == []
    # Если 2ГИС в фильтре не выбран, его дыра дню не мешает.
    assert complete_days(holey, ["yandex"]) == [date(2026, 9, 12)]


def test_источника_нет_в_данных_вовсе_полных_дней_нет() -> None:
    frame = _days([date(2026, 9, 12)], sources=("yandex",))
    assert complete_days(frame, ["yandex", "2gis"]) == []


def test_неделя_берёт_последние_семь_полных_дней_и_семь_до_них() -> None:
    days = [date(2026, 9, day) for day in range(1, 17)]
    current, previous = period_windows(days, "Неделя", days[0], days[-1])
    assert current == [date(2026, 9, day) for day in range(10, 17)]
    assert previous == [date(2026, 9, day) for day in range(3, 10)]


def test_месяц_сравнивается_с_предыдущими_тридцатью_полными_днями() -> None:
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(70)]
    current, previous = period_windows(days, "Месяц", days[0], days[-1])
    assert current == days[-30:]
    assert previous == days[-60:-30]


def test_прошлого_периода_может_не_быть() -> None:
    days = [date(2026, 9, day) for day in range(1, 6)]
    current, previous = period_windows(days, "Неделя", days[0], days[-1])
    assert current == days
    assert previous == []


def test_всё_время_не_с_чем_сравнивать() -> None:
    days = [date(2026, 9, day) for day in range(1, 6)]
    assert period_windows(days, "Всё время", days[0], days[-1]) == (days, [])


def test_свой_период_сравнивается_с_такими_же_сутками_до_него() -> None:
    days = [date(2026, 9, day) for day in range(1, 16)]
    # Выбраны 10-12.09 (трое суток) - предыдущее окно 07-09.09.
    current, previous = period_windows(
        days, "Свой период", date(2026, 9, 10), date(2026, 9, 12)
    )
    assert current == [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]
    assert previous == [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]


def test_в_своём_периоде_неполные_дни_выпадают_из_обоих_окон() -> None:
    # 08.09 и 11.09 собраны не целиком - их нет в списке полных дней.
    days = [date(2026, 9, d) for d in (7, 9, 10, 12)]
    current, previous = period_windows(
        days, "Свой период", date(2026, 9, 10), date(2026, 9, 12)
    )
    assert current == [date(2026, 9, 10), date(2026, 9, 12)]
    assert previous == [date(2026, 9, 7), date(2026, 9, 9)]


def test_перепутанные_границы_своего_периода_не_ломают_сравнение() -> None:
    days = [date(2026, 9, day) for day in range(1, 16)]
    forward = period_windows(days, "Свой период", date(2026, 9, 10), date(2026, 9, 12))
    backward = period_windows(days, "Свой период", date(2026, 9, 12), date(2026, 9, 10))
    assert forward == backward


def test_склонение_дней() -> None:
    assert [plural_days(n) for n in (1, 2, 5, 11, 21, 22)] == [
        "1 день",
        "2 дня",
        "5 дней",
        "11 дней",
        "21 день",
        "22 дня",
    ]


def test_среднее_за_неделю_считается_по_источникам_отдельно() -> None:
    week = [date(2026, 9, day) for day in range(10, 17)]
    frame = pd.concat(
        [_days(week, sources=("yandex",), score=6), _days(week, sources=("2gis",), score=4)]
    )
    # Ночной замер, сползший за полночь, в среднее недели не идёт.
    frame = pd.concat(
        [frame, pd.DataFrame([{"date": week[0], "hour": 2, "source": "yandex", "score": 0}])]
    )
    averages = window_average(frame, week)
    assert averages["yandex"] == 6
    assert averages["2gis"] == 4


def test_среднее_без_дней_пустое() -> None:
    assert window_average(_days([date(2026, 9, 12)]), []).empty


def test_перепутанные_границы_не_дают_пустой_период() -> None:
    # Календарь Streamlit такого не отдаёт, но подмена дат в URL - отдаёт.
    forward = slice_period(_ten_days(), date(2026, 8, 19), date(2026, 8, 21))
    backward = slice_period(_ten_days(), date(2026, 8, 21), date(2026, 8, 19))
    assert len(backward) == len(forward) == 3
