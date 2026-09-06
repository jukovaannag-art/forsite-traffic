"""Ручные заметки к дню: то, чего не знает ни один источник.

Погоду машина берёт сама, а перекрытие моста, ремонт, праздник или крупное ДТП
знает только человек. Такая заметка - вторая половина контекста к баллу пробок.

Хранение - `data/day_notes.csv` в том же репозитории, что и сам ряд. Файловая
система Streamlit Cloud эфемерна: запись «в файл рядом» пережила бы только до
перезапуска приложения, поэтому в облаке пишем через GitHub Contents API.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

TIMEOUT_SEC = 20
FIELDNAMES = ["date", "note", "updated_utc"]

REPO = os.environ.get("NOTES_REPO", "jukovaannag-art/forsite-traffic")
BRANCH = os.environ.get("NOTES_BRANCH", "main")
REPO_PATH = "data/day_notes.csv"

# Заметка отвечает на «почему такой день», а не «что случилось поимённо».
# Файл лежит в публичном репозитории - персональным данным там не место.
MAX_NOTE_LEN = 500


@dataclass
class Storage:
    """Где живут заметки для текущего запуска.

    `token` пуст - работаем с локальным файлом: так дашборд поднимается на
    машине без секретов и остаётся редактируемым, просто без облака.
    """

    path: Path
    token: str = ""

    @property
    def writes_to_github(self) -> bool:
        return bool(self.token)


def _serialize(dated: dict[date, tuple[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    for day in sorted(dated):
        note, stamp = dated[day]
        writer.writerow({"date": day.isoformat(), "note": note, "updated_utc": stamp})
    return buffer.getvalue()


def _parse_dated(text: str) -> dict[date, tuple[str, str]]:
    """Заметки вместе с их временем правки. Битые и пустые строки пропускаются.

    Время хранится по каждой заметке отдельно: файл пишется целиком, и общая
    отметка «сейчас» на всех строках стирала бы историю правок соседних дней.
    """
    dated: dict[date, tuple[str, str]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            day = date.fromisoformat((row.get("date") or "").strip())
        except ValueError:
            continue
        note = (row.get("note") or "").strip()
        if note:
            dated[day] = (note, (row.get("updated_utc") or "").strip())
    return dated


def parse(text: str) -> dict[date, str]:
    """Заметки для показа: дата - текст."""
    return {day: note for day, (note, _) in _parse_dated(text).items()}


def _api(url: str, token: str, method: str = "GET", body: dict | None = None):
    request = urllib.request.Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "forsite-traffic/1.0",
        },
    )
    return urllib.request.urlopen(request, timeout=TIMEOUT_SEC)


def _read_from_github(token: str) -> tuple[dict[date, tuple[str, str]], str | None]:
    """Читает файл через API и возвращает заметки вместе с его `sha`.

    `sha` нужен для записи: без него GitHub не даст перезаписать существующий
    файл. Файла ещё нет (404) - это не ошибка, а первая заметка.
    """
    url = (
        f"https://api.github.com/repos/{REPO}/contents/"
        f"{urllib.parse.quote(REPO_PATH)}?ref={BRANCH}&t={int(time.time())}"
    )
    try:
        with _api(url, token) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}, None
        raise
    text = base64.b64decode(payload.get("content", "")).decode("utf-8")
    return _parse_dated(text), payload.get("sha")


def load(storage: Storage, url: str = "") -> dict[date, str]:
    """Заметки для показа: сначала сеть, при обрыве - копия рядом с дашбордом.

    Тот же порядок, что и у самого ряда замеров: копия в развёрнутом приложении
    обновляется только при перезапуске и отстаёт.
    """
    if url:
        try:
            request = urllib.request.Request(
                f"{url}{'&' if '?' in url else '?'}t={int(time.time())}",
                headers={"User-Agent": "forsite-traffic/1.0"},
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                return parse(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError):
            pass
    if not storage.path.exists():
        return {}
    return parse(storage.path.read_text(encoding="utf-8"))


def save(storage: Storage, day: date, note: str) -> str:
    """Сохраняет (или стирает пустой строкой) заметку дня. Возвращает, куда легло.

    В облаке пишем весь файл целиком через Contents API. Между чтением и
    записью файл мог изменить другой сеанс - тогда GitHub отвечает 409/422, и
    мы перечитываем свежую версию и накладываем правку заново. Один повтор:
    вторая подряд гонка на дашборде, которым пользуется один человек, означает
    не гонку, а сломанный доступ.
    """
    note = note.strip()[:MAX_NOTE_LEN]

    if not storage.writes_to_github:
        text = (
            storage.path.read_text(encoding="utf-8") if storage.path.exists() else ""
        )
        notes = _parse_dated(text)
        _apply(notes, day, note)
        storage.path.parent.mkdir(parents=True, exist_ok=True)
        storage.path.write_text(_serialize(notes), encoding="utf-8")
        return f"локальный файл {storage.path}"

    for attempt in range(2):
        notes, sha = _read_from_github(storage.token)
        _apply(notes, day, note)
        body = {
            "message": f"Заметка к дню {day:%d.%m.%Y}",
            "content": base64.b64encode(_serialize(notes).encode("utf-8")).decode(),
            "branch": BRANCH,
        }
        if sha:
            body["sha"] = sha
        try:
            with _api(
                f"https://api.github.com/repos/{REPO}/contents/"
                f"{urllib.parse.quote(REPO_PATH)}",
                storage.token,
                method="PUT",
                body=body,
            ):
                return "GitHub"
        except urllib.error.HTTPError as error:
            if error.code in (409, 422) and attempt == 0:
                continue
            # Текст ответа GitHub наружу не выносим: в нём бывает эхо запроса.
            raise RuntimeError(f"GitHub ответил {error.code}") from None
    raise RuntimeError("GitHub: не удалось записать, файл меняли параллельно")


def _apply(notes: dict[date, tuple[str, str]], day: date, note: str) -> None:
    """Меняет один день, не трогая время правки остальных."""
    if note:
        notes[day] = (note, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    else:
        notes.pop(day, None)
