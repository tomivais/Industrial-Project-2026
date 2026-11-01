
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go

from shiny import ui, render, reactive
from shinywidgets import output_widget, render_widget


# ============================================================
# ASETUKSET
# ============================================================

PARQUET_PATH = (
    Path(__file__).parent.parent
    / "data.parquet"
)

# Kuinka monta aikaikkunaa näytetään yleiskuvassa.
MAX_OVERVIEW_BUCKETS = 2000

# Enimmäismäärä alkuperäisiä pisteitä tarkassa zoomauksessa.
MAX_DETAIL_POINTS = 250_000

# Poikkeamien tunnistuksen herkkyys.
K = 10

# DuckDB:n muistiraja.
DUCKDB_MEMORY_LIMIT = "4GB"


# ============================================================
# DUCKDB
# ============================================================

def create_connection():

    con = duckdb.connect(database=":memory:")

    con.execute(
        f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'"
    )

    con.execute("SET threads = 4")

    return con


# ============================================================
# UI
# ============================================================

def app1_ui():

    return ui.div(

        ui.h3("Signaalin visualisointi"),

        ui.p(
            "Koko signaali näytetään tiivistettynä. "
            "Zoomauksessa voidaan tarkastella alkuperäisiä näytteitä."
        ),

        ui.hr(),

        ui.h4("Koko signaali"),

        output_widget("overview_plot"),

        ui.hr(),

        ui.h4("Tarkastele signaalin aluetta"),

        ui.div(
            ui.input_numeric(
                "zoom_start",
                "Alkuindeksi",
                value=0,
                min=0,
                step=1
            ),

            ui.input_numeric(
                "zoom_end",
                "Loppuindeksi",
                value=10000,
                min=0,
                step=1
            ),

            style=(
                "display: flex; "
                "gap: 20px; "
                "flex-wrap: wrap;"
            )
        ),

        ui.output_text("dataset_info"),

        output_widget("detail_plot"),

    )


# ============================================================
# SERVER
# ============================================================

def app1_server(input, output, session):

    con = create_connection()

    if not PARQUET_PATH.exists():
        raise FileNotFoundError(
            f"Parquet-tiedostoa ei löydy: {PARQUET_PATH}"
        )

    parquet = str(PARQUET_PATH)

    # --------------------------------------------------------
    # 1. Lasketaan koko aineiston tilastot.
    # --------------------------------------------------------

    @reactive.calc
    def global_stats():

        count, median = con.execute(
            """
            SELECT
                COUNT(*),
                approx_quantile(adc1, 0.5)

            FROM read_parquet(
                ?,
                file_row_number = true
            )
            """,
            [parquet]
        ).fetchone()

        mad = con.execute(
            """
            SELECT
                approx_quantile(
                    abs(adc1 - ?),
                    0.5
                )

            FROM read_parquet(
                ?,
                file_row_number = true
            )
            """,
            [median, parquet]
        ).fetchone()[0]

        threshold_high = median + K * mad
        threshold_low = median - K * mad

        return {
            "count": int(count),
            "median": float(median),
            "mad": float(mad),
            "threshold_high": float(threshold_high),
            "threshold_low": float(threshold_low)
        }


    # --------------------------------------------------------
    # 2. Koko aineiston Min-Max-downsampling.
    # --------------------------------------------------------

    @reactive.calc
    def overview_data():

        stats = global_stats()

        total_count = stats["count"]

        bucket_size = max(
            1,
            (total_count + MAX_OVERVIEW_BUCKETS - 1)
            // MAX_OVERVIEW_BUCKETS
        )

        high = stats["threshold_high"]
        low = stats["threshold_low"]

        df = con.execute(
            """
            SELECT

                FLOOR(
                    file_row_number / ?
                )::BIGINT AS bucket,

                MIN(adc1) AS min_value,
                MAX(adc1) AS max_value,

                MIN_BY(
                    file_row_number,
                    adc1
                ) AS min_sample,

                MAX_BY(
                    file_row_number,
                    adc1
                ) AS max_sample,

                COUNT(*) FILTER (
                    WHERE adc1 > ? OR adc1 < ?
                ) AS anomaly_count,

                MIN(adc1) FILTER (
                    WHERE adc1 > ? OR adc1 < ?
                ) AS anomaly_min_value,

                MAX(adc1) FILTER (
                    WHERE adc1 > ? OR adc1 < ?
                ) AS anomaly_max_value,

                MIN_BY(
                    file_row_number,
                    adc1
                ) FILTER (
                    WHERE adc1 > ? OR adc1 < ?
                ) AS anomaly_min_sample,

                MAX_BY(
                    file_row_number,
                    adc1
                ) FILTER (
                    WHERE adc1 > ? OR adc1 < ?
                ) AS anomaly_max_sample

            FROM read_parquet(
                ?,
                file_row_number = true
            )

            GROUP BY bucket

            ORDER BY bucket
            """,

            [
                bucket_size,

                high, low,
                high, low,
                high, low,
                high, low,
                high, low,

                parquet
            ]
        ).fetchdf()

        return df


    # --------------------------------------------------------
    # 3. Koko signaalin visualisointi.
    # --------------------------------------------------------

    @render_widget
    def overview_plot():

        stats = global_stats()
        df = overview_data()

        # Minimit ja maksimit säilytetään niiden
        # alkuperäisessä aikajärjestyksessä.

        x = []
        y = []

        anomaly_x = []
        anomaly_y = []

        for row in df.itertuples():

            points = [
                (
                    int(row.min_sample),
                    float(row.min_value)
                ),
                (
                    int(row.max_sample),
                    float(row.max_value)
                )
            ]

            points.sort(key=lambda p: p[0])

            for sample, value in points:

                if (
                    not x
                    or sample != x[-1]
                ):
                    x.append(sample)
                    y.append(value)

            # Poikkeamien paikalliset ääriarvot.

            if row.anomaly_count > 0:

                if pd.notna(row.anomaly_min_sample):

                    anomaly_x.append(
                        int(row.anomaly_min_sample)
                    )

                    anomaly_y.append(
                        float(row.anomaly_min_value)
                    )

                if (
                    pd.notna(row.anomaly_max_sample)
                    and row.anomaly_max_sample
                    != row.anomaly_min_sample
                ):

                    anomaly_x.append(
                        int(row.anomaly_max_sample)
                    )

                    anomaly_y.append(
                        float(row.anomaly_max_value)
                    )

        anomaly_total = int(
            df["anomaly_count"].sum()
        )

        fig = go.Figure()

        fig.add_trace(
            go.Scattergl(
                x=x,
                y=y,
                mode="lines",
                name="Signaali (Min-Max)",
                line=dict(
                    color="#8A00FF",
                    width=1
                )
            )
        )

        fig.add_trace(
            go.Scattergl(
                x=anomaly_x,
                y=anomaly_y,
                mode="markers",
                name=f"Poikkeamat ({anomaly_total})",
                marker=dict(
                    color="red",
                    size=5
                )
            )
        )

        fig.add_hline(
            y=stats["threshold_high"],
            line_dash="dash",
            line_color="red",
            annotation_text="Yläraja"
        )

        fig.add_hline(
            y=stats["threshold_low"],
            line_dash="dash",
            line_color="red",
            annotation_text="Alaraja"
        )

        fig.update_layout(
            template="simple_white",
            height=650,
            hovermode="x unified",
            xaxis_title="Näyteindeksi",
            yaxis_title="ADC1",
            legend_title="Selite",
            uirevision="overview"
        )

        return fig


    # --------------------------------------------------------
    # 4. Alkuperäisen datan hakeminen zoomauksessa.
    # --------------------------------------------------------

    @render_widget
    def detail_plot():

        stats = global_stats()

        total_count = stats["count"]

        start = input.zoom_start()
        end = input.zoom_end()

        if start is None or end is None:
            return go.Figure()

        start = max(0, int(start))
        end = min(total_count - 1, int(end))

        if start >= end:

            fig = go.Figure()

            fig.add_annotation(
                text="Tarkista alku- ja loppuindeksi.",
                showarrow=False
            )

            return fig

        point_count = end - start + 1

        fig = go.Figure()

        # Liian suuri alue ei ladata RAM-muistiin.
        if point_count > MAX_DETAIL_POINTS:

            fig.add_annotation(
                text=(
                    f"Valittu alue sisältää {point_count:,} näytettä. "
                    f"Rajaa alue enintään "
                    f"{MAX_DETAIL_POINTS:,} näytteeseen."
                ),
                showarrow=False,
                font=dict(size=15)
            )

            fig.update_layout(
                template="simple_white",
                height=450
            )

            return fig

        # Haetaan vain valitun alueen alkuperäiset näytteet.

        df = con.execute(
            """
            SELECT
                file_row_number AS sample_idx,
                adc1

            FROM read_parquet(
                ?,
                file_row_number = true
            )

            WHERE
                file_row_number >= ?
                AND file_row_number <= ?

            ORDER BY file_row_number
            """,

            [
                parquet,
                start,
                end
            ]

        ).fetchdf()

        # Poikkeamat lasketaan alkuperäisistä näytteistä.

        anomaly_mask = (
            (df["adc1"] > stats["threshold_high"])
            |
            (df["adc1"] < stats["threshold_low"])
        )

        anomalies = df[anomaly_mask]

        fig.add_trace(
            go.Scattergl(
                x=df["sample_idx"],
                y=df["adc1"],
                mode="lines",
                name="Alkuperäinen signaali",
                line=dict(
                    color="#8A00FF",
                    width=1
                )
            )
        )

        fig.add_trace(
            go.Scattergl(
                x=anomalies["sample_idx"],
                y=anomalies["adc1"],
                mode="markers",
                name=f"Poikkeamat ({len(anomalies)})",
                marker=dict(
                    color="red",
                    size=6
                )
            )
        )

        fig.add_hline(
            y=stats["threshold_high"],
            line_dash="dash",
            line_color="red"
        )

        fig.add_hline(
            y=stats["threshold_low"],
            line_dash="dash",
            line_color="red"
        )

        fig.update_layout(
            template="simple_white",
            height=550,
            hovermode="x unified",
            xaxis_title="Näyteindeksi",
            yaxis_title="ADC1",
            legend_title="Selite",
            uirevision="detail"
        )

        return fig


    # --------------------------------------------------------
    # 5. Tietojen näyttäminen käyttöliittymässä.
    # --------------------------------------------------------

    @render.text
    def dataset_info():

        stats = global_stats()

        start = input.zoom_start()
        end = input.zoom_end()

        return (
            f"Aineiston koko: {stats['count']:,} näytettä\n"
            f"Mediaani: {stats['median']:.4f}\n"
            f"MAD: {stats['mad']:.4f}\n"
            f"Poikkeamaraja: "
            f"{stats['threshold_low']:.4f} – "
            f"{stats['threshold_high']:.4f}\n"
            f"Zoomausalue: {start} – {end}\n"
            f"Enimmäismäärä tarkassa näkymässä: "
            f"{MAX_DETAIL_POINTS:,} näytettä"
        )
