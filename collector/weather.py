"""Фактическая погода Иркутска за прошедшие дни - контекст к баллу пробок.

Источник - Open-Meteo: без ключа, без регистрации, отдаёт факт за уже
прошедшие сутки (а не прогноз, сохранённый задним числом).

Погода не хранится в репозитории: она однозначно определяется датой и тянется
одним запросом на весь показанный период. Своя копия означала бы второй ряд,
способный разойтись с первоисточником, и второе место, где сбор ломается.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

TIMEOUT_SEC = 20

# Иркутск, центр города. Балл пробок считается по городу целиком, поэтому одной
# точки достаточно - разброс погоды внутри города меньше, чем шаг наших выводов.
LAT, LON = 52.2871, 104.3050
TIMEZONE = "Asia/Irkutsk"  # сутки погоды должны совпасть с сутками замеров

# Свежие даты отдаёт обычный прогнозный эндпоинт (у него есть и прошлое),
# глубже - архив ERA5. Граница у Open-Meteo: 92 дня назад.
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_DEPTH_DAYS = 92

DAILY_FIELDS = [
    "weather_code",
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
]

# Коды WMO. Open-Meteo отдаёт только число, словаря в ответе нет.
# Соседние оттенки одного явления схлопнуты: для чтения графика пробок разница
# между «слабым» и «умеренным» дождём ничего не решает, а строка становится
# длиннее подсказки.
WMO_RU = {
    0: "ясно",
    1: "малооблачно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "морось",
    53: "морось",
    55: "сильная морось",
    56: "ледяная морось",
    57: "ледяная морось",
    61: "слабый дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "ледяной дождь",
    71: "слабый снег",
    73: "снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "ливень",
    81: "ливень",
    82: "сильный ливень",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "гроза с градом",
}


@dataclass
class DayWeather:
    """Погода одних суток. Любое поле может быть None - источник не всеведущ."""

    day: date
    code: int | None = None
    t_min: float | None = None
    t_max: float | None = None
    precipitation: float | None = None  # мм
    snowfall: float | None = None  # см
    wind_max: float | None = None  # км/ч

    @property
    def description(self) -> str:
        return WMO_RU.get(self.code, "") if self.code is not None else ""

    def summary(self) -> str:
        """Одна строка для подсказки и таблицы: «дождь, 7...11°, 32 мм, ветер 24 км/ч».

        Осадки и ветер попадают в строку только когда их стоит заметить: нули и
        штиль в каждой строке превращают колонку в шум.
        """
        parts: list[str] = []
        if self.description:
            parts.append(self.description)
        if self.t_min is not None and self.t_max is not None:
            parts.append(f"{self.t_min:.0f}...{self.t_max:.0f}°")
        elif self.t_max is not None:
            parts.append(f"{self.t_max:.0f}°")
        if self.snowfall:
            parts.append(f"снег {self.snowfall:.1f} см")
        elif self.precipitation:
            parts.append(f"{self.precipitation:.1f} мм")
        if self.wind_max is not None and self.wind_max >= 20:
            parts.append(f"ветер {self.wind_max:.0f} км/ч")
        return ", ".join(parts)


def _get_json(url: str, params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={"Accept": "application/json", "User-Agent": "forsite-traffic/1.0"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse(payload: dict) -> dict[date, DayWeather]:
    """Разбирает ответ Open-Meteo: параллельные массивы, выровненные по `time`.

    Дни, где не разобралась дата, пропускаются целиком: строка погоды без даты
    ни к чему не привязывается. Дырявое поле внутри дня остаётся None.
    """
    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict):
        # Ответ не той формы (ошибка источника, страница-заглушка) - погоды
        # просто нет. Ронять из-за неё дашборд нельзя.
        return {}
    days = daily.get("time") or []
    result: dict[date, DayWeather] = {}
    for position, raw_day in enumerate(days):
        try:
            day = date.fromisoformat(str(raw_day))
        except ValueError:
            continue

        def value(field: str, index: int = position) -> float | None:
            column = daily.get(field) or []
            if index >= len(column):
                return None
            item = column[index]
            try:
                return None if item is None else float(item)
            except (TypeError, ValueError):
                return None

        code = value("weather_code")
        result[day] = DayWeather(
            day=day,
            code=None if code is None else int(code),
            t_min=value("temperature_2m_min"),
            t_max=value("temperature_2m_max"),
            precipitation=value("precipitation_sum"),
            snowfall=value("snowfall_sum"),
            wind_max=value("wind_speed_10m_max"),
        )
    return result


def _fetch_range(url: str, start: date, end: date) -> dict[date, DayWeather]:
    payload = _get_json(
        url,
        {
            "latitude": LAT,
            "longitude": LON,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_FIELDS),
            "timezone": TIMEZONE,
        },
    )
    return _parse(payload)


def fetch_daily(start: date, end: date, today: date | None = None) -> dict[date, DayWeather]:
    """Погода по дням за период включительно. Ключ - дата, значение - DayWeather.

    Период режется по границе 92 дней: свежую часть отдаёт прогнозный эндпоинт,
    давнюю - архив ERA5. Архив отстаёт на несколько суток, поэтому свежие даты
    берутся не из него, хотя формально он их диапазон покрывает.

    Сеть недоступна или источник ответил мусором - возвращается то, что успели
    собрать (возможно, пустой словарь). Погода поясняет ряд, но не является им:
    её отсутствие не должно ронять дашборд.
    """
    if start > end:
        start, end = end, start
    today = today or date.today()
    border = today - timedelta(days=FORECAST_DEPTH_DAYS)

    windows: list[tuple[str, date, date]] = []
    if start < border:
        windows.append((ARCHIVE_URL, start, min(end, border - timedelta(days=1))))
    if end >= border:
        windows.append((FORECAST_URL, max(start, border), end))

    result: dict[date, DayWeather] = {}
    for url, window_start, window_end in windows:
        if window_start > window_end:
            continue
        try:
            result.update(_fetch_range(url, window_start, window_end))
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):
            continue
    return result
