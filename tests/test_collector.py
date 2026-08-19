"""Тесты сборщика: дедупликация и разбор ответов источников.

Запуск: python -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import sources  # noqa: E402
from collector.storage import read_rows, upsert  # noqa: E402


def _row(hour: int, source: str, score: str, error: str = "") -> dict:
    return {
        "ts_utc": "2026-08-18T01:00:00+00:00",
        "ts_local": f"2026-08-18T{hour:02d}:00:00",
        "date": "2026-08-18",
        "hour": hour,
        "source": source,
        "score": score,
        "hint": "",
        "jams_length": "",
        "error": error,
    }


def test_повтор_в_том_же_часе_не_плодит_строк(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"
    upsert(path, [_row(9, "yandex", "3")])
    added, replaced = upsert(path, [_row(9, "yandex", "5")])
    rows = read_rows(path)
    assert (added, replaced) == (0, 0)
    assert len(rows) == 1
    assert rows[0]["score"] == "3", "первое значение внутри часа сохраняется"


def test_ретрай_чинит_дырку(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"
    upsert(path, [_row(9, "2gis", "", error="network: timeout")])
    added, replaced = upsert(path, [_row(9, "2gis", "2")])
    rows = read_rows(path)
    assert (added, replaced) == (0, 1)
    assert len(rows) == 1
    assert rows[0]["score"] == "2" and rows[0]["error"] == ""


def test_разные_часы_и_источники_живут_отдельно(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"
    upsert(path, [_row(9, "yandex", "3"), _row(9, "2gis", "1")])
    upsert(path, [_row(10, "yandex", "4")])
    assert len(read_rows(path)) == 3


def test_яндекс_разбирает_xml() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <info><traffic><region id="63">
      <level>5</level><length>26045.1</length><icon>yellow</icon>
      <hint lang="ru">Затруднения</hint><hint lang="en">Congested</hint>
    </region></traffic></info>""".encode("utf-8")
    with mock.patch.object(sources, "_get", return_value=xml):
        reading = sources.fetch_yandex()
    assert reading.score == 5
    assert reading.hint == "Затруднения"
    assert reading.extra["jams_length"] == 26045.1
    assert reading.error == ""


def test_2гис_разбирает_json() -> None:
    payload = b'{"projects":[{"name":"irkutsk","score":4,"fetch_radius":5000}]}'
    with mock.patch.object(sources, "_get", return_value=payload):
        reading = sources.fetch_dgis()
    assert reading.score == 4 and reading.error == ""


def test_2гис_не_берёт_чужой_город() -> None:
    payload = b'{"projects":[{"name":"novosibirsk","score":7}]}'
    with mock.patch.object(sources, "_get", return_value=payload):
        reading = sources.fetch_dgis()
    assert reading.score is None
    assert "irkutsk" in reading.error


def test_сбой_сети_даёт_пустой_балл_а_не_исключение() -> None:
    with mock.patch.object(sources, "_get", side_effect=OSError("нет сети")):
        assert sources.fetch_yandex().score is None
        assert sources.fetch_dgis().score is None
