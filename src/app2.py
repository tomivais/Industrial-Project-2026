from pathlib import Path
import time

import duckdb
import numpy as np
import plotly.graph_objects as go
from scipy.signal import butter, find_peaks, sosfilt, sosfilt_zi
from shiny import render, ui
from shinywidgets import output_widget, render_widget


SAMPLE_RATE_HZ = 50_000
BLOCK_SIZE = SAMPLE_RATE_HZ
DEFAULT_START_SECONDS = 107.8
DEFAULT_WINDOW_SECONDS = 5.0
MAX_WINDOW_SECONDS = 5.0
WARMUP_SAMPLES = 5 * SAMPLE_RATE_HZ
LOOKAHEAD_SAMPLES = 2_500
PARQUET_PATH = Path(__file__).parent.parent / "data.parquet"


class DetectedPeak:
    def __init__(self, sample, amplitude, raw_value, baseline, threshold, prominence, width_samples):
        self.sample = sample
        self.amplitude = amplitude
        self.raw_value = raw_value
        self.baseline = baseline
        self.threshold = threshold
        self.prominence = prominence
        self.width_samples = width_samples


class StreamingPeakDetector:
    """projekti.py:n perustason seuranta ja muotopohjainen piikkien tunnistus."""

    def __init__(self, baseline_cutoff, min_distance, min_width, max_width):
        self.sos = butter(1, baseline_cutoff, fs=SAMPLE_RATE_HZ, output="sos")
        self.filter_state = None
        self.min_distance = min_distance
        self.min_width = min_width
        self.max_width = max_width
        self.last_peak_sample = -min_distance
        self.tail_residual = np.empty(0, dtype=np.float64)
        self.tail_baseline = np.empty(0, dtype=np.float64)
        self.tail_raw = np.empty(0, dtype=np.float64)

    def process(self, block, start_sample, min_amplitude, noise_multiplier, min_prominence):
        if self.filter_state is None:
            self.filter_state = sosfilt_zi(self.sos) * block[0]
        baseline, self.filter_state = sosfilt(self.sos, block, zi=self.filter_state)
        residual = block - baseline

        differences = np.diff(block)
        if differences.size:
            diff_mad = float(np.median(np.abs(differences - np.median(differences))))
            noise_sigma = 1.4826 * diff_mad / np.sqrt(2.0)
        else:
            noise_sigma = 0.0

        threshold = max(min_amplitude, noise_multiplier * noise_sigma)
        prominence = max(min_prominence, 2.5 * noise_sigma)
        combined_residual = np.concatenate((self.tail_residual, residual))
        combined_baseline = np.concatenate((self.tail_baseline, baseline))
        combined_raw = np.concatenate((self.tail_raw, block))
        global_offset = start_sample - self.tail_residual.size
        locations, properties = find_peaks(
            combined_residual,
            height=threshold,
            prominence=prominence,
            distance=self.min_distance,
            width=(self.min_width, self.max_width),
        )

        safe_end = start_sample + block.size - max(1, self.max_width // 2)
        peaks = []
        for index, location in enumerate(locations):
            sample_index = global_offset + int(location)
            if sample_index > safe_end:
                continue
            if sample_index - self.last_peak_sample < self.min_distance:
                continue
            self.last_peak_sample = sample_index
            peaks.append(
                DetectedPeak(
                    sample=sample_index,
                    amplitude=float(properties["peak_heights"][index]),
                    raw_value=float(combined_raw[location]),
                    baseline=float(combined_baseline[location]),
                    threshold=threshold,
                    prominence=float(properties["prominences"][index]),
                    width_samples=float(properties["widths"][index]),
                )
            )

        keep = min(self.max_width + self.min_distance, combined_residual.size)
        self.tail_residual = combined_residual[-keep:].copy()
        self.tail_baseline = combined_baseline[-keep:].copy()
        self.tail_raw = combined_raw[-keep:].copy()
        return peaks, baseline, threshold, noise_sigma


def quote_identifier(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def app2_ui():
    return ui.div(
        ui.busy_indicators.options(fade_opacity=1),
        ui.h3("Piikkien tunnistus mittausdatasta"),
        ui.p(
            "Piikkihaku käyttää projekti.py:n perustason seurantaa, "
            "kohinan arviota ja piikin muotoon perustuvia ehtoja. "
            "Kuvaaja näyttää valitun enintään viiden sekunnin jakson."
        ),
        ui.div(
            ui.input_numeric(
                "app2_start_seconds",
                "Alkuaika (s)",
                value=DEFAULT_START_SECONDS,
                min=0,
                step=1,
            ),
            ui.input_numeric(
                "app2_duration_seconds",
                "Jakson pituus (s)",
                value=DEFAULT_WINDOW_SECONDS,
                min=0.1,
                max=MAX_WINDOW_SECONDS,
                step=0.5,
            ),
            ui.input_numeric(
                "app2_min_amplitude",
                "Piikin vähimmäiskorkeus perustasosta",
                value=10,
                min=0,
                step=1,
            ),
            style="display: flex; gap: 20px; flex-wrap: wrap; align-items: end;",
        ),
        ui.output_text("app2_dataset_info"),
        ui.div(
            output_widget("app2_peak_plot"),
            class_="signal-plot-slot signal-plot-slot-detail",
        ),
    )


def app2_server(input, output, session):
    if not PARQUET_PATH.is_file():
        raise FileNotFoundError(f"Parquet-tiedostoa ei löydy: {PARQUET_PATH}")

    with duckdb.connect(database=":memory:") as metadata_connection:
        schema = metadata_connection.execute(
            "SELECT * FROM read_parquet(?) LIMIT 0", [str(PARQUET_PATH)]
        ).description
        columns = [column[0] for column in schema]
        signal_column = next(
            (column for column in columns if column.lower() not in {"row_id", "sample_id"}),
            None,
        )
        if signal_column is None:
            raise ValueError("Parquet-tiedostosta ei löytynyt mittaussaraketta.")
        total_samples = metadata_connection.execute(
            "SELECT COUNT(*) FROM read_parquet(?)", [str(PARQUET_PATH)]
        ).fetchone()[0]

    @render_widget
    def app2_peak_plot():
        start_seconds = max(0.0, float(input.app2_start_seconds() or 0.0))
        duration_seconds = float(input.app2_duration_seconds() or DEFAULT_WINDOW_SECONDS)
        duration_seconds = min(max(duration_seconds, 0.1), MAX_WINDOW_SECONDS)
        start_sample = min(int(start_seconds * SAMPLE_RATE_HZ), total_samples)
        end_sample = min(
            total_samples,
            start_sample + int(duration_seconds * SAMPLE_RATE_HZ),
        )
        if end_sample <= start_sample:
            fig = go.Figure()
            fig.add_annotation(text="Valitulla kohdalla ei ole näytteitä.", showarrow=False)
            return fig

        read_start = max(0, start_sample - WARMUP_SAMPLES)
        read_end = min(total_samples, end_sample + LOOKAHEAD_SAMPLES)
        detector = StreamingPeakDetector(
            baseline_cutoff=2.0,
            min_distance=500,
            min_width=100,
            max_width=5_000,
        )
        min_amplitude = max(0.0, float(input.app2_min_amplitude() or 0.0))
        signal_identifier = quote_identifier(signal_column)
        started = time.perf_counter()

        with duckdb.connect(database=":memory:") as connection:
            connection.execute("SET threads = 4")
            frame = connection.execute(
                f"""
                SELECT file_row_number AS sample_idx, {signal_identifier} AS signal_value
                FROM read_parquet(?, file_row_number = true)
                WHERE file_row_number >= ? AND file_row_number < ?
                ORDER BY file_row_number
                """,
                [str(PARQUET_PATH), read_start, read_end],
            ).fetchdf()

        if frame.empty:
            fig = go.Figure()
            fig.add_annotation(text="Valitulta alueelta ei löytynyt näytteitä.", showarrow=False)
            return fig

        values = frame["signal_value"].to_numpy(dtype=np.float64)
        sample_indices = frame["sample_idx"].to_numpy(dtype=np.int64)
        if not np.all(np.isfinite(values)):
            raise ValueError("Valitulla alueella on tyhjiä tai virheellisiä mittausarvoja.")

        times = []
        signal_values = []
        baseline_values = []
        threshold_values = []
        peak_times = []
        peak_values = []
        peak_details = []

        for offset in range(0, values.size, BLOCK_SIZE):
            block = values[offset:offset + BLOCK_SIZE]
            block_indices = sample_indices[offset:offset + BLOCK_SIZE]
            block_start = int(block_indices[0])
            peaks, baseline, threshold, _ = detector.process(
                block,
                block_start,
                min_amplitude,
                noise_multiplier=5.0,
                min_prominence=6.0,
            )

            visible_start = max(start_sample, block_start)
            visible_end = min(end_sample, block_start + block.size)
            if visible_end > visible_start:
                local_start = visible_start - block_start
                local_end = visible_end - block_start
                visible_indices = block_indices[local_start:local_end]
                visible_baseline = baseline[local_start:local_end]
                times.extend((visible_indices / SAMPLE_RATE_HZ).tolist())
                signal_values.extend(block[local_start:local_end].tolist())
                baseline_values.extend(visible_baseline.tolist())
                threshold_values.extend((visible_baseline + threshold).tolist())

            for peak in peaks:
                if start_sample <= peak.sample < end_sample:
                    peak_times.append(peak.sample / SAMPLE_RATE_HZ)
                    peak_values.append(peak.raw_value)
                    peak_details.append(
                        (
                            peak.amplitude,
                            peak.prominence,
                            peak.width_samples / SAMPLE_RATE_HZ * 1_000,
                        )
                    )

        elapsed = time.perf_counter() - started
        fig = go.Figure()
        fig.add_trace(
            go.Scattergl(
                x=times,
                y=signal_values,
                mode="lines",
                name="Mittauskanava 1",
                line=dict(color="navy", width=0.9),
            )
        )
        fig.add_trace(
            go.Scattergl(
                x=times,
                y=baseline_values,
                mode="lines",
                name="Seurattu perustaso",
                line=dict(color="green", width=1.2),
            )
        )
        fig.add_trace(
            go.Scattergl(
                x=times,
                y=threshold_values,
                mode="lines",
                name="Tunnistuskynnys",
                line=dict(color="darkorange", width=1, dash="dash"),
            )
        )
        fig.add_trace(
            go.Scattergl(
                x=peak_times,
                y=peak_values,
                mode="markers",
                name=f"Tunnistetut piikit ({len(peak_times)})",
                customdata=peak_details,
                hovertemplate=(
                    "Aika: %{x:.5f} s<br>Mittausarvo: %{y:.2f}<br>"
                    "Korkeus perustasosta: %{customdata[0]:.2f}<br>"
                    "Erottuvuus: %{customdata[1]:.2f}<br>"
                    "Leveys: %{customdata[2]:.2f} ms<extra></extra>"
                ),
                marker=dict(color="red", size=7),
            )
        )
        fig.update_layout(
            template="simple_white",
            height=600,
            hovermode="x unified",
            title=f"Tunnistetut piikit: {len(peak_times)} | käsittely {elapsed:.2f} s",
            xaxis_title="Aika (s)",
            yaxis_title="Mittausarvo",
            legend_title="Selite",
            uirevision="app2-detail",
        )
        fig.update_xaxes(range=[start_sample / SAMPLE_RATE_HZ, end_sample / SAMPLE_RATE_HZ])
        return fig

    @render.text
    def app2_dataset_info():
        start = max(0.0, float(input.app2_start_seconds() or 0.0))
        duration = min(
            max(float(input.app2_duration_seconds() or DEFAULT_WINDOW_SECONDS), 0.1),
            MAX_WINDOW_SECONDS,
        )
        return (
            f"Aineisto: {total_samples:,} näytettä ({total_samples / SAMPLE_RATE_HZ:.2f} s)\n"
            f"Tarkasteltava jakso: {start:.2f}–{start + duration:.2f} s"
        )
