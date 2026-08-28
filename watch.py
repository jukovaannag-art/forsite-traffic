"""Сторож сбора: проверяет данные и пишет письмо о статусе.

Два режима:
  python watch.py --mode alert   # молчит, если всё хорошо (в течение дня)
  python watch.py --mode daily   # пишет всегда, это вечерняя сводка

Локальная проверка без отправки:
  python watch.py --mode daily --dry-run

Почта берётся из окружения (в GitHub Actions - из секретов репозитория):
  MAIL_USER      - ящик-отправитель целиком, вместе с доменом
  MAIL_PASSWORD  - пароль приложения почты (не обычный пароль от ящика)
  MAIL_TO        - кому слать; если не задан, письмо уйдёт самому себе
  MAIL_HOST      - SMTP-сервер, по умолчанию Яндекс; для Gmail - smtp.gmail.com
  MAIL_PORT      - порт SMTP поверх SSL, по умолчанию 465

У Яндекса пароль приложения выдаётся в Яндекс ID -> Безопасность -> Пароли
приложений -> Почта. Там же в настройках почты должен быть разрешён доступ
по протоколу SMTP - без этого логин отвергается с 535.

Пароль читается только в момент отправки и никуда не печатается: письмо с
диагнозом уходит по SMTP, а в лог прогона попадает лишь тема.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from collector.health import Health, inspect
from collector.storage import read_rows

IRKUTSK_TZ = timezone(timedelta(hours=8), name="Asia/Irkutsk")
DATA_PATH = Path(__file__).resolve().parent / "data" / "traffic_irkutsk.csv"

# Яндекс по умолчанию: пароль приложения там выдают без обязательной
# двухэтапной аутентификации, у Google без неё страница паролей не открывается.
DEFAULT_SMTP_HOST = "smtp.yandex.ru"
DEFAULT_SMTP_PORT = 465

DASHBOARD_URL = "https://forsite-traffic.streamlit.app"
ACTIONS_URL = "https://github.com/jukovaannag-art/forsite-traffic/actions"


def compose(health: Health, now: datetime) -> tuple[str, str]:
    """Тема и текст письма. Тема - чтобы всё было понятно из списка входящих."""
    if health.ok:
        subject = f"Пробки Иркутска: сбор работает ({now:%d.%m})"
        opening = "Сбор идёт штатно."
    else:
        subject = f"Пробки Иркутска: проблема со сбором ({now:%d.%m})"
        opening = "Похоже, со сбором что-то не так:"

    lines = [opening, ""]
    for problem in health.problems:
        lines.append(f"  - {problem}")
    if health.problems:
        lines.append("")
    lines.append("Как обстоят дела:")
    for note in health.notes:
        lines.append(f"  - {note}")
    lines += [
        "",
        f"Дашборд: {DASHBOARD_URL}",
        f"Журнал запусков: {ACTIONS_URL}",
        "",
        f"Проверено {now:%d.%m.%Y %H:%M} по Иркутску.",
    ]
    return subject, "\n".join(lines)


def smtp_settings() -> tuple[str, int]:
    """Адрес и порт SMTP из окружения. Пустая переменная - как незаданная:
    незаполненный секрет в Actions приходит пустой строкой, а не отсутствует."""
    host = os.environ.get("MAIL_HOST", "").strip() or DEFAULT_SMTP_HOST
    raw_port = os.environ.get("MAIL_PORT", "").strip()
    if not raw_port:
        return host, DEFAULT_SMTP_PORT
    if not raw_port.isdigit():
        raise SystemExit(f"MAIL_PORT должен быть числом, а пришло: {raw_port!r}")
    return host, int(raw_port)


def send(subject: str, body: str) -> None:
    """Отправляет письмо по SMTP поверх SSL. Нет доступов - падаем громко."""
    user = os.environ.get("MAIL_USER", "").strip()
    password = os.environ.get("MAIL_PASSWORD", "").strip()
    recipient = os.environ.get("MAIL_TO", "").strip() or user
    if not user or not password:
        raise SystemExit(
            "Нет MAIL_USER или MAIL_PASSWORD - письмо отправить нечем. "
            "В GitHub Actions это секреты репозитория."
        )
    host, port = smtp_settings()

    message = EmailMessage()
    message["From"] = user
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("alert", "daily"),
        default="alert",
        help="alert - письмо только при проблеме; daily - письмо всегда",
    )
    parser.add_argument("--out", type=Path, default=DATA_PATH, help="путь к CSV")
    parser.add_argument(
        "--dry-run", action="store_true", help="показать письмо, но не отправлять"
    )
    args = parser.parse_args()

    now = datetime.now(IRKUTSK_TZ).replace(tzinfo=None)
    health = inspect(read_rows(args.out), now)
    subject, body = compose(health, now)

    print(subject)
    for problem in health.problems:
        print(f"  ! {problem}", file=sys.stderr)

    if args.dry_run:
        print()
        print(body)
        return 0

    if health.ok and args.mode == "alert":
        print("Проблем нет, письмо не нужно.")
        return 0

    send(subject, body)
    print("Письмо отправлено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
