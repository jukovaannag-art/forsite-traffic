"""Дашборд по баллам пробок Иркутска: Яндекс и 2ГИС.

Запуск: streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
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

HOUR_FROM, HOUR_TO = 7, 23

# Готовые периоды и сколько дней они берут, считая последний день с данными.
PERIOD_DAYS = {"Неделя": 7, "2 недели": 14, "Месяц": 30}
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


def daily_average(frame: pd.DataFrame) -> pd.DataFrame:
    """Среднее за день по каждому источнику + сколько часов из 17 собрано."""
    grouped = frame.groupby(["date", "source"], as_index=False).agg(
        score=("score", "mean"), hours=("hour", "nunique")
    )
    grouped["score"] = grouped["score"].round(2)
    return grouped


def preset_range(frame: pd.DataFrame, label: str) -> tuple[date, date]:
    """Границы готового периода: последние N дней, считая от последнего замера.

    Отсчёт идёт от данных, а не от сегодня: если сбор остановился, дашборд
    покажет последнюю живую неделю, а не пустой экран.
    """
    first, last = min(frame["date"]), max(frame["date"])
    days = PERIOD_DAYS.get(label)
    if days is None:
        return first, last
    return max(first, last - timedelta(days=days - 1)), last


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


def line_by_source(frame: pd.DataFrame, x_field: str, x_title: str) -> go.Figure:
    figure = go.Figure()
    for source, part in frame.groupby("source"):
        part = part.sort_values(x_field)
        figure.add_trace(
            go.Scatter(
                x=part[x_field],
                y=part["score"],
                name=SOURCE_TITLES.get(source, source),
                mode="lines+markers",
                line=dict(color=SOURCE_COLORS.get(source, "#888"), width=2),
                marker=dict(size=8),
                hovertemplate="%{y:.2f} балла<extra>%{fullData.name}</extra>",
            )
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


def main() -> None:
    st.title("🚦 Пробки Иркутска")
    st.caption(
        "Балл пробок снимается раз в час с 7:00 до 23:00 по Иркутску "
        "(сборщик опрашивает источники каждые 10 минут, в час засчитывается первый удачный замер). "
        "Источники считают по своим методикам - смотреть их рядом, а не смешивать."
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
    first_date, last_date = min(frame["date"]), max(frame["date"])
    filters = st.columns([2, 2, 3])
    with filters[0]:
        period_label = st.selectbox(
            "Период", [*PERIOD_DAYS, ALL_TIME, CUSTOM], index=0
        )
    with filters[1]:
        available = sorted(frame["source"].unique())
        chosen = st.multiselect(
            "Источники",
            available,
            default=available,
            format_func=lambda s: SOURCE_TITLES.get(s, s),
        )

    start, end = preset_range(frame, period_label)
    if period_label == CUSTOM:
        with filters[2]:
            picked = st.date_input(
                "С какого по какое",
                value=preset_range(frame, "Неделя"),
                min_value=first_date,
                max_value=last_date,
                format="DD.MM.YYYY",
            )
        # Пока выбрана только первая дата, календарь отдаёт кортеж из одного
        # элемента - до второго клика показываем период до последнего замера.
        picked = picked if isinstance(picked, (list, tuple)) else (picked,)
        start = picked[0]
        end = picked[1] if len(picked) > 1 else last_date

    period = slice_period(frame, start, end)
    period = period[period["source"].isin(chosen)] if chosen else period.iloc[0:0]
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

    # --- KPI ---
    daily = daily_average(period)
    latest_ts = period["ts_local"].max()
    # Последний день считается внутри периода: при своих датах последний замер
    # всей истории может лежать далеко за правой границей.
    period_first, period_last = min(period["date"]), max(period["date"])
    tiles = st.columns(len(chosen) + 2)
    for column, source in zip(tiles, chosen):
        source_rows = period[period["source"] == source].sort_values("ts_local")
        last_score = source_rows["score"].iloc[-1]
        last_time = str(source_rows["ts_local"].iloc[-1])[11:16]
        today = daily[(daily["source"] == source) & (daily["date"] == period_last)]
        today_avg = today["score"].iloc[0] if not today.empty else float("nan")
        column.metric(
            f"{SOURCE_TITLES.get(source, source)}: среднее за {period_last:%d.%m}",
            "-" if pd.isna(today_avg) else f"{today_avg:.1f}",
        )
        column.caption(f"последний замер {last_score:g} в {last_time}")
    tiles[-2].metric("Среднее за период", f"{period['score'].mean():.2f}")

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

    st.divider()

    left, right = st.columns(2)
    with left:
        st.subheader("Средний балл по дням")
        st.plotly_chart(line_by_source(daily, "date", "Дата"), width="stretch")
    with right:
        st.subheader("Профиль по часам")
        hourly = (
            period.groupby(["hour", "source"], as_index=False)["score"].mean().round(2)
        )
        st.plotly_chart(line_by_source(hourly, "hour", "Час"), width="stretch")

    st.subheader("День и час: где скапливаются пробки")
    heat_source = st.radio(
        "Источник для карты",
        chosen,
        horizontal=True,
        format_func=lambda s: SOURCE_TITLES.get(s, s),
        label_visibility="collapsed",
    )
    st.plotly_chart(heatmap(period, heat_source), width="stretch")

    with st.expander("Таблица: средние по дням"):
        table = daily.pivot(index="date", columns="source", values="score")
        table.columns = [SOURCE_TITLES.get(c, c) for c in table.columns]
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
