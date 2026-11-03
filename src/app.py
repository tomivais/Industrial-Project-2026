"""Shiny view for streaming peak detection on a Parquet measurement file."""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import numpy as np
import plotly.graph_objects as go
from scipy.signal import butter, find_peaks, sosfilt, sosfilt_zi
from shiny.express import ui
from shinywidgets import render_plotly


SAMPLE_RATE_HZ = 50_000
BLOCK_SIZE = SAMPLE_RATE_HZ
# Keep the rendered graph compact for the 843-million-sample measurement file.
DISPLAY_BUCKET_SIZE = 5_000
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
    """Follow a slowly changing baseline and detect peaks in each block."""

    def __init__(self) -> None:
        self.sos = butter(1, 2.0, fs=SAMPLE_RATE_HZ, output="sos")
        self.filter_state = None
        self.min_distance = 500
        self.min_width = 100
        self.max_width = 5_000
        self.last_peak_sample = -self.min_distance
        self.tail_residual = np.empty(0, dtype=np.float64)
        self.tail_baseline = np.empty(0, dtype=np.float64)
        self.tail_raw = np.empty(0, dtype=np.float64)

    def process(self, block: np.ndarray, start_sample: int):
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
        threshold = max(10.0, 5.0 * noise_sigma)
        min_prominence = max(6.0, 2.5 * noise_sigma)

        combined_residual = np.concatenate((self.tail_residual, residual))
        combined_baseline = np.concatenate((self.tail_baseline, baseline))
        combined_raw = np.concatenate((self.tail_raw, block))
        global_offset = start_sample - self.tail_residual.size
        locations, properties = find_peaks(
            combined_residual,
            height=threshold,
            prominence=min_prominence,
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
        return peaks, baseline, threshold


def add_display_envelope(times, signal_values, baseline_values, threshold_values,
                         block, baseline, start_sample, threshold):
    """Keep each display bucket's min and max so narrow peaks remain visible."""
    for offset in range(0, block.size, DISPLAY_BUCKET_SIZE):
        end = min(offset + DISPLAY_BUCKET_SIZE, block.size)
        segment = block[offset:end]
        locations = {offset + int(np.argmin(segment)), offset + int(np.argmax(segment))}
        for index in sorted(locations):
            times.append((start_sample + index) / SAMPLE_RATE_HZ)
            signal_values.append(float(block[index]))
            baseline_values.append(float(baseline[index]))
            threshold_values.append(float(baseline[index] + threshold))


def process_parquet():
    """Read the first Parquet column in blocks and run the same peak detector."""
    if not PARQUET_PATH.is_file():
        raise FileNotFoundError(
            f"Parquet-tiedostoa ei löydy: {PARQUET_PATH}. "
            "Lisää mittaus-CSV sovelluksen juureen ja suorita main.py."
        )

    detector = StreamingPeakDetector()
    times = []
    signal_values = []
    baseline_values = []
    threshold_values = []
    peak_times = []
    peak_values = []
    peak_details = []
    total_samples = 0
    started = time.perf_counter()

    with duckdb.connect() as connection:
        schema = connection.execute(
            "SELECT * FROM read_parquet(?) LIMIT 0", [str(PARQUET_PATH)]
        )
        if not schema.description:
            raise ValueError("Parquet-tiedostossa ei ole mittaussarakkeita.")
        first_column = schema.description[0][0].replace('"', '""')
        cursor = connection.execute(
            f'SELECT "{first_column}" FROM read_parquet(?)', [str(PARQUET_PATH)]
        )

        while True:
            rows = cursor.fetchmany(BLOCK_SIZE)
            if not rows:
                break
            try:
                block = np.fromiter((row[0] for row in rows), dtype=np.float64, count=len(rows))
            except (TypeError, ValueError) as exc:
                raise ValueError("Mittausdatassa on tyhjiä tai ei-numeerisia arvoja.") from exc
            if not np.all(np.isfinite(block)):
                raise ValueError("Mittausdatassa on tyhjiä tai virheellisiä arvoja.")

            peaks, baseline, threshold = detector.process(block, total_samples)
            add_display_envelope(
                times,
                signal_values,
                baseline_values,
                threshold_values,
                block,
                baseline,
                total_samples,
                threshold,
            )
            for peak in peaks:
                peak_time = peak.sample / SAMPLE_RATE_HZ
                peak_times.append(peak_time)
                peak_values.append(peak.raw_value)
                peak_details.append(
                    (
                        peak.amplitude,
                        peak.prominence,
                        peak.width_samples / SAMPLE_RATE_HZ * 1000.0,
                    )
                )
            total_samples += block.size

    elapsed = time.perf_counter() - started
    return (
        times,
        signal_values,
        baseline_values,
        threshold_values,
        peak_times,
        peak_values,
        peak_details,
        total_samples,
        elapsed,
    )


ui.page_opts(title="Signaalianalyysi")
ui.h3("Piikkitunnistus Parquet-mittausdatasta")
ui.p(
    "Parquet-data luetaan 50 000 näytteen lohkoissa. "
    "Kuvaajassa näkyvät mittaussignaali, seurattu perustaso, tunnistetut piikit ja niiden kynnys."
)


@render_plotly
def signal_plot():
    (
        times,
        signal_values,
        baseline_values,
        threshold_values,
        peak_times,
        peak_values,
        peak_details,
        total_samples,
        elapsed,
    ) = process_parquet()

    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=times,
            y=signal_values,
            mode="lines",
            name="Mittauskanava 1",
            line=dict(color="navy", width=1),
        )
    )
    fig.add_trace(
        go.Scattergl(
            x=times,
            y=baseline_values,
            mode="lines",
            name="Seurattu perustaso",
            line=dict(color="green", width=1.5),
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
                "Aika: %{x:.5f} s<br>"
                "Mittausarvo: %{y:.2f}<br>"
                "Korkeus perustasosta: %{customdata[0]:.2f}<br>"
                "Erottuvuus: %{customdata[1]:.2f}<br>"
                "Leveys: %{customdata[2]:.2f} ms<extra></extra>"
            ),
            marker=dict(color="red", size=7),
        )
    )
    fig.update_layout(
        template="simple_white",
        hovermode="x unified",
        height=700,
        title=(
            f"Piikkitunnistus: {len(peak_times)} piikkiä | "
            f"{total_samples:,} näytettä | laskenta {elapsed:.2f} s"
        ),
        xaxis_title="Aika (s)",
        yaxis_title="Mittausarvo",
        legend_title="Selite",
    )
    return fig
