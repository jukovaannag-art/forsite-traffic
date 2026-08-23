"""Снимает балл пробок по Иркутску с Яндекса и 2ГИС и дописывает в CSV.

Запускается каждые 20 минут в окне 7:00-23:00 по Иркутску (GitHub Actions, см.
.github/workflows/collect.yml). Локально: python collect.py

Записывает всегда, в любой час. Окно задаёт расписание, а не сам скрипт:
опоздавший запуск раньше отбрасывался целиком, хотя источники ему ответили.
Отсечка 7:00-23:00 живёт на дашборде, там же видны сползшие замеры.

Балл - целое 0-10 у обоих источников, но методики разные, поэтому значения
не смешиваются: каждая строка помечена источником, сравнение только рядом.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector.sources import PROVIDERS
from collector.storage import upsert

# Иркутск - UTC+8 круглый год, перевода часов нет.
IRKUTSK_TZ = timezone(timedelta(hours=8), name="Asia/Irkutsk")


DATA_PATH = Path(__file__).resolve().parent / "data" / "traffic_irkutsk.csv"


def collect(now_local: datetime) -> list[dict]:
    """Опрашивает все источники и превращает ответы в строки CSV."""
    now_utc = now_local.astimezone(timezone.utc)
    rows = []
    for name, fetch in PROVIDERS.items():
        reading = fetch()
        if reading.error:
            print(f"[{name}] ошибка: {reading.error}", file=sys.stderr)
        else:
            print(f"[{name}] балл {reading.score:g} {reading.hint}".strip())
        rows.append(
            {
                "ts_utc": now_utc.replace(microsecond=0).isoformat(),
                "ts_local": now_local.replace(microsecond=0, tzinfo=None).isoformat(),
                "date": now_local.strftime("%Y-%m-%d"),
                "hour": now_local.hour,
                "source": name,
                "score": "" if reading.score is None else f"{reading.score:g}",
                "hint": reading.hint,
                "jams_length": reading.extra.get("jams_length", ""),
                "error": reading.error,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DATA_PATH, help="путь к CSV с историей"
    )
    args = parser.parse_args()

    now_local = datetime.now(IRKUTSK_TZ)
    rows = collect(now_local)
    added, replaced = upsert(args.out, rows)
    print(
        f"{now_local:%Y-%m-%d %H:%M} Иркутск: добавлено {added}, "
        f"заменено {replaced} -> {args.out}"
    )

    # Провал считаем провалом только если молчат оба источника: одиночный
    # сбой не должен красить весь прогон в красное.
    if all(not row["score"] for row in rows):
        print("Ни один источник не ответил.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
