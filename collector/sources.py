"""Источники балла пробок по Иркутску.

Каждый провайдер возвращает Reading. Если источник недоступен - score=None
и заполненный error, чтобы пропуск был виден в данных, а не молча потерялся.

Ни один из источников не требует ключа: оба эндпоинта публичные и отдают
данные обычному HTTP-клиенту.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

TIMEOUT_SEC = 20

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Иркутск в справочниках источников
YANDEX_REGION_ID = 63  # geoId Иркутска в Яндекс.Картах
DGIS_PROJECT = "irkutsk"  # имя проекта в ответе jam.api.2gis.com
# bbox Иркутска: lon_min,lat_max,lon_max,lat_min (порядок, который ждёт 2ГИС)
DGIS_VIEW = "103.929159524171,52.465392368426734,104.632284475829,52.11018763157327"
DGIS_ZOOM = 11


@dataclass
class Reading:
    """Одно измерение балла пробок от одного источника."""

    source: str
    score: float | None = None
    hint: str = ""
    extra: dict = field(default_factory=dict)
    error: str = ""


def _get(url: str, referer: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Referer": referer,
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return response.read()


def fetch_yandex() -> Reading:
    """Балл Яндекс.Пробок из публичного XML-экспорта.

    Отдаёт level (0-10), текстовую подсказку и суммарную длину пробок.
    """
    url = (
        "https://export.yandex.ru/bar/reginfo.xml"
        f"?region={YANDEX_REGION_ID}&lang=ru"
    )
    try:
        raw = _get(url, referer="https://yandex.ru/maps/")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Reading(source="yandex", error=f"network: {exc}")

    try:
        root = ET.fromstring(raw.decode("utf-8"))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        return Reading(source="yandex", error=f"parse: {exc}")

    region = root.find(".//traffic/region")
    if region is None:
        return Reading(source="yandex", error="parse: нет блока traffic/region")

    level_node = region.find("level")
    if level_node is None or not (level_node.text or "").strip():
        return Reading(source="yandex", error="parse: нет level")

    try:
        score = float(level_node.text.strip())
    except ValueError as exc:
        return Reading(source="yandex", error=f"parse: level не число ({exc})")

    hint = ""
    for node in region.findall("hint"):
        if node.get("lang") == "ru":
            hint = (node.text or "").strip()
            break

    extra: dict = {}
    length_node = region.find("length")
    if length_node is not None and (length_node.text or "").strip():
        try:
            extra["jams_length"] = float(length_node.text.strip())
        except ValueError:
            pass
    icon_node = region.find("icon")
    if icon_node is not None:
        extra["icon"] = (icon_node.text or "").strip()

    return Reading(source="yandex", score=score, hint=hint, extra=extra)


def fetch_dgis() -> Reading:
    """Балл пробок 2ГИС из того же эндпоинта, что использует 2gis.ru.

    Ответ вида {"projects": [{"name": "irkutsk", "score": 3, ...}]}.
    Проверяем name, чтобы не записать балл чужого города, если 2ГИС
    поменяет разбиение проектов.
    """
    url = f"https://jam.api.2gis.com/scores?view={DGIS_VIEW}&z={DGIS_ZOOM}"
    try:
        raw = _get(url, referer="https://2gis.ru/irkutsk?traffic")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Reading(source="2gis", error=f"network: {exc}")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return Reading(source="2gis", error=f"parse: {exc}")

    projects = payload.get("projects") or []
    project = next(
        (item for item in projects if item.get("name") == DGIS_PROJECT), None
    )
    if project is None:
        names = ", ".join(str(item.get("name")) for item in projects) or "пусто"
        return Reading(
            source="2gis", error=f"parse: нет проекта {DGIS_PROJECT} (пришло: {names})"
        )

    if project.get("score") is None:
        return Reading(source="2gis", error="parse: нет score")

    try:
        score = float(project["score"])
    except (TypeError, ValueError) as exc:
        return Reading(source="2gis", error=f"parse: score не число ({exc})")

    return Reading(source="2gis", score=score, extra={"project": DGIS_PROJECT})


PROVIDERS = {
    "yandex": fetch_yandex,
    "2gis": fetch_dgis,
}
