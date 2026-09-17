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
    closed_window,
    collected_hours,
    complete_days,
    expected_slots,
    heat_days,
    month_start,
    open_window,
    period_windows,
    plural_days,
    preset_range,
    preset_window,
    running_day,
    slice_period,
    week_start,
    window_average,
)

# 15.09.2026 - вторник: неделя 14-20.09 идёт, последняя закрытая 07-13.09.
TUESDAY = date(2026, 9, 15)


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


def test_начало_недели_это_понедельник() -> None:
    assert week_start(TUESDAY) == date(2026, 9, 14)
    # Воскресенье принадлежит своей неделе, а не следующей.
    assert week_start(date(2026, 9, 13)) == date(2026, 9, 7)
    assert month_start(TUESDAY) == date(2026, 9, 1)


def test_неделя_это_последняя_закрытая_календарная_неделя() -> None:
    # Во вторник текущая неделя ещё идёт - считаем 07-13.09, сравниваем с 31.08-06.09.
    assert closed_window("Неделя", TUESDAY, 0) == (date(2026, 9, 7), date(2026, 9, 13))
    assert closed_window("Неделя", TUESDAY, 1) == (date(2026, 8, 31), date(2026, 9, 6))


def test_в_понедельник_неделя_не_перескакивает_на_текущую() -> None:
    # 14.09 понедельник: неделя только началась, расчётной остаётся 07-13.09.
    assert closed_window("Неделя", date(2026, 9, 14), 0) == (
        date(2026, 9, 7),
        date(2026, 9, 13),
    )
    # И в воскресенье 20.09 тоже: неделя закроется только в полночь.
    assert closed_window("Неделя", date(2026, 9, 20), 0) == (
        date(2026, 9, 7),
        date(2026, 9, 13),
    )


def test_две_недели_это_две_закрытые_недели_подряд() -> None:
    assert closed_window("2 недели", TUESDAY, 0) == (date(2026, 8, 31), date(2026, 9, 13))
    assert closed_window("2 недели", TUESDAY, 1) == (date(2026, 8, 17), date(2026, 8, 30))


def test_месяц_это_последний_закрытый_календарный_месяц() -> None:
    assert closed_window("Месяц", TUESDAY, 0) == (date(2026, 8, 1), date(2026, 8, 31))
    assert closed_window("Месяц", TUESDAY, 1) == (date(2026, 7, 1), date(2026, 7, 31))
    # Первое января смотрит на декабрь прошлого года.
    assert closed_window("Месяц", date(2027, 1, 1), 0) == (
        date(2026, 12, 1),
        date(2026, 12, 31),
    )


def test_текущий_незакрытый_период_идёт_с_начала_недели_или_месяца() -> None:
    assert open_window("Неделя", TUESDAY) == (date(2026, 9, 14), TUESDAY)
    # У «2 недель» хвост - текущая неделя, а не две: вперёд показывать нечего.
    assert open_window("2 недели", TUESDAY) == (date(2026, 9, 14), TUESDAY)
    assert open_window("Месяц", TUESDAY) == (date(2026, 9, 1), TUESDAY)


def test_пустая_закрытая_неделя_отматывается_назад_к_живой() -> None:
    # Сбор встал 30.08: недели 31.08-06.09 и 07-13.09 пустые, показываем 24-30.08.
    complete = [date(2026, 8, day) for day in range(24, 31)]
    now, was = preset_window("Неделя", complete, TUESDAY)
    assert now == (date(2026, 8, 24), date(2026, 8, 30))
    assert was == (date(2026, 8, 17), date(2026, 8, 23))


def test_данных_нет_вовсе_окно_остаётся_последним_закрытым() -> None:
    now, was = preset_window("Неделя", [], TUESDAY)
    assert now == (date(2026, 9, 7), date(2026, 9, 13))
    assert was == (date(2026, 8, 31), date(2026, 9, 6))


def test_фильтр_недели_запрашивает_от_понедельника_до_конца_истории() -> None:
    # Правая граница - конец данных: дни текущей недели показываются сверх окна.
    frame = _frame([(date(2026, 9, day), 12) for day in range(1, 16)])
    assert preset_range(frame, "Неделя", TUESDAY) == (date(2026, 9, 7), TUESDAY)


def test_период_длиннее_истории_не_уезжает_за_первый_замер() -> None:
    frame = _frame([(date(2026, 8, 25), 12), (date(2026, 8, 26), 12)])
    assert preset_range(frame, "Месяц", TUESDAY) == (date(2026, 8, 25), date(2026, 8, 26))


def test_всё_время_берёт_всю_историю() -> None:
    assert preset_range(_ten_days(), "Всё время", TUESDAY) == (
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


def test_неделя_берёт_календарную_неделю_и_неделю_перед_ней() -> None:
    days = [date(2026, 8, 31) + timedelta(days=i) for i in range(15)]  # 31.08-14.09
    current, previous = period_windows(days, "Неделя", days[0], days[-1], TUESDAY)
    assert current == [date(2026, 9, day) for day in range(7, 14)]
    assert previous == [date(2026, 8, 31)] + [date(2026, 9, day) for day in range(1, 7)]
    # Понедельник текущей недели собран целиком, но в расчёт не идёт.
    assert date(2026, 9, 14) not in current + previous


def test_дыра_внутри_календарной_недели_не_растягивает_окно() -> None:
    # 09.09 собран не целиком - неделя считается по шести дням, а не лезет в 06.09.
    days = [date(2026, 9, d) for d in (5, 6, 7, 8, 10, 11, 12, 13)]
    current, previous = period_windows(days, "Неделя", days[0], days[-1], TUESDAY)
    assert current == [date(2026, 9, d) for d in (7, 8, 10, 11, 12, 13)]
    assert previous == [date(2026, 9, 5), date(2026, 9, 6)]


def test_месяц_сравнивается_с_предыдущим_календарным_месяцем() -> None:
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(76)]  # 01.07-14.09
    current, previous = period_windows(days, "Месяц", days[0], days[-1], TUESDAY)
    assert current == [date(2026, 8, 1) + timedelta(days=i) for i in range(31)]
    assert previous == [date(2026, 7, 1) + timedelta(days=i) for i in range(31)]


def test_прошлого_периода_может_не_быть() -> None:
    # Данные только за расчётную неделю - сравнивать не с чем.
    days = [date(2026, 9, day) for day in range(7, 14)]
    current, previous = period_windows(days, "Неделя", days[0], days[-1], TUESDAY)
    assert current == days
    assert previous == []


def test_всё_время_не_с_чем_сравнивать() -> None:
    days = [date(2026, 9, day) for day in range(1, 6)]
    assert period_windows(days, "Всё время", days[0], days[-1], TUESDAY) == (days, [])


def test_свой_период_сравнивается_с_такими_же_сутками_до_него() -> None:
    days = [date(2026, 9, day) for day in range(1, 16)]
    # Выбраны 10-12.09 (трое суток) - предыдущее окно 07-09.09.
    current, previous = period_windows(
        days, "Свой период", date(2026, 9, 10), date(2026, 9, 12), TUESDAY
    )
    assert current == [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]
    assert previous == [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]


def test_в_своём_периоде_неполные_дни_выпадают_из_обоих_окон() -> None:
    # 08.09 и 11.09 собраны не целиком - их нет в списке полных дней.
    days = [date(2026, 9, d) for d in (7, 9, 10, 12)]
    current, previous = period_windows(
        days, "Свой период", date(2026, 9, 10), date(2026, 9, 12), TUESDAY
    )
    assert current == [date(2026, 9, 10), date(2026, 9, 12)]
    assert previous == [date(2026, 9, 7), date(2026, 9, 9)]


def test_перепутанные_границы_своего_периода_не_ломают_сравнение() -> None:
    days = [date(2026, 9, day) for day in range(1, 16)]
    forward = period_windows(
        days, "Свой период", date(2026, 9, 10), date(2026, 9, 12), TUESDAY
    )
    backward = period_windows(
        days, "Свой период", date(2026, 9, 12), date(2026, 9, 10), TUESDAY
    )
    assert forward == backward


def _week_plus_today(hours_today: int = 6) -> tuple[pd.DataFrame, list[date], date]:
    """Полные сутки 31.08-14.09 и сегодняшний 15.09, собранный до полудня."""
    today = TUESDAY
    full = [date(2026, 8, 31) + timedelta(days=i) for i in range(15)]
    frame = _days(full)
    part = pd.DataFrame(
        [
            {"date": today, "hour": hour, "source": source, "score": 3}
            for hour in range(7, 7 + hours_today)
            for source in ("yandex", "2gis")
        ]
    )
    return pd.concat([frame, part], ignore_index=True), full, today


SOURCES = ["yandex", "2gis"]


def test_текущий_день_не_попадает_в_расчёт_но_виден_отдельно() -> None:
    frame, full, today = _week_plus_today()
    complete = complete_days(frame, SOURCES)
    assert complete == full
    assert running_day(frame, complete, SOURCES, today) == today
    # Расчёт - закрытая неделя 07-13.09; ни сегодня, ни полный понедельник 14.09
    # в неё не входят.
    current, previous = period_windows(complete, "Неделя", full[0], today, today)
    assert current == [date(2026, 9, day) for day in range(7, 14)]
    assert previous == [date(2026, 8, 31)] + [date(2026, 9, day) for day in range(1, 7)]
    for window in (current, previous):
        assert today not in window
        assert date(2026, 9, 14) not in window


def test_дни_текущей_недели_это_полный_понедельник_и_идущий_вторник() -> None:
    frame, _, today = _week_plus_today()
    complete = complete_days(frame, SOURCES)
    open_from, open_to = open_window("Неделя", today)
    open_days = [day for day in complete if open_from <= day <= open_to]
    # Понедельник собран целиком - он в хвосте, но не в расчёте.
    assert open_days == [date(2026, 9, 14)]
    assert running_day(frame, complete, SOURCES, today) == today


def test_досчитанный_до_конца_сегодняшний_день_перестаёт_быть_текущим() -> None:
    frame, _, today = _week_plus_today()
    frame = pd.concat([frame, _days([today])], ignore_index=True)
    complete = complete_days(frame, SOURCES)
    # День собран целиком - он равноправная часть недели, а не «идёт».
    assert today in complete
    assert running_day(frame, complete, SOURCES, today) is None


def test_упавший_позавчера_сбор_не_выдаётся_за_идущие_сутки() -> None:
    frame, _, _ = _week_plus_today()
    # «Сегодня» уже 17.09, а последние данные - от 15.09: это поломка сбора.
    assert running_day(frame, complete_days(frame, SOURCES), SOURCES, date(2026, 9, 17)) is None


def test_ночью_до_первого_замера_текущего_дня_нет() -> None:
    frame = _days([date(2026, 9, day) for day in range(8, 15)])
    # 00:30 15.09: замеров за сегодня ещё нет, сборщик стартует в 7:00.
    complete = complete_days(frame, SOURCES)
    assert running_day(frame, complete, SOURCES, date(2026, 9, 15)) is None


def test_замеры_текущего_дня_вне_окна_не_делают_его_видимым() -> None:
    frame, _, today = _week_plus_today()
    frame = frame[~((frame["date"] == today) & frame["hour"].between(7, 23))]
    frame = pd.concat(
        [frame, pd.DataFrame([{"date": today, "hour": 2, "source": "yandex", "score": 1}])]
    )
    # Единственный замер сполз за полночь - показывать по нему день нечем.
    assert running_day(frame, complete_days(frame, SOURCES), SOURCES, today) is None


def test_часы_текущего_дня_считаются_по_худшему_источнику() -> None:
    frame, _, today = _week_plus_today(hours_today=6)
    assert collected_hours(frame, today, SOURCES) == 6
    # У 2ГИС дыра в 9:00 - день собран на 5 часов, а не на 6.
    holey = frame[~((frame["date"] == today) & (frame["source"] == "2gis") & (frame["hour"] == 9))]
    assert collected_hours(holey, today, SOURCES) == 5
    assert collected_hours(holey, today, ["yandex"]) == 6


def test_без_выбранных_источников_текущего_дня_нет() -> None:
    frame, _, today = _week_plus_today()
    assert running_day(frame, [], [], today) is None


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


def test_карта_показывает_семь_полных_дней_и_текущий() -> None:
    complete = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]
    days = heat_days(complete, date(2026, 9, 11))
    assert len(days) == 8
    assert days[0] == date(2026, 9, 4)
    assert days[-1] == date(2026, 9, 11)


def test_карта_без_текущего_дня_добирает_полным() -> None:
    complete = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]
    days = heat_days(complete, None)
    assert len(days) == 8
    assert days[-1] == date(2026, 9, 10)


def test_карта_коротких_данных_показывает_что_есть() -> None:
    assert heat_days([date(2026, 9, 1)], date(2026, 9, 2)) == [
        date(2026, 9, 1),
        date(2026, 9, 2),
    ]
    assert heat_days([], None) == []
