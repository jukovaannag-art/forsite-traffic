"""Хранение измерений в CSV.

Один файл на весь ряд: строк мало (2 источника x 17 часов x 365 дней ~ 12 тыс.
в год), зато вся история читается одним pandas.read_csv и одинаково хорошо
живёт в git - каждый час добавляется пара строк, diff читаемый.
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

FIELDNAMES = [
    "ts_utc",  # ISO-8601, момент запроса
    "ts_local",  # то же время в Иркутске (UTC+8)
    "date",  # локальная дата, YYYY-MM-DD
    "hour",  # локальный час, 0-23
    "source",  # yandex | 2gis
    "score",  # балл 0-10, пусто если источник не ответил
    "hint",  # текстовая расшифровка (есть только у Яндекса)
    "jams_length",  # суммарная длина пробок, только Яндекс
    "error",  # причина пропуска, пусто если всё хорошо
]

KEY = ("date", "hour", "source")


def read_rows(path: Path) -> list[dict]:
    """Читает CSV. Нет файла или он пустой - вернёт пустой список."""
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _key(row: dict) -> tuple:
    return tuple(str(row.get(name, "")) for name in KEY)


def upsert(path: Path, new_rows: list[dict]) -> tuple[int, int]:
    """Добавляет строки, схлопывая дубли по (дата, час, источник).

    Если за этот час уже есть удачное измерение, повторный запуск его не
    перетирает: первое значение внутри часа ближе к началу часа. Строку с
    ошибкой заменяем на удачную - так восстановленный ретрай чинит дырку.

    Возвращает (добавлено, заменено).
    """
    existing = read_rows(path)
    index = {_key(row): position for position, row in enumerate(existing)}

    added = replaced = 0
    for row in new_rows:
        position = index.get(_key(row))
        if position is None:
            existing.append(row)
            index[_key(row)] = len(existing) - 1
            added += 1
            continue
        old = existing[position]
        old_is_bad = not str(old.get("score", "")).strip()
        new_is_good = str(row.get("score", "")).strip() != ""
        if old_is_bad and new_is_good:
            existing[position] = row
            replaced += 1

    existing.sort(key=lambda row: (row.get("date", ""), int(row.get("hour") or 0), row.get("source", "")))
    _write_atomic(path, existing)
    return added, replaced


def _write_atomic(path: Path, rows: list[dict]) -> None:
    """Пишет через временный файл: обрыв на середине не испортит историю."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", delete=False, dir=str(path.parent)
    )
    try:
        with handle:
            # lineterminator фиксирован: иначе Windows пишет \r\n, а раннер
            # GitHub Actions \n, и каждый прогон переписывал бы весь файл.
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in FIELDNAMES})
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
