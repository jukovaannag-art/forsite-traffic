"""Тесты погоды: разбор ответа Open-Meteo, строка для подсказки, границы окон.

Запуск: python -m pytest tests -q
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import weather  # noqa: E402

ANSWER = {
    "daily": {
        "time": ["2026-09-02", "2026-09-03"],
        "weather_code": [53, 81],
        "temperature_2m_min": [8.7, 7.2],
        "temperature_2m_max": [15.7, 11.2],
        "precipitation_sum": [2.6, 32.2],
        "snowfall_sum": [0.0, 0.0],
        "wind_speed_10m_max": [23.9, 23.9],
    }
}


def test_разбирает_день_целиком() -> None:
    parsed = weather._parse(ANSWER)
    day = parsed[date(2026, 9, 3)]
    assert (day.t_min, day.t_max, day.precipitation) == (7.2, 11.2, 32.2)
    assert day.description == "ливень"


def test_день_без_даты_пропускается_а_остальные_остаются() -> None:
    # Сдвиг массивов сломал бы привязку погоды к дню - лучше потерять строку.
    broken = {"daily": dict(ANSWER["daily"], time=["не дата", "2026-09-03"])}
    parsed = weather._parse(broken)
    assert list(parsed) == [date(2026, 9, 3)]
    assert parsed[date(2026, 9, 3)].t_max == 11.2


def test_короткий_массив_не_роняет_разбор() -> None:
    # Источник вправе не отдать какое-то поле за свежие сутки.
    short = {"daily": dict(ANSWER["daily"], precipitation_sum=[2.6])}
    assert weather._parse(short)[date(2026, 9, 3)].precipitation is None


def test_пустой_ответ_даёт_пустой_результат() -> None:
    assert weather._parse({}) == {}


def test_ответ_не_той_формы_не_роняет_дашборд() -> None:
    # Источник может отдать список ошибок или страницу-заглушку вместо данных.
    assert weather._parse([]) == {}
    assert weather._parse({"daily": "сервис недоступен"}) == {}


def test_строка_подсказки_собирается_из_заметного() -> None:
    day = weather._parse(ANSWER)[date(2026, 9, 3)]
    assert day.summary() == "ливень, 7...11°, 32.2 мм, ветер 24 км/ч"


def test_сухой_безветренный_день_не_тащит_нули() -> None:
    dry = weather.DayWeather(
        day=date(2026, 9, 5),
        code=3,
        t_min=5.3,
        t_max=16.0,
        precipitation=0.0,
        snowfall=0.0,
        wind_max=9.4,
    )
    assert dry.summary() == "пасмурно, 5...16°"


def test_снег_вытесняет_осадки_в_миллиметрах() -> None:
    snowy = weather.DayWeather(
        day=date(2026, 12, 1), code=73, t_min=-18, t_max=-9, precipitation=1.4, snowfall=2.0
    )
    assert snowy.summary() == "снег, -18...-9°, снег 2.0 см"


def test_неизвестный_код_не_ломает_строку() -> None:
    assert weather.DayWeather(day=date(2026, 9, 5), code=4242, t_max=10).summary() == "10°"


def test_свежий_период_идёт_в_прогноз_а_давний_в_архив(monkeypatch) -> None:
    # Граница - 92 дня: за ней прогнозный эндпоинт уже не отвечает, а архив
    # ERA5 отстаёт на несколько суток и свежие даты закрыть не может.
    calls: list[tuple[str, date, date]] = []

    def fake(url, start, end):
        calls.append((url, start, end))
        return {}

    monkeypatch.setattr(weather, "_fetch_range", fake)
    today = date(2026, 9, 6)
    weather.fetch_daily(date(2026, 1, 1), today, today=today)

    assert [url for url, _, _ in calls] == [weather.ARCHIVE_URL, weather.FORECAST_URL]
    assert calls[0][1] == date(2026, 1, 1)
    # Окна стыкуются без дыры и без нахлёста.
    assert calls[1][1] - calls[0][2] == date(2026, 9, 6) - date(2026, 9, 5)
    assert calls[1][2] == today


def test_короткий_свежий_период_архив_не_трогает(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        weather, "_fetch_range", lambda url, start, end: calls.append(url) or {}
    )
    today = date(2026, 9, 6)
    weather.fetch_daily(date(2026, 8, 18), today, today=today)
    assert calls == [weather.FORECAST_URL]


def test_обрыв_сети_даёт_пустой_словарь_а_не_исключение(monkeypatch) -> None:
    # Погода поясняет ряд, но не является им: её пропажа не должна ронять дашборд.
    def boom(url, start, end):
        raise OSError("сеть недоступна")

    monkeypatch.setattr(weather, "_fetch_range", boom)
    today = date(2026, 9, 6)
    assert weather.fetch_daily(date(2026, 9, 1), today, today=today) == {}


def test_перепутанные_границы_периода_меняются_местами(monkeypatch) -> None:
    calls: list[tuple[date, date]] = []
    monkeypatch.setattr(
        weather,
        "_fetch_range",
        lambda url, start, end: calls.append((start, end)) or {},
    )
    today = date(2026, 9, 6)
    weather.fetch_daily(today, date(2026, 9, 1), today=today)
    assert calls == [(date(2026, 9, 1), today)]
