"""Проверка здоровья сбора: молчит ли сборщик и отвечают ли источники.

Отдельно от `watch.py` (который шлёт письмо), потому что тут чистые функции над
строками CSV - их можно прогонять в тестах, не поднимая почту.

Все времена - иркутские (UTC+8 без перевода часов), как и в самих данных.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

HOUR_FROM, HOUR_TO = 7, 23

# Сколько часов внутри окна сбор может молчать, прежде чем это тревога.
# Два часа, а не один: цикл опрашивает раз в 10 минут, но между прогонами
# бывает законная пауза, пока очередь передаёт эстафету.
MAX_SILENCE_HOURS = 2

# Сколько неудачных часов у источника за сутки считаем сбоем. Один-два - это
# сетевая рябь, она чинится следующим опросом; больше - меняется что-то на
# стороне источника.
SOURCE_FAILURES_ALERT = 3


@dataclass
class Health:
    """Итог проверки: тревоги отдельно, цифры для сводки отдельно."""

    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def hours_word(count: int) -> str:
    """«1 час», «3 часа», «5 часов» - иначе письмо читается как машинный лог."""
    tail_two, tail_one = count % 100, count % 10
    if 11 <= tail_two <= 14:
        return "часов"
    if tail_one == 1:
        return "час"
    if 2 <= tail_one <= 4:
        return "часа"
    return "часов"


def _parse_local(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def last_measurement(rows: list[dict]) -> datetime | None:
    """Момент последнего удачного замера. Строки с ошибкой не считаются."""
    stamps = [
        _parse_local(row.get("ts_local", ""))
        for row in rows
        if str(row.get("score", "")).strip()
    ]
    stamps = [stamp for stamp in stamps if stamp is not None]
    return max(stamps) if stamps else None


def check_silence(rows: list[dict], now: datetime) -> str | None:
    """Тревога, если внутри окна сбора давно нет новых строк.

    Вне окна молчание законно, поэтому ночью проверка ничего не говорит - иначе
    каждое утро приходило бы письмо про восьмичасовую паузу.
    """
    if not (HOUR_FROM <= now.hour <= HOUR_TO):
        return None
    last = last_measurement(rows)
    if last is None:
        return "в файле нет ни одного удачного замера"
    silence = now - last
    if silence <= timedelta(hours=MAX_SILENCE_HOURS):
        return None
    hours = silence.total_seconds() / 3600
    return (
        f"сбор молчит {hours:.1f} ч - последний замер {last:%d.%m %H:%M}, "
        "сейчас рабочее время"
    )


def failures_by_source(rows: list[dict], now: datetime) -> dict[str, int]:
    """Сколько часов за последние сутки источник так и не закрыл."""
    since = now - timedelta(hours=24)
    counts: dict[str, int] = {}
    for row in rows:
        stamp = _parse_local(row.get("ts_local", ""))
        if stamp is None or stamp < since:
            continue
        if str(row.get("score", "")).strip():
            continue
        source = str(row.get("source", "")) or "?"
        counts[source] = counts.get(source, 0) + 1
    return counts


def check_sources(rows: list[dict], now: datetime) -> list[str]:
    """Тревоги по источникам: кто и сколько раз за сутки не ответил."""
    problems = []
    for source, count in sorted(failures_by_source(rows, now).items()):
        if count >= SOURCE_FAILURES_ALERT:
            reason = _last_error(rows, source)
            tail = f" ({reason})" if reason else ""
            problems.append(
                f"источник {source}: {count} неудачных {hours_word(count)} за сутки{tail}"
            )
    return problems


def _last_error(rows: list[dict], source: str) -> str:
    """Текст последней ошибки источника - чтобы в письме был диагноз, а не факт."""
    errors = [
        str(row.get("error", "")).strip()
        for row in rows
        if row.get("source") == source and str(row.get("error", "")).strip()
    ]
    return errors[-1][:120] if errors else ""


def closed_hours(rows: list[dict], day: str) -> int:
    """Сколько часов окна закрыто хотя бы одним источником за указанный день."""
    hours = {
        str(row.get("hour", ""))
        for row in rows
        if row.get("date") == day and str(row.get("score", "")).strip()
        and str(row.get("hour", "")).isdigit()
        and HOUR_FROM <= int(row["hour"]) <= HOUR_TO
    }
    return len(hours)


def inspect(rows: list[dict], now: datetime) -> Health:
    """Полная проверка: тревоги + цифры для ежедневной сводки."""
    health = Health()

    silence = check_silence(rows, now)
    if silence:
        health.problems.append(silence)
    health.problems.extend(check_sources(rows, now))

    today = now.strftime("%Y-%m-%d")
    total = HOUR_TO - HOUR_FROM + 1
    closed = closed_hours(rows, today)
    health.notes.append(f"за сегодня закрыто {closed} {hours_word(closed)} из {total}")

    last = last_measurement(rows)
    health.notes.append(
        f"последний замер: {last:%d.%m %H:%M}" if last else "замеров нет вовсе"
    )

    failures = failures_by_source(rows, now)
    if failures:
        listing = ", ".join(f"{name} - {count}" for name, count in sorted(failures.items()))
        health.notes.append(f"неудачных часов за сутки: {listing}")
    else:
        health.notes.append("источники отвечали без сбоев")
    return health
