"""Дашборд по баллам пробок Иркутска: Яндекс и 2ГИС.

Запуск: streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
# TRAFFIC_CSV позволяет открыть дашборд на другом файле (демо, проверка).
DATA_PATH = Path(os.environ.get("TRAFFIC_CSV") or ROOT / "data" / "traffic_irkutsk.csv")

HOUR_FROM, HOUR_TO = 7, 23
HOURS_PER_DAY = HOUR_TO - HOUR_FROM + 1

# Цвета источников закреплены за источником, а не за порядком в фильтре.
# Пара проверена на различимость при дальтонизме (ΔE 32 protan).
SOURCE_COLORS = {"yandex": "#2563eb", "2gis": "#d97706"}
SOURCE_TITLES = {"yandex": "Яндекс.Пробки", "2gis": "2ГИС"}

GRID = "#e8e6e1"
INK_MUTED = "#6b6b66"

st.set_page_config(page_title="Пробки Иркутска - Форсайт", page_icon="🚦", layout="wide")


@st.cache_data(ttl=300)
def load_data(path: Path) -> pd.DataFrame:
    """Читает историю измерений. Строки без балла (сбой источника) отбрасываем."""
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"source": str})
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["hour"] = pd.to_numeric(frame["hour"], errors="coerce")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["date", "hour", "score"])
    frame["hour"] = frame["hour"].astype(int)
    return frame


def daily_average(frame: pd.DataFrame) -> pd.DataFrame:
    """Среднее за день по каждому источнику + сколько часов из 17 собрано."""
    grouped = frame.groupby(["date", "source"], as_index=False).agg(
        score=("score", "mean"), hours=("hour", "nunique")
    )
    grouped["score"] = grouped["score"].round(2)
    return grouped


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
        "Балл пробок снимается каждый час с 7:00 до 23:00 по Иркутску. "
        "Источники считают по своим методикам - смотреть их рядом, а не смешивать."
    )

    frame = load_data(DATA_PATH)
    if frame.empty:
        st.warning(
            f"Данных пока нет. Файл `{DATA_PATH}` пуст или отсутствует. "
            "Запустите `python collect.py --force`."
        )
        return

    # --- фильтры одной строкой над графиками ---
    last_date = max(frame["date"])
    filters = st.columns([2, 2, 3])
    with filters[0]:
        period_label = st.selectbox(
            "Период", ["Неделя", "2 недели", "Месяц", "Всё время"], index=0
        )
    days = {"Неделя": 7, "2 недели": 14, "Месяц": 30}.get(period_label)
    with filters[1]:
        available = sorted(frame["source"].unique())
        chosen = st.multiselect(
            "Источники",
            available,
            default=available,
            format_func=lambda s: SOURCE_TITLES.get(s, s),
        )

    period = frame if days is None else frame[frame["date"] > last_date - timedelta(days=days)]
    period = period[period["source"].isin(chosen)] if chosen else period.iloc[0:0]
    if period.empty:
        st.info("За выбранный период и источники данных нет.")
        return

    # --- KPI ---
    daily = daily_average(period)
    latest_ts = period["ts_local"].max()
    tiles = st.columns(len(chosen) + 2)
    for column, source in zip(tiles, chosen):
        source_rows = period[period["source"] == source].sort_values("ts_local")
        last_score = source_rows["score"].iloc[-1]
        last_time = str(source_rows["ts_local"].iloc[-1])[11:16]
        today = daily[(daily["source"] == source) & (daily["date"] == last_date)]
        today_avg = today["score"].iloc[0] if not today.empty else float("nan")
        column.metric(
            f"{SOURCE_TITLES.get(source, source)}: среднее за день",
            "-" if pd.isna(today_avg) else f"{today_avg:.1f}",
        )
        column.caption(f"последний замер {last_score:g} в {last_time}")
    tiles[-2].metric("Среднее за период", f"{period['score'].mean():.2f}")

    # Считаем от календарных дней периода, а не от дней с данными: иначе
    # полностью пропущенные сутки не видны - процент останется высоким.
    span_days = (last_date - min(period["date"])).days + 1
    expected = span_days * HOURS_PER_DAY * max(len(chosen), 1)
    coverage = len(period) / expected * 100 if expected else 0
    tiles[-1].metric(
        "Полнота сбора",
        f"{coverage:.0f}%",
        help=(
            f"{len(period)} замеров из {expected} возможных за {span_days} дн. "
            f"Последнее измерение: {latest_ts}"
        ),
    )

    st.divider()

    left, right = st.columns(2)
    with left:
        st.subheader("Средний балл по дням")
        st.plotly_chart(line_by_source(daily, "date", "Дата"), use_container_width=True)
    with right:
        st.subheader("Профиль по часам")
        hourly = (
            period.groupby(["hour", "source"], as_index=False)["score"].mean().round(2)
        )
        st.plotly_chart(line_by_source(hourly, "hour", "Час"), use_container_width=True)

    st.subheader("День и час: где скапливаются пробки")
    heat_source = st.radio(
        "Источник для карты",
        chosen,
        horizontal=True,
        format_func=lambda s: SOURCE_TITLES.get(s, s),
        label_visibility="collapsed",
    )
    st.plotly_chart(heatmap(period, heat_source), use_container_width=True)

    with st.expander("Таблица: средние по дням"):
        table = daily.pivot(index="date", columns="source", values="score")
        table.columns = [SOURCE_TITLES.get(c, c) for c in table.columns]
        table.index.name = "Дата"
        st.dataframe(table.sort_index(ascending=False), use_container_width=True)
        st.download_button(
            "Скачать всю историю (CSV)",
            DATA_PATH.read_bytes(),
            file_name="traffic_irkutsk.csv",
            mime="text/csv",
        )

    gaps = period[~period["hour"].between(HOUR_FROM, HOUR_TO)]
    if not gaps.empty:
        st.caption(
            f"В данных есть {len(gaps)} измерений вне окна 7:00-23:00 - "
            "скорее всего, ручные прогоны с --force."
        )


if __name__ == "__main__":
    main()
