"""Снимает балл пробок по Иркутску с Яндекса и 2ГИС и дописывает в CSV.

Запускается раз в час с 7:00 до 23:00 по Иркутску (GitHub Actions, см.
.github/workflows/collect.yml). Локально: python collect.py --force

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

HOUR_FROM = 7
HOUR_TO = 23  # включительно

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
        "--force",
        action="store_true",
        help=f"записать, даже если сейчас не {HOUR_FROM}:00-{HOUR_TO}:59 по Иркутску",
    )
    parser.add_argument(
        "--out", type=Path, default=DATA_PATH, help="путь к CSV с историей"
    )
    args = parser.parse_args()

    now_local = datetime.now(IRKUTSK_TZ)
    if not args.force and not (HOUR_FROM <= now_local.hour <= HOUR_TO):
        print(
            f"Сейчас {now_local:%H:%M} по Иркутску - вне окна "
            f"{HOUR_FROM}:00-{HOUR_TO}:59, ничего не пишу."
        )
        return 0

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
