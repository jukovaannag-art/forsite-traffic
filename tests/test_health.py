"""Тесты сторожа: когда он обязан поднять тревогу, а когда обязан молчать.

Запуск: python -m pytest tests -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.health import (  # noqa: E402
    check_silence,
    check_sources,
    closed_hours,
    hours_word,
    inspect,
)

NOW = datetime(2026, 8, 27, 15, 5)  # 15:05 по Иркутску, окно открыто


def _row(stamp: datetime, source: str = "yandex", score: str = "3", error: str = "") -> dict:
    return {
        "ts_local": stamp.isoformat(),
        "date": stamp.strftime("%Y-%m-%d"),
        "hour": str(stamp.hour),
        "source": source,
        "score": score,
        "error": error,
    }


def test_свежий_замер_тревоги_не_даёт() -> None:
    rows = [_row(NOW - timedelta(minutes=20))]
    assert check_silence(rows, NOW) is None


def test_молчание_дольше_двух_часов_в_окне_это_тревога() -> None:
    rows = [_row(NOW - timedelta(hours=3))]
    problem = check_silence(rows, NOW)
    assert problem is not None and "молчит" in problem


def test_ночью_молчание_законно() -> None:
    # 3:00 - окно закрыто, сборщик спит. Иначе письмо приходило бы каждое утро.
    night = datetime(2026, 8, 27, 3, 0)
    rows = [_row(night - timedelta(hours=5))]
    assert check_silence(rows, night) is None


def test_строка_с_ошибкой_не_считается_замером() -> None:
    # Источник отвечал, но баллом это не стало - для свежести такой строки нет.
    rows = [_row(NOW - timedelta(minutes=10), score="", error="network: timeout")]
    problem = check_silence(rows, NOW)
    assert problem is not None and "нет ни одного удачного замера" in problem


def test_единичные_сбои_источника_не_тревожат() -> None:
    # Один-два неудачных часа чинит следующий опрос, письмо тут лишнее.
    rows = [
        _row(NOW - timedelta(hours=h), score="", error="network: timeout")
        for h in (1, 2)
    ]
    assert check_sources(rows, NOW) == []


def test_три_сбоя_за_сутки_это_тревога_с_диагнозом() -> None:
    rows = [
        _row(NOW - timedelta(hours=h), score="", error="parse: нет level")
        for h in (1, 2, 3)
    ]
    problems = check_sources(rows, NOW)
    assert len(problems) == 1
    assert "yandex" in problems[0] and "parse: нет level" in problems[0]


def test_вчерашние_сбои_в_сегодняшнюю_тревогу_не_идут() -> None:
    rows = [
        _row(NOW - timedelta(hours=h), score="", error="network: timeout")
        for h in (25, 26, 27)
    ]
    assert check_sources(rows, NOW) == []


def test_закрытые_часы_считаются_по_уникальным_часам() -> None:
    rows = [
        _row(NOW.replace(hour=7), source="yandex"),
        _row(NOW.replace(hour=7), source="2gis"),  # тот же час, второй источник
        _row(NOW.replace(hour=8), source="yandex"),
        _row(NOW.replace(hour=9), source="yandex", score="", error="сбой"),  # не в счёт
    ]
    assert closed_hours(rows, "2026-08-27") == 2


def test_часы_склоняются_по_русски() -> None:
    # 11-14 - исключение: «11 часов», а не «11 час».
    assert [hours_word(n) for n in (1, 2, 5, 11, 14, 21, 22)] == [
        "час",
        "часа",
        "часов",
        "часов",
        "часов",
        "час",
        "часа",
    ]


def test_всё_хорошо_значит_ok_и_есть_цифры_для_сводки() -> None:
    rows = [_row(NOW - timedelta(minutes=5))]
    health = inspect(rows, NOW)
    assert health.ok
    assert any("закрыто" in note for note in health.notes)
