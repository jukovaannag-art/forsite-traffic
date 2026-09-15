"""Дашборд по баллам пробок Иркутска: Яндекс и 2ГИС.

Запуск: streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
# streamlit run кладёт в sys.path папку скрипта, а не корень репозитория -
# без этой строки не находятся ни collector, ни соседний модуль заметок.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector import weather as weather_source  # noqa: E402
from dashboard import notes as notes_store  # noqa: E402
# TRAFFIC_CSV позволяет открыть дашборд на другом файле (демо, проверка).
DATA_PATH = Path(os.environ.get("TRAFFIC_CSV") or ROOT / "data" / "traffic_irkutsk.csv")

# Данные берутся прямо с GitHub, а не из копии репозитория рядом с дашбордом:
# Streamlit Cloud обновляет свою копию только при перезапуске приложения, и
# свежие замеры появлялись на дашборде с опозданием в часы. Сеть недоступна -
# откатываемся на локальный файл, чтобы дашборд работал и без интернета.
#
# Указан TRAFFIC_CSV - значит дашборд открыли на конкретном файле (демо,
# проверка, тесты), и лезть за настоящими данными в сеть нельзя: показали бы
# не то, что просили, да ещё и с непредсказуемой задержкой.
DATA_URL = (
    ""
    if os.environ.get("TRAFFIC_CSV")
    else os.environ.get(
        "TRAFFIC_CSV_URL",
        "https://raw.githubusercontent.com/jukovaannag-art/forsite-traffic/main/data/traffic_irkutsk.csv",
    )
)

NOTES_PATH = ROOT / "data" / "day_notes.csv"
NOTES_URL = (
    ""
    if os.environ.get("TRAFFIC_CSV")
    else os.environ.get(
        "DAY_NOTES_URL",
        "https://raw.githubusercontent.com/jukovaannag-art/forsite-traffic/main/data/day_notes.csv",
    )
)

HOUR_FROM, HOUR_TO = 7, 23
# Сколько замеров даёт полностью собранный день.
FULL_DAY_HOURS = HOUR_TO - HOUR_FROM + 1

# Сутки замеров идут по Иркутску, а дашборд крутится на сервере в UTC. Без
# этого пояса «сегодня» в облаке с 16:00 до полуночи по Иркутску - уже завтра.
# Смещение фиксированное: перевода часов в России нет.
IRKUTSK_TZ = timezone(timedelta(hours=8), name="Asia/Irkutsk")

WEEK, TWO_WEEKS, MONTH = "Неделя", "2 недели", "Месяц"
# Готовые периоды - календарные и закрытые: неделя понедельник-воскресенье,
# месяц с первого по последнее число. Считается последний закрытый период:
# текущая неделя ещё идёт, и её три дня против семи прошлых - не сравнение.
CALENDAR_PERIODS = (WEEK, TWO_WEEKS, MONTH)
ALL_TIME = "Всё время"
CUSTOM = "Свой период"

# Цвета источников закреплены за источником, а не за порядком в фильтре.
# Пара проверена на различимость при дальтонизме (ΔE 32 protan).
SOURCE_COLORS = {"yandex": "#2563eb", "2gis": "#d97706"}
SOURCE_TITLES = {"yandex": "Яндекс.Пробки", "2gis": "2ГИС"}

GRID = "#e8e6e1"
INK_MUTED = "#6b6b66"

st.set_page_config(page_title="Пробки Иркутска - Форсайт", page_icon="🚦", layout="wide")


def _read_source(path: Path, url: str) -> tuple[pd.DataFrame, str]:
    """Сначала GitHub, потом локальный файл.

    Возвращает и происхождение данных: откат на локальную копию должен быть
    виден. Сеть до GitHub рвётся, копия рядом с дашбордом отстаёт на сутки, и
    молчаливая подмена выглядит как «сбор встал», хотя он идёт.
    """
    if url:
        for _ in range(3):
            try:
                # Метка времени обходит кэш CDN: без неё raw отдаёт версию до
                # пяти минут давности. На ключ кэша Streamlit она не влияет -
                # считается внутри функции, а не в аргументах.
                fresh = f"{url}{'&' if '?' in url else '?'}t={int(time.time())}"
                return pd.read_csv(fresh, dtype={"source": str}), "github"
            except Exception:  # noqa: BLE001 - сеть, прокси, 404: причина не меняет действий
                time.sleep(1)
    if not path.exists():
        return pd.DataFrame(), "пусто"
    return pd.read_csv(path, dtype={"source": str}), "локальная копия"


@st.cache_data(ttl=120)
def load_data(path: Path, url: str = DATA_URL) -> tuple[pd.DataFrame, str]:
    """Читает историю измерений. Строки без балла (сбой источника) отбрасываем."""
    frame, origin = _read_source(path, url)
    if frame.empty:
        return frame, origin
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["hour"] = pd.to_numeric(frame["hour"], errors="coerce")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["date", "hour", "score"])
    frame["hour"] = frame["hour"].astype(int)
    return frame, origin


@st.cache_data(ttl=3600, show_spinner=False)
def load_weather(start: date, end: date) -> dict[date, str]:
    """Строка погоды на каждый день периода. Сеть недоступна - пустой словарь.

    Час кэша: погода прошедших суток уже не изменится, а текущие сутки
    уточняются медленно. Ключ кэша - границы периода, поэтому переключение
    фильтра не тянет источник заново.
    """
    return {
        day: item.summary()
        for day, item in weather_source.fetch_daily(start, end).items()
        if item.summary()
    }


def notes_storage() -> notes_store.Storage:
    """Токен на запись живёт в секретах Streamlit, рядом с кодом его нет.

    Секретов может не быть вовсе (локальный запуск) - тогда st.secrets бросает
    исключение уже на обращении к ключу, и это нормальный режим, а не сбой.
    """
    token = ""
    try:
        token = str(st.secrets.get("GITHUB_TOKEN", ""))
    except Exception:  # noqa: BLE001 - файла секретов нет: работаем локально
        token = ""
    return notes_store.Storage(path=NOTES_PATH, token=token or os.environ.get("GITHUB_TOKEN", ""))


@st.cache_data(ttl=60, show_spinner=False)
def load_notes(url: str = NOTES_URL) -> dict[date, str]:
    return notes_store.load(notes_storage(), url)


def day_context(
    day: date, weather: dict[date, str], notes: dict[date, str]
) -> str:
    """Погода и заметка одной строкой - для подсказки на графике и таблицы."""
    parts = [weather.get(day, ""), notes.get(day, "")]
    return " · ".join(part for part in parts if part)


def daily_average(frame: pd.DataFrame) -> pd.DataFrame:
    """Среднее за день по каждому источнику + сколько часов из 17 собрано."""
    grouped = frame.groupby(["date", "source"], as_index=False).agg(
        score=("score", "mean"), hours=("hour", "nunique")
    )
    grouped["score"] = grouped["score"].round(2)
    return grouped


def week_start(day: date) -> date:
    """Понедельник недели, в которую попал день."""
    return day - timedelta(days=day.weekday())


def month_start(day: date) -> date:
    return day.replace(day=1)


def closed_window(label: str, today: date, step: int = 0) -> tuple[date, date]:
    """Границы закрытого календарного периода: step=0 - последний, 1 - до него.

    Текущий период не берётся ни при каком step: он ещё идёт. Сравнивать
    вторник с прошлой полной неделей - это сравнивать два дня с семью.
    """
    if label == MONTH:
        end = month_start(today) - timedelta(days=1)
        for _ in range(step):
            end = month_start(end) - timedelta(days=1)
        return month_start(end), end
    weeks = 2 if label == TWO_WEEKS else 1
    # Воскресенье перед текущей неделей - правый край последнего закрытого окна.
    end = week_start(today) - timedelta(days=7 * weeks * step + 1)
    return end - timedelta(days=7 * weeks - 1), end


def open_window(label: str, today: date) -> tuple[date, date]:
    """Текущий, ещё не закрывшийся период: с его начала по сегодня.

    Для «2 недель» это текущая неделя, а не две: хвост показывает, что идёт
    прямо сейчас, и растягивать его на две недели вперёд нечем.
    """
    start = month_start(today) if label == MONTH else week_start(today)
    return start, today


def preset_window(
    label: str, complete: list[date], today: date
) -> tuple[tuple[date, date], tuple[date, date]]:
    """Расчётное окно и предыдущее такое же. Пустое окно - шаг назад по календарю.

    Шаг назад нужен, если сбор стоял: пустой экран вместо последней живой
    недели читается как поломка дашборда, а не как дыра в данных.
    """
    earliest = min(complete) if complete else today
    step = 0
    while True:
        start, end = closed_window(label, today, step)
        if end < earliest or any(start <= day <= end for day in complete):
            break
        step += 1
    return closed_window(label, today, step), closed_window(label, today, step + 1)


def preset_range(frame: pd.DataFrame, label: str, today: date) -> tuple[date, date]:
    """Границы, которые запрашивает фильтр, до отбора по полным суткам."""
    first, last = min(frame["date"]), max(frame["date"])
    if label not in CALENDAR_PERIODS:
        return first, last
    start, _ = closed_window(label, today)
    # Правая граница - конец истории, а не конец закрытого окна: дни текущего
    # периода показываются сверх расчётного.
    return max(first, start), last


def complete_days(frame: pd.DataFrame, sources: list[str]) -> list[date]:
    """Дни, где каждый выбранный источник дал все 17 часов окна 7:00-23:00.

    Дашборд показывает только такие дни: текущие сутки собраны наполовину, и
    их среднее ниже настоящего - вечерний пик ещё не случился. Сравнивать
    неполный день с полным нельзя, а период с периодом - тем более.
    """
    inside = frame[frame["hour"].between(HOUR_FROM, HOUR_TO)]
    if inside.empty or not sources:
        return []
    counts = (
        inside.groupby(["date", "source"])["hour"]
        .nunique()
        .unstack(fill_value=0)
        # Источник, которого нет в данных вовсе, колонки не создаст - без
        # reindex он молча выпал бы из проверки, и день считался бы полным.
        .reindex(columns=list(sources), fill_value=0)
    )
    full = counts.min(axis=1) >= FULL_DAY_HOURS
    return sorted(day for day, ok in full.items() if ok)


def today_irkutsk() -> date:
    return datetime.now(IRKUTSK_TZ).date()


def collected_hours(frame: pd.DataFrame, day: date, sources: list[str]) -> int:
    """Сколько часов окна 7:00-23:00 собрано за день - по худшему источнику.

    По худшему, а не в среднем: день считается собранным настолько, насколько
    собран самый отстающий из выбранных источников.
    """
    inside = frame[(frame["date"] == day) & frame["hour"].between(HOUR_FROM, HOUR_TO)]
    if inside.empty or not sources:
        return 0
    counts = inside.groupby("source")["hour"].nunique()
    return int(min(int(counts.get(source, 0)) for source in sources))


def running_day(
    frame: pd.DataFrame, complete: list[date], sources: list[str], today: date
) -> date | None:
    """Текущие сутки: сегодняшний день, если он собран ещё не целиком.

    Нужен на графиках отдельно от полных суток. Без него дашборд в полдень
    показывает картину позавчерашнего дня и выглядит остановившимся, а с ним
    в средних - занижает период: вечерний пик сегодня ещё не случился.
    Поэтому день видно, но все средние и сравнения идут мимо него.

    Проверка именно на «сегодня», а не на «последний день в данных»: если сбор
    упал позавчера, незаконченный позавчерашний день - это поломка, а не
    идущие сутки, и подписывать его «день идёт» нельзя.
    """
    if frame.empty or not sources or today in complete:
        return None
    if today not in set(frame["date"]):
        return None
    return today if collected_hours(frame, today, sources) else None


def period_windows(
    days: list[date], label: str, start: date, end: date, today: date
) -> tuple[list[date], list[date]]:
    """Полные дни расчётного периода и предыдущего периода той же длины.

    Готовые периоды - календарные и закрытые: «Неделя» это последняя закрытая
    неделя понедельник-воскресенье, сравнение - неделя перед ней. Дни текущей,
    ещё не закрывшейся недели сюда не попадают: их показывают отдельно.

    У своего периода длина календарная, как выбрал человек: предыдущее окно -
    столько же суток вплотную перед началом. Считать его в полных днях нельзя -
    дыра в сборе увела бы сравнение на произвольную глубину назад.
    """
    if label in CALENDAR_PERIODS:
        (now_from, now_to), (was_from, was_to) = preset_window(label, days, today)
        return (
            [day for day in days if now_from <= day <= now_to],
            [day for day in days if was_from <= day <= was_to],
        )
    if label == ALL_TIME:
        # Вся история уже показана целиком - предыдущего периода не существует.
        return days, []
    if start > end:
        start, end = end, start
    length = (end - start).days + 1
    before = start - timedelta(days=length)
    return (
        [day for day in days if start <= day <= end],
        [day for day in days if before <= day < start],
    )


def window_average(frame: pd.DataFrame, days: list[date]) -> pd.Series:
    """Средний балл за набор дней по каждому источнику.

    По источникам раздельно: методики Яндекса и 2ГИС разные, общее среднее
    двух шкал не значит ничего.
    """
    if not days:
        return pd.Series(dtype=float)
    part = frame[frame["date"].isin(days) & frame["hour"].between(HOUR_FROM, HOUR_TO)]
    return part.groupby("source")["score"].mean()


def slice_period(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Замеры с start по end включительно. Границы перепутаны - меняем местами."""
    if start > end:
        start, end = end, start
    return frame[(frame["date"] >= start) & (frame["date"] <= end)]


def expected_slots(frame: pd.DataFrame, sources: int) -> int:
    """Сколько замеров можно было снять за период.

    Полные сутки внутри периода считаются целиком, края - от первого и до
    последнего фактического замера. Иначе первый день сбора и текущий,
    ещё не закончившийся, всегда штрафуют полноту за часы, которых не было.
    """
    first_date, last_date = min(frame["date"]), max(frame["date"])
    first_hour = int(frame[frame["date"] == first_date]["hour"].min())
    last_hour = int(frame[frame["date"] == last_date]["hour"].max())
    total, day = 0, first_date
    while day <= last_date:
        low = first_hour if day == first_date else HOUR_FROM
        high = last_hour if day == last_date else HOUR_TO
        total += max(0, min(high, HOUR_TO) - max(low, HOUR_FROM) + 1)
        day += timedelta(days=1)
    return total * sources


def style_axes(figure: go.Figure, y_title: str = "Балл пробок") -> go.Figure:
    figure.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=8, b=8),
        height=360,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text=""),
        font=dict(size=13),
    )
    figure.update_xaxes(showgrid=False, linecolor=GRID, tickfont=dict(color=INK_MUTED))
    figure.update_yaxes(
        gridcolor=GRID,
        zeroline=False,
        linecolor="rgba(0,0,0,0)",
        title_text=y_title,
        tickfont=dict(color=INK_MUTED),
    )
    return figure


def add_day_context(figure: go.Figure, days: list[date], context: dict[date, str]) -> None:
    """Добавляет строку контекста в общую подсказку дня.

    Подсказка на графике объединена по X, поэтому контекст несёт отдельный
    невидимый след: иначе погода и заметка повторились бы в каждой строке
    подсказки - по разу на источник.
    """
    labels = [context.get(day, "") for day in days]
    if not any(labels):
        return
    figure.add_trace(
        go.Scatter(
            x=days,
            # Дни без контекста получают None: точки нет - и пустой строки в
            # подсказке тоже нет.
            y=[0 if label else None for label in labels],
            mode="markers",
            marker=dict(size=0.1, color="rgba(0,0,0,0)"),
            customdata=labels,
            showlegend=False,
            hovertemplate="%{customdata}<extra></extra>",
        )
    )


def line_by_source(
    frame: pd.DataFrame,
    x_field: str,
    x_title: str,
    context: dict[date, str] | None = None,
    partial: date | None = None,
    open_from: date | None = None,
    open_title: str = "",
) -> go.Figure:
    figure = go.Figure()
    for source, part in frame.groupby("source"):
        part = part.sort_values(x_field)
        colour = SOURCE_COLORS.get(source, "#888")
        marker = dict(size=8, color=colour)
        if x_field == "date" and partial is not None:
            # Полая точка на незаконченных сутках: цифра за день ещё вырастет,
            # и она не равноправна с полными днями рядом.
            marker = dict(
                size=9,
                color=colour,
                symbol=[
                    "circle-open" if day == partial else "circle" for day in part[x_field]
                ],
                line=dict(width=2, color=colour),
            )
        figure.add_trace(
            go.Scatter(
                x=part[x_field],
                y=part["score"],
                name=SOURCE_TITLES.get(source, source),
                mode="lines+markers",
                line=dict(color=colour, width=2),
                marker=marker,
                hovertemplate="%{y:.2f} балла<extra>%{fullData.name}</extra>",
            )
        )
    if x_field == "date" and context:
        add_day_context(figure, sorted(frame["date"].unique()), context)
    if x_field == "date" and open_from is not None and not frame.empty:
        # Дни текущего периода закрашены отдельным полем: они видны на графике,
        # но в расчёт не идут, и без границы читались бы как часть периода.
        # Половина суток по краям - чтобы заливка легла между точками, а не по
        # их центрам.
        figure.add_vrect(
            x0=datetime.combine(open_from, datetime.min.time()) - timedelta(hours=12),
            x1=datetime.combine(max(frame["date"]), datetime.min.time())
            + timedelta(hours=12),
            fillcolor="#f2c94c",
            opacity=0.12,
            line_width=0,
            layer="below",
            annotation_text=open_title,
            annotation_position="top left",
            annotation_font=dict(size=11, color=INK_MUTED),
        )
    figure = style_axes(figure)
    figure.update_xaxes(title_text=x_title)
    figure.update_yaxes(range=[0, 10])
    if x_field == "date":
        # Plotly подписывает даты по-английски - переводим на день.месяц.
        figure.update_xaxes(tickformat="%d.%m", dtick="D1")
    else:
        figure.update_xaxes(tickmode="linear", tick0=HOUR_FROM, dtick=2, ticksuffix=":00")
    return figure


def heatmap(frame: pd.DataFrame, source: str) -> go.Figure:
    """Тепловая карта день x час - одна последовательная шкала, без радуги."""
    part = frame[frame["source"] == source]
    pivot = part.pivot_table(index="hour", columns="date", values="score", aggfunc="mean")
    pivot = pivot.reindex(range(HOUR_FROM, HOUR_TO + 1))
    figure = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=[d.strftime("%d.%m") for d in pivot.columns],
            y=[f"{h}:00" for h in pivot.index],
            colorscale=[[0, "#eef3fd"], [0.5, "#7aa5f0"], [1, "#12327a"]],
            zmin=0,
            zmax=10,
            xgap=2,
            ygap=2,
            colorbar=dict(title="Балл", thickness=12),
            hovertemplate="%{x}, %{y}: %{z:.1f} балла<extra></extra>",
        )
    )
    # Без type="category" Plotly принимает "12.08" за число и теряет месяц.
    figure.update_xaxes(type="category")
    figure.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=8, b=8),
        height=420,
        font=dict(size=13),
    )
    return figure


def note_editor(
    days: list[date],
    default_day: date,
    weather: dict[date, str],
    notes: dict[date, str],
) -> None:
    """Погоду показываем, заметку даём написать - рядом, про один и тот же день."""
    if not days:
        return
    # Свежий день сверху и выбран по умолчанию: чаще всего комментируют вчера.
    ordered = sorted(days, reverse=True)
    columns = st.columns([1, 3])
    with columns[0]:
        day = st.selectbox(
            "День",
            ordered,
            index=ordered.index(default_day) if default_day in ordered else 0,
            format_func=lambda d: f"{d:%d.%m.%Y}",
        )
    with columns[1]:
        st.text_input(
            "Погода (тянется автоматически)",
            value=weather.get(day, "источник погоды недоступен"),
            disabled=True,
        )

    storage = notes_storage()
    with st.form(f"note-{day}"):
        text = st.text_area(
            "Заметка: перекрытия, ремонт, праздник, крупное ДТП",
            value=notes.get(day, ""),
            max_chars=notes_store.MAX_NOTE_LEN,
            height=90,
            placeholder="Например: перекрыт Глазковский мост, ремонт до 12.09",
        )
        saved = st.form_submit_button("Сохранить", type="primary")

    if not storage.writes_to_github:
        st.caption(
            "Токен GitHub не задан - заметка сохранится в файл рядом с "
            "дашбордом. В облаке такой файл живёт только до перезапуска."
        )
    st.caption(
        "Файл заметок лежит в публичном репозитории: без фамилий, номеров машин "
        "и телефонов."
    )

    if saved:
        try:
            where = notes_store.save(storage, day, text)
        except Exception as error:  # noqa: BLE001 - показываем причину, не роняем дашборд
            st.error(f"Не сохранилось: {error}")
            return
        # Кэш заметок держится минуту - без сброса своя же правка вернулась бы
        # на экран старой версией.
        load_notes.clear()
        st.success(f"Сохранено ({where}).")
        st.rerun()


def plural_days(count: int) -> str:
    """«1 день», «2 дня», «7 дней» - число всегда на виду, без «дн.»."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} дней"
    tail = count % 10
    if tail == 1:
        return f"{count} день"
    if 2 <= tail <= 4:
        return f"{count} дня"
    return f"{count} дней"


def span(days: list[date]) -> str:
    return f"{days[0]:%d.%m} - {days[-1]:%d.%m}"


def period_totals(
    frame: pd.DataFrame,
    current: list[date],
    previous: list[date],
    sources: list[str],
    window: tuple[date, date] | None = None,
) -> None:
    """Среднее за период по источникам и разница с предыдущим таким же периодом.

    window - календарные границы расчётного окна. Нужны отдельно от списка
    полных дней: если внутри недели день не собрался, «Период 08.09 - 13.09»
    соврал бы про границы недели, которая на деле началась 07.09.
    """
    st.subheader("Итог периода")
    now = window_average(frame, current)
    # Огрызок предыдущего периода в сравнение не идёт: цифра дельты читается
    # как равное сравнение и спорит с подписью под ней. Половина - граница
    # произвольная, но два дня против четырнадцати она отсекает.
    comparable = len(previous) * 2 >= len(current)
    before = window_average(frame, previous if comparable else [])
    for column, source in zip(st.columns(len(sources)), sources):
        value = now.get(source, float("nan"))
        past = before.get(source, float("nan"))
        column.metric(
            SOURCE_TITLES.get(source, source),
            "-" if pd.isna(value) else f"{value:.2f}",
            delta=(
                None
                if pd.isna(value) or pd.isna(past)
                else f"{value - past:+.2f} к прошлому периоду"
            ),
            # Рост балла - это ухудшение, зелёным его красить нельзя.
            delta_color="inverse",
        )

    bounds = span(list(window)) if window else span(current)
    missing = (
        ""
        if window is None or len(current) == (window[1] - window[0]).days + 1
        else ", остальные собраны не полностью и в расчёт не вошли"
    )
    st.caption(
        f"Период {bounds}: в расчёте {plural_days(len(current))} с полным "
        f"сбором по {FULL_DAY_HOURS} часов{missing}."
    )
    if not previous:
        st.caption("Предыдущего периода в данных нет - сравнивать не с чем.")
    elif not comparable:
        st.caption(
            f"Предыдущий период собран слишком неполно: полных дней всего "
            f"{len(previous)} против {len(current)} - сравнение не показываем."
        )
    elif len(previous) < len(current):
        # Молчать нельзя: сравнение семи дней с пятью выглядит как равное.
        st.caption(
            f"Предыдущий период {span(previous)} собран не целиком: полных дней "
            f"{len(previous)} против {len(current)}, сравнение идёт по ним."
        )
    else:
        st.caption(f"Предыдущий период: {span(previous)}.")


def main() -> None:
    st.title("🚦 Пробки Иркутска")
    st.caption(
        "Балл пробок снимается раз в час с 7:00 до 23:00 по Иркутску "
        "(сборщик опрашивает источники каждые 10 минут, в час засчитывается первый удачный замер). "
        "Источники считают по своим методикам - смотреть их рядом, а не смешивать. "
        "Неделя и месяц - календарные: считается последний закрытый период, "
        "текущий показан отдельно и в расчёт не идёт."
    )

    frame, origin = load_data(DATA_PATH)
    if frame.empty:
        st.warning(
            f"Данных пока нет. Файл `{DATA_PATH}` пуст или отсутствует. "
            "Запустите `python collect.py`."
        )
        return
    if origin == "локальная копия" and DATA_URL:
        # Молчать тут нельзя: устаревшая копия выглядит как остановившийся сбор.
        st.warning(
            "Не удалось получить свежие данные с GitHub - показана копия рядом с "
            "дашбордом, она может отставать. Обновите страницу через минуту."
        )

    # --- фильтры одной строкой над графиками ---
    today = today_irkutsk()
    first_date, last_date = min(frame["date"]), max(frame["date"])
    filters = st.columns([2, 2, 3])
    with filters[0]:
        period_label = st.selectbox(
            "Период", [*CALENDAR_PERIODS, ALL_TIME, CUSTOM], index=0
        )
    with filters[1]:
        available = sorted(frame["source"].unique())
        chosen = st.multiselect(
            "Источники",
            available,
            default=available,
            format_func=lambda s: SOURCE_TITLES.get(s, s),
        )

    start, end = preset_range(frame, period_label, today)
    if period_label == CUSTOM:
        with filters[2]:
            picked = st.date_input(
                "С какого по какое",
                value=preset_range(frame, WEEK, today),
                min_value=first_date,
                max_value=last_date,
                format="DD.MM.YYYY",
            )
        # Пока выбрана только первая дата, календарь отдаёт кортеж из одного
        # элемента - до второго клика показываем период до последнего замера.
        picked = picked if isinstance(picked, (list, tuple)) else (picked,)
        start = picked[0]
        end = picked[1] if len(picked) > 1 else last_date

    # Границы, которые запросил человек: ниже start/end съезжают на фактические
    # полные сутки, а проверить попадание текущего дня надо по запрошенным.
    wanted_start, wanted_end = min(start, end), max(start, end)

    # Средние считаются только по полностью собранным суткам: дни с дырами в
    # сборе из них выпадают.
    complete = complete_days(frame, chosen) if chosen else []
    current_days, prev_days = (
        period_windows(complete, period_label, start, end, today) if chosen else ([], [])
    )
    # Дни текущего, ещё не закрывшегося периода: полные сутки этой недели плюс
    # сегодняшний незаконченный день. Видно на графике, в расчёт не идут.
    open_days: list[date] = []
    if period_label in CALENDAR_PERIODS:
        open_from, _ = open_window(period_label, today)
        open_days = [day for day in complete if open_from <= day <= today]
    partial = running_day(frame, complete, chosen, today)
    if partial is not None and not wanted_start <= partial <= wanted_end:
        partial = None
    if partial is not None:
        open_days.append(partial)

    shown_days = sorted(set(current_days) | set(open_days))
    if shown_days:
        start, end = shown_days[0], shown_days[-1]

    period = slice_period(frame, start, end)
    period = period[period["source"].isin(chosen)] if chosen else period.iloc[0:0]
    if shown_days:
        # Неполный день посреди периода (сбор падал) выбрасываем целиком: иначе
        # он занижает и дневной график, и среднее за период. Сегодняшний день -
        # исключение, он в списке показанных.
        period = period[period["date"].isin(shown_days)]
    # Замеры вне 7:00-23:00 - это опоздавшие запуски сборщика, сползшие за
    # полночь. В средние и в полноту они не идут, но и не пропадают: счётчик
    # под таблицей показывает, сколько их.
    outside = period[~period["hour"].between(HOUR_FROM, HOUR_TO)]
    period = period[period["hour"].between(HOUR_FROM, HOUR_TO)]
    if period.empty:
        st.info(
            f"С {min(start, end):%d.%m.%Y} по {max(start, end):%d.%m.%Y} "
            "по выбранным источникам данных нет."
        )
        return

    open_title = (
        "текущая неделя" if period_label in (WEEK, TWO_WEEKS)
        else "текущий месяц" if period_label == MONTH
        else "сегодня"
    )
    if not current_days and not open_days:
        st.caption(
            f"В этом периоде нет ни одних полностью собранных суток "
            f"({FULL_DAY_HOURS} часов подряд) - показано всё как есть, включая "
            "незаконченные дни."
        )
    elif not current_days:
        st.caption(
            f"Полностью собранных суток ({FULL_DAY_HOURS} часов подряд) в "
            f"закрытом периоде нет - показан только {open_title}, считать и "
            "сравнивать пока нечего."
        )

    # --- контекст дня: погода тянется сама, заметки пишет человек ---
    period_first, period_last = min(period["date"]), max(period["date"])
    weather = load_weather(period_first, period_last)
    notes = load_notes()
    context = {
        day: day_context(day, weather, notes)
        for day in sorted(period["date"].unique())
    }

    # --- KPI ---
    daily = daily_average(period)
    latest_ts = period["ts_local"].max()
    # Полные сутки периода: на них считаются все средние. Текущий день сюда не
    # входит - его среднее ещё вырастет к вечеру.
    full_period = period[period["date"].isin(current_days)] if current_days else period
    # Последний день считается внутри периода: при своих датах последний замер
    # всей истории может лежать далеко за правой границей.
    tiles = st.columns(len(chosen) + 2)
    for column, source in zip(tiles, chosen):
        source_rows = period[period["source"] == source].sort_values("ts_local")
        last_score = source_rows["score"].iloc[-1]
        last_time = str(source_rows["ts_local"].iloc[-1])[11:16]
        last_day = daily[(daily["source"] == source) & (daily["date"] == period_last)]
        last_avg = last_day["score"].iloc[0] if not last_day.empty else float("nan")
        running = " (идёт)" if period_last == partial else ""
        column.metric(
            f"{SOURCE_TITLES.get(source, source)}: среднее за {period_last:%d.%m}{running}",
            "-" if pd.isna(last_avg) else f"{last_avg:.1f}",
        )
        column.caption(f"последний замер {last_score:g} в {last_time}")
    tiles[-2].metric("Среднее за период", f"{full_period['score'].mean():.2f}")
    if open_days and current_days:
        tiles[-2].caption(f"по закрытому периоду, без дней «{open_title}»")

    # Дни внутри периода считаются целиком, поэтому полностью пропущенные
    # сутки видны. Края - по факту первого и последнего замера.
    span_days = (period_last - period_first).days + 1
    expected = expected_slots(period, max(len(chosen), 1))
    coverage = len(period) / expected * 100 if expected else 0
    tiles[-1].metric(
        "Полнота сбора",
        f"{coverage:.0f}%",
        help=(
            f"{len(period)} замеров из {expected} возможных за {span_days} дн. "
            "Первый и последний день периода считаются от фактического замера, "
            f"а не от 7:00 и 23:00. Последнее измерение: {latest_ts}"
        ),
    )

    if open_days and current_days:
        where = span(open_days) if len(open_days) > 1 else f"{open_days[0]:%d.%m}"
        st.caption(
            f"Жёлтым справа - {open_title} ({where}). Период ещё не закрылся, "
            "поэтому эти дни видно, но в средние и в сравнение они не идут - "
            "войдут, когда период закончится."
        )
    if partial is not None:
        done = collected_hours(period, partial, chosen)
        st.caption(
            f"{partial:%d.%m} - сутки ещё идут: собрано {done} из {FULL_DAY_HOURS} "
            "часов, на графике день отмечен полой точкой. Вечерний пик ещё "
            "впереди, и цифра дня к ночи вырастет."
        )

    if context.get(period_last):
        st.caption(f"{period_last:%d.%m}: {context[period_last]}")

    st.divider()

    left, right = st.columns(2)
    with left:
        st.subheader("Средний балл по дням")
        st.plotly_chart(
            line_by_source(
                daily,
                "date",
                "Дата",
                context=context,
                partial=partial,
                open_from=min(open_days) if open_days else None,
                open_title=open_title,
            ),
            width="stretch",
        )
    with right:
        st.subheader("Профиль по часам")
        # Профиль - по полным суткам: у текущего дня есть только утро, и оно
        # перевесило бы утренние часы относительно вечерних.
        hourly = (
            full_period.groupby(["hour", "source"], as_index=False)["score"]
            .mean()
            .round(2)
        )
        st.plotly_chart(line_by_source(hourly, "hour", "Час"), width="stretch")

    if current_days:
        window = (
            preset_window(period_label, complete, today)[0]
            if period_label in CALENDAR_PERIODS
            else None
        )
        period_totals(frame, current_days, prev_days, chosen, window)

    st.subheader("День и час: где скапливаются пробки")
    heat_source = st.radio(
        "Источник для карты",
        chosen,
        horizontal=True,
        format_func=lambda s: SOURCE_TITLES.get(s, s),
        label_visibility="collapsed",
    )
    st.plotly_chart(heatmap(period, heat_source), width="stretch")

    st.subheader("Что было в этот день")
    note_editor(sorted(period["date"].unique()), period_last, weather, notes)

    with st.expander("Таблица: средние по дням"):
        table = daily.pivot(index="date", columns="source", values="score")
        table.columns = [SOURCE_TITLES.get(c, c) for c in table.columns]
        # Колонка нужна только когда есть незаконченный день: иначе в таблице
        # стояла бы полоса одинаковых семнадцаток без смысла.
        if partial is not None:
            table[f"Часов из {FULL_DAY_HOURS}"] = [
                collected_hours(period, day, chosen) for day in table.index
            ]
        # Контекст идёт последними колонками: сначала цифра, потом объяснение.
        table["Погода"] = [weather.get(day, "") for day in table.index]
        table["Заметка"] = [notes.get(day, "") for day in table.index]
        table.index.name = "Дата"
        st.dataframe(table.sort_index(ascending=False), width="stretch")
        st.download_button(
            "Скачать всю историю (CSV)",
            DATA_PATH.read_bytes(),
            file_name="traffic_irkutsk.csv",
            mime="text/csv",
        )

    if not outside.empty:
        st.caption(
            f"Ещё {len(outside)} измерений пришлись на время вне окна 7:00-23:00 - "
            "запуски сборщика, опоздавшие за полночь. В средние они не вошли."
        )


if __name__ == "__main__":
    main()
