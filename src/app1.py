
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go

from shiny import ui, reactive
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

# Tarkka näkymä on aina näin monta peräkkäistä näytettä.
WINDOW = 100_000

# Ensimmäiset imupiikit alkavat tästä. Tiedoston alku on tasainen.
DEFAULT_START = 5_390_000

# Poikkeamien tunnistuksen herkkyys.
K = 10

# DuckDB:n muistiraja.
DUCKDB_MEMORY_LIMIT = "4GB"

# Kohinanvaimennus keskiarvoistaa näin monta peräkkäistä näytettä.
SMOOTH_WINDOW = 20

# Automaattinen kynnys on zoomatun alueen mediaani plus tämä luku.
LEVEL_ABOVE_MEDIAN = 40

# Oma kynnys, kun automaattinen valinta on pois päältä.
DEFAULT_THRESHOLD = 550

# Tätä lyhyempi kynnyksen ylitys on kohinaa, ei piikkiä.
MIN_PEAK_WIDTH = 30

# Näin lyhyt notkahdus kynnyksen alle ei katkaise samaa nousua.
MAX_GAP = 15


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


def smooth_signal(values):
    """Kohinanvaimennus: jokainen piste on 20 vierekkäisen näytteen keskiarvo."""

    return values.rolling(
        window=SMOOTH_WINDOW,
        center=True,
        min_periods=1
    ).mean()


def peak_indices(values, threshold=None):
    """Yksi huippu per nousu. Kohinanvaimennus tehdään ennen tätä kutsua."""

    if threshold is None:
        threshold = float(values.median()) + LEVEL_ABOVE_MEDIAN

    hot = (values > threshold).to_numpy()
    labels = values.index.to_numpy()
    peaks = []

    i = 0
    n = len(hot)

    while i < n:

        if not hot[i]:
            i += 1
            continue

        j = i

        while j < n and hot[j]:
            j += 1

        # Alle 15 näytteen notkahdus ei katkaise samaa nousua.
        while True:

            k = j

            while k < n and k - j <= MAX_GAP and not hot[k]:
                k += 1

            if k < n and k - j <= MAX_GAP and hot[k]:

                while k < n and hot[k]:
                    k += 1

                j = k

            else:
                break

        # Alle 30 näytteen nousu jätetään pois.
        if j - i >= MIN_PEAK_WIDTH:

            segment = values.iloc[i:j]
            peaks.append(labels[i + int(segment.to_numpy().argmax())])

        i = j

    return pd.Series(peaks), threshold


# ============================================================
# UI
# ============================================================

def app1_ui():

    return ui.div(

        ui.busy_indicators.options(fade_opacity=1),

        ui.h4("Tarkastele signaalin aluetta"),

        ui.div(
            ui.input_numeric(
                "start_row",
                "Alkurivi",
                value=DEFAULT_START,
                min=0,
                step=WINDOW
            ),

            style=(
                "display: flex; "
                "gap: 20px; "
                "flex-wrap: wrap; "
                "align-items: end;"
            )
        ),

        ui.div(
            ui.input_checkbox(
                "smooth",
                "Liukuva keskiarvo (20 näytettä)",
                value=True
            ),

            ui.input_checkbox(
                "show_peaks",
                "Merkitse kynnyksen ylittävät huiput",
                value=True
            ),

            ui.input_checkbox(
                "auto_threshold",
                "Automaattinen kynnys",
                value=True
            ),

            ui.input_numeric(
                "threshold_value",
                "Kynnys",
                value=DEFAULT_THRESHOLD,
                min=0,
                step=10
            ),

            style=(
                "display: flex; "
                "gap: 20px; "
                "flex-wrap: wrap; "
                "align-items: end;"
            )
        ),

        ui.div(
            output_widget("detail_plot"),
            class_="signal-plot-slot signal-plot-slot-detail",
        ),

        ui.hr(),

        ui.h4("Koko signaali"),

        ui.div(
            output_widget("overview_plot"),
            class_="signal-plot-slot signal-plot-slot-overview",
        ),

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

        start_value = input.start_row()

        fig = go.Figure()

        start = DEFAULT_START if start_value is None else max(0, int(start_value))
        end = min(total_count, start + WINDOW)

        if start >= end:

            fig.add_annotation(
                text="Ei rivejä tällä alkukohdalla.",
                showarrow=False
            )

            fig.update_layout(
                template="simple_white",
                height=550
            )

            return fig

        # Haetaan 100 000 peräkkäistä näytettä.

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
                AND file_row_number < ?

            ORDER BY file_row_number
            """,

            [
                parquet,
                start,
                end
            ]

        ).fetchdf()

        if df.empty:

            fig.update_layout(
                template="simple_white",
                title="Ei rivejä tällä alueella",
                height=550
            )

            return fig

        df = df.set_index("sample_idx")

        # Kohinanvaimennus ennen piikkien etsintää.
        # Jos tasoitus on pois, piikit etsitään raa'asta signaalista.
        if input.smooth():
            signal = smooth_signal(df["adc1"])
            signal_name = "Tasoitettu signaali"
        else:
            signal = df["adc1"]
            signal_name = "Alkuperäinen signaali"

        fig.add_trace(
            go.Scattergl(
                x=signal.index,
                y=signal,
                mode="lines",
                name=signal_name,
                line=dict(
                    color="#8A00FF",
                    width=1
                )
            )
        )

        if input.show_peaks():

            if input.auto_threshold():
                chosen = None
            else:
                typed = input.threshold_value()
                chosen = (
                    DEFAULT_THRESHOLD
                    if typed is None
                    else float(typed)
                )

            peaks, threshold = peak_indices(signal, chosen)

            if len(peaks):

                fig.add_trace(
                    go.Scattergl(
                        x=peaks,
                        y=signal.loc[peaks],
                        mode="markers",
                        name=f"Piikit ({len(peaks)})",
                        marker=dict(
                            color="red",
                            size=9,
                            symbol="diamond"
                        )
                    )
                )

            fig.add_hline(
                y=threshold,
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
