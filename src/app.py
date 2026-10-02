from pathlib import Path

import duckdb
import plotly.graph_objects as go

from shiny.express import ui
from shinywidgets import render_plotly

ui.page_opts(title="Signaalianalyysi")

PARQUET_PATH = (
    Path(__file__).parent.parent
    / "data.parquet"
)

ui.h3("Signaali")
ui.p("Punaiset pisteet = havaittu poikkeama")


@render_plotly
def signal_plot():

    df = duckdb.sql(
        f"""
        SELECT adc1
        FROM '{PARQUET_PATH}'
        USING SAMPLE 100000 ROWS
        """
    ).df()

    df["adc1_filtered"] = (
        df["adc1"]
        .rolling(
            window=20,
            center=True,
            min_periods=1
        )
        .mean()
    )

    median = df["adc1_filtered"].median()

    mad = (
        (df["adc1_filtered"] - median)
        .abs()
        .median()
    )

    k = 10

    threshold_high = median + k * mad
    threshold_low = median - k * mad

    df["anomaly"] = (
        (df["adc1_filtered"] > threshold_high)
        |
        (df["adc1_filtered"] < threshold_low)
    )

    anomalies = df[df["anomaly"]]

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["adc1_filtered"],
            mode="lines",
            name="Suodatettu signaali",
            line=dict(
                color="#8A00FF",
                width=1
            )
        )
    )

    fig.add_trace(
        go.Scatter(
            x=anomalies.index,
            y=anomalies["adc1_filtered"],
            mode="markers",
            name=f"Poikkeamat ({len(anomalies)})",
            marker=dict(
                color="red",
                size=5
            )
        )
    )

    fig.add_hline(
        y=threshold_high,
        line_dash="dash",
        line_color="red"
    )

    fig.add_hline(
        y=threshold_low,
        line_dash="dash",
        line_color="red"
    )

    fig.update_layout(
        template="simple_white",
        hovermode="x unified",
        height=700,
        xaxis_title="Aika",
        yaxis_title="Signaali",
        legend_title="Selite"
    )

    return fig