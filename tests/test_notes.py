"""Тесты заметок к дню: разбор, локальная запись, конфликт версий в GitHub.

Запуск: python -m pytest tests -q
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import notes  # noqa: E402

DAY = date(2026, 9, 3)


def _storage(tmp_path: Path, token: str = "") -> notes.Storage:
    return notes.Storage(path=tmp_path / "day_notes.csv", token=token)


def test_разбор_пропускает_битые_и_пустые_строки() -> None:
    text = (
        "date,note,updated_utc\n"
        "2026-09-03,Перекрыт мост,2026-09-03T10:00:00+00:00\n"
        "не дата,Мусор,\n"
        "2026-09-04,,\n"
    )
    assert notes.parse(text) == {DAY: "Перекрыт мост"}


def test_запись_и_чтение_локального_файла(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    where = notes.save(storage, DAY, "  Ремонт Глазковского моста  ")
    assert "локальный файл" in where
    assert notes.load(storage) == {DAY: "Ремонт Глазковского моста"}


def test_пустая_заметка_стирает_старую(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    notes.save(storage, DAY, "Ремонт")
    notes.save(storage, DAY, "   ")
    assert notes.load(storage) == {}


def test_соседние_дни_не_затираются(tmp_path: Path) -> None:
    # Пишем файл целиком, поэтому потеря чужой строки - реальный риск.
    storage = _storage(tmp_path)
    notes.save(storage, DAY, "Ливень, стоял весь центр")
    notes.save(storage, date(2026, 9, 4), "Праздник")
    assert notes.load(storage) == {
        DAY: "Ливень, стоял весь центр",
        date(2026, 9, 4): "Праздник",
    }


def test_правка_одного_дня_не_меняет_время_соседнего(tmp_path: Path) -> None:
    # Файл пишется целиком, и общая отметка «сейчас» стёрла бы историю правок
    # всех остальных дней разом.
    storage = _storage(tmp_path)
    storage.path.parent.mkdir(parents=True, exist_ok=True)
    storage.path.write_text(
        "date,note,updated_utc\n2026-09-03,Ливень,2026-09-03T10:00:00+00:00\n",
        encoding="utf-8",
    )
    notes.save(storage, date(2026, 9, 4), "Праздник")

    saved = notes._parse_dated(storage.path.read_text(encoding="utf-8"))
    assert saved[DAY][1] == "2026-09-03T10:00:00+00:00"
    assert saved[date(2026, 9, 4)][1] != "2026-09-03T10:00:00+00:00"


def test_длинная_заметка_обрезается(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    notes.save(storage, DAY, "я" * (notes.MAX_NOTE_LEN + 50))
    assert len(notes.load(storage)[DAY]) == notes.MAX_NOTE_LEN


def test_чтение_без_файла_даёт_пусто(tmp_path: Path) -> None:
    assert notes.load(_storage(tmp_path)) == {}


class _Response:
    """Заглушка ответа urlopen: годится и для with, и для .read()."""

    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_) -> bool:
        return False


def _github(monkeypatch, script: list) -> list[dict]:
    """Подменяет обращения к API. script - что вернуть/бросить по очереди."""
    sent: list[dict] = []

    def fake(url, token, method="GET", body=None):
        sent.append({"url": url, "method": method, "body": body})
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _Response(step)

    monkeypatch.setattr(notes, "_api", fake)
    return sent


def _file(content: str, sha: str = "sha1") -> dict:
    return {"content": base64.b64encode(content.encode("utf-8")).decode(), "sha": sha}


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com", code, "", None, None)


def test_в_облаке_пишет_с_sha_прочитанного_файла(monkeypatch, tmp_path: Path) -> None:
    sent = _github(monkeypatch, [_file("date,note,updated_utc\n"), {}])
    assert notes.save(_storage(tmp_path, token="тк"), DAY, "Ливень") == "GitHub"

    put = sent[1]
    assert put["method"] == "PUT" and put["body"]["sha"] == "sha1"
    written = base64.b64decode(put["body"]["content"]).decode("utf-8")
    assert notes.parse(written) == {DAY: "Ливень"}


def test_первая_заметка_пишется_без_sha(monkeypatch, tmp_path: Path) -> None:
    # Файла ещё нет - GitHub отвечает 404, и это не ошибка.
    sent = _github(monkeypatch, [_http_error(404), {}])
    notes.save(_storage(tmp_path, token="тк"), DAY, "Первая")
    assert "sha" not in sent[1]["body"]


def test_конфликт_версий_перечитывает_и_повторяет(monkeypatch, tmp_path: Path) -> None:
    # Между чтением и записью файл изменил другой сеанс. Слепой повтор затёр
    # бы его правку - поэтому перед вторым заходом перечитываем свежий файл.
    other = "date,note,updated_utc\n2026-09-04,Правка соседа,\n"
    sent = _github(
        monkeypatch,
        [_file("date,note,updated_utc\n", "старая"), _http_error(409), _file(other, "новая"), {}],
    )
    assert notes.save(_storage(tmp_path, token="тк"), DAY, "Ливень") == "GitHub"

    assert sent[3]["body"]["sha"] == "новая"
    written = notes.parse(base64.b64decode(sent[3]["body"]["content"]).decode("utf-8"))
    assert written == {DAY: "Ливень", date(2026, 9, 4): "Правка соседа"}


def test_отказ_доступа_не_маскируется_под_гонку(monkeypatch, tmp_path: Path) -> None:
    _github(monkeypatch, [_file("date,note,updated_utc\n"), _http_error(403)])
    with pytest.raises(RuntimeError, match="403"):
        notes.save(_storage(tmp_path, token="тк"), DAY, "Ливень")


def test_вторая_подряд_гонка_сдаётся_с_понятной_ошибкой(monkeypatch, tmp_path: Path) -> None:
    _github(
        monkeypatch,
        [
            _file("date,note,updated_utc\n"),
            _http_error(409),
            _file("date,note,updated_utc\n"),
            _http_error(409),
        ],
    )
    with pytest.raises(RuntimeError):
        notes.save(_storage(tmp_path, token="тк"), DAY, "Ливень")
