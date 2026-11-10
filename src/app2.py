from pathlib import Path
import math
import re
import shutil
import tempfile
import time

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from scipy.signal import butter, find_peaks, sosfilt, sosfilt_zi
from shiny import reactive, render, ui
from shinywidgets import output_widget, render_widget


SAMPLE_RATE_HZ = 50_000
BLOCK_SIZE = SAMPLE_RATE_HZ
DEFAULT_START_SECONDS = 0.0
DEFAULT_WINDOW_SECONDS = 5.0
MAX_PEAK_WIDTH_SAMPLES = 20_000  # 400 ms; includes the wider pulses visible in the supplied plot.
# The 2 Hz first-order baseline filter settles in well under one second.
# One second of context warms it up without rereading five seconds per view.
WARMUP_SAMPLES = SAMPLE_RATE_HZ
LOOKAHEAD_SAMPLES = MAX_PEAK_WIDTH_SAMPLES // 2
MAX_DETAIL_POINTS = 10_000
MAX_OVERVIEW_POINTS = 8_000
SMOOTH_WINDOW = 20
# Tuned against the 15-event high, middle, and low reference sections.
MIN_PEAK_DISTANCE_SAMPLES = int(0.30 * SAMPLE_RATE_HZ)
DEFAULT_MIN_PEAK_HEIGHT = 35.0
PARQUET_PATH = Path(__file__).resolve().parent.parent / "data.parquet"


class DetectedPeak:
    def __init__(self, sample, amplitude, raw_value, baseline, prominence, width_samples):
        self.sample = sample
        self.amplitude = amplitude
        self.raw_value = raw_value
        self.baseline = baseline
        self.prominence = prominence
        self.width_samples = width_samples


class StreamingPeakDetector:
    """Causal baseline filter and shape-based peak detection, with state across blocks."""

    def __init__(self, baseline_cutoff=2.0, min_distance=MIN_PEAK_DISTANCE_SAMPLES, min_width=100, max_width=MAX_PEAK_WIDTH_SAMPLES):
        self.sos = butter(1, baseline_cutoff, fs=SAMPLE_RATE_HZ, output="sos")
        self.filter_state = None
        self.min_distance = min_distance
        self.min_width = min_width
        self.max_width = max_width
        self.last_peak_sample = -min_distance
        self.tail_residual = np.empty(0, dtype=np.float64)
        self.tail_baseline = np.empty(0, dtype=np.float64)
        self.tail_raw = np.empty(0, dtype=np.float64)
        self.smoothing_tail = np.empty(0, dtype=np.float64)

    def process(self, block, start_sample, min_amplitude, detect_from=0, smooth=False):
        if block.size == 0:
            return [], np.empty(0), 0.0, block
        if smooth:
            # Causal 20-sample moving mean; carry history across data blocks.
            joined = np.concatenate((self.smoothing_tail, block))
            cumulative = np.concatenate(([0.0], np.cumsum(joined, dtype=np.float64)))
            ends = self.smoothing_tail.size + np.arange(1, block.size + 1)
            starts = np.maximum(0, ends - SMOOTH_WINDOW)
            processed = (cumulative[ends] - cumulative[starts]) / (ends - starts)
            self.smoothing_tail = joined[-(SMOOTH_WINDOW - 1):].copy()
        else:
            processed = block
            self.smoothing_tail = np.empty(0, dtype=np.float64)
        if self.filter_state is None:
            self.filter_state = sosfilt_zi(self.sos) * processed[0]
        baseline, self.filter_state = sosfilt(self.sos, processed, zi=self.filter_state)
        residual = processed - baseline

        detect_active = start_sample + block.size > detect_from
        if detect_active:
            differences = np.diff(processed)
            if differences.size:
                median_difference = np.median(differences)
                diff_mad = float(np.median(np.abs(differences - median_difference)))
                noise_sigma = 1.4826 * diff_mad / np.sqrt(2.0)
            else:
                noise_sigma = 0.0
        else:
            # No threshold is used before detect_from; skip two median calculations there.
            noise_sigma = 0.0
        threshold = max(min_amplitude, 5.0 * noise_sigma)
        min_prominence = max(6.0, 2.5 * noise_sigma)

        combined_residual = np.concatenate((self.tail_residual, residual))
        combined_baseline = np.concatenate((self.tail_baseline, baseline))
        combined_raw = np.concatenate((self.tail_raw, processed))
        global_offset = start_sample - self.tail_residual.size
        peaks = []

        # Warm-up samples update the baseline filter without spending time on peak search.
        if detect_active:
            locations, properties = find_peaks(
                combined_residual,
                height=threshold,
                prominence=min_prominence,
                distance=self.min_distance,
                width=(self.min_width, self.max_width),
            )
            safe_end = start_sample + block.size - max(1, self.max_width // 2)
            for index, location in enumerate(locations):
                sample_index = global_offset + int(location)
                if sample_index < detect_from or sample_index > safe_end:
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
                        prominence=float(properties["prominences"][index]),
                        width_samples=float(properties["widths"][index]),
                    )
                )

        keep = min(self.max_width + self.min_distance, combined_residual.size)
        self.tail_residual = combined_residual[-keep:].copy()
        self.tail_baseline = combined_baseline[-keep:].copy()
        self.tail_raw = combined_raw[-keep:].copy()
        return peaks, baseline, threshold, processed


def quote_identifier(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def finite_float(value, default):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def selected_duration_seconds(value, total_samples):
    data_duration = max(0.1, total_samples / SAMPLE_RATE_HZ)
    return min(max(finite_float(value, DEFAULT_WINDOW_SECONDS), 0.1), data_duration)


def get_dataset_info(path):
    """Read schema and Parquet row count from file metadata, without scanning all samples."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Parquet-tiedostoa ei löydy: {path}")
    with duckdb.connect(database=":memory:") as con:
        description = con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
        ).fetchall()
        columns = [(row[0], row[1].upper()) for row in description]
        count_row = con.execute(
            "SELECT num_rows FROM parquet_file_metadata(?)", [str(path)]
        ).fetchone()
    if not count_row or count_row[0] is None:
        raise ValueError("Parquet-tiedoston rivimäärää ei saatu luettua.")
    numeric_types = (
        "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT",
        "USMALLINT", "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL",
    )
    numeric_columns = [
        (name, re.sub(r"[^a-z0-9]", "", name.lower()))
        for name, kind in columns
        if any(kind.startswith(number_type) for number_type in numeric_types)
    ]
    excluded_names = {
        "rowid", "sampleid", "index", "sample", "samplenumber", "sampleno",
        "sampleindex", "row", "rownumber", "rowindex", "time", "times",
        "timestamp", "datetime", "date", "seconds", "second",
    }
    signal_candidates = [
        (name, normalized)
        for name, normalized in numeric_columns
        if normalized not in excluded_names
        and not normalized.startswith(("time", "timestamp", "datetime", "elapsedtime"))
        and not normalized.endswith(("timestamp", "time"))
    ]
    preferred_tokens = ("signal", "measurement", "channel", "sensor", "amplitude", "value")
    signal_column = next(
        (name for name, normalized in signal_candidates if any(token in normalized for token in preferred_tokens)),
        signal_candidates[0][0] if signal_candidates else None,
    )
    if signal_column is None:
        raise ValueError("Tiedostosta ei löytynyt numeerista mittaussaraketta.")
    return {"path": path, "signal_column": signal_column, "total_samples": int(count_row[0])}


def read_blocks(dataset, start_sample, end_sample, block_size=BLOCK_SIZE):
    """Yield bounded NumPy arrays; never materialize a whole long interval as a DataFrame."""
    if end_sample <= start_sample:
        return
    signal_column = quote_identifier(dataset["signal_column"])
    with duckdb.connect(database=":memory:") as con:
        con.execute("SET threads = 4")
        cursor = con.execute(
            f"""
            SELECT file_row_number AS sample_idx, {signal_column} AS signal_value
            FROM read_parquet(?, file_row_number = true)
            WHERE file_row_number >= ? AND file_row_number < ?
            ORDER BY file_row_number
            """,
            [str(dataset["path"]), int(start_sample), int(end_sample)],
        )
        while True:
            rows = cursor.fetchmany(block_size)
            if not rows:
                break
            indices = np.fromiter((row[0] for row in rows), dtype=np.int64, count=len(rows))
            values = np.fromiter((row[1] for row in rows), dtype=np.float64, count=len(rows))
            if not np.all(np.isfinite(values)):
                raise ValueError("Valitulla alueella on tyhjiä tai virheellisiä mittausarvoja.")
            yield indices, values


def plot_indices(length, max_points):
    """Return bin minimum/maximum indices so narrow peaks remain visible after decimation."""
    if length <= max_points:
        return np.arange(length, dtype=np.int64)
    bins = max(1, max_points // 2)
    edges = np.linspace(0, length, bins + 1, dtype=np.int64)
    return edges


def minmax_sample(indices, values, max_points):
    if len(values) <= max_points:
        return indices, values
    edges = plot_indices(len(values), max_points)
    out_indices = []
    out_values = []
    for left, right in zip(edges[:-1], edges[1:]):
        if right <= left:
            continue
        segment = values[left:right]
        lo = left + int(np.argmin(segment))
        hi = left + int(np.argmax(segment))
        for point in sorted({lo, hi}):
            out_indices.append(indices[point])
            out_values.append(values[point])
    return np.asarray(out_indices), np.asarray(out_values)


class SignalPlotBuffer:
    """Keep min/max plot points per time bin; memory use stays bounded for long windows."""

    def __init__(self, start_sample, span_samples, max_points=MAX_DETAIL_POINTS):
        self.start_sample = int(start_sample)
        self.bin_width = max(1, math.ceil(max(1, int(span_samples)) / max(1, max_points // 2)))
        self.bins = {}

    @staticmethod
    def _record(sample, signal, baseline, threshold):
        return (int(sample), float(signal), float(baseline), float(threshold))

    def add(self, indices, signal, baseline, threshold):
        if len(indices) == 0:
            return
        bucket_ids = (indices - self.start_sample) // self.bin_width
        boundaries = np.r_[0, np.flatnonzero(np.diff(bucket_ids)) + 1, len(indices)]
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            bucket = int(bucket_ids[left])
            segment = signal[left:right]
            low_pos = left + int(np.argmin(segment))
            high_pos = left + int(np.argmax(segment))
            low = self._record(indices[low_pos], signal[low_pos], baseline[low_pos], threshold[low_pos])
            high = self._record(indices[high_pos], signal[high_pos], baseline[high_pos], threshold[high_pos])
            current = self.bins.get(bucket)
            if current is None:
                self.bins[bucket] = [low, high]
            else:
                if low[1] < current[0][1]:
                    current[0] = low
                if high[1] > current[1][1]:
                    current[1] = high

    def trim_before(self, sample):
        first_bucket = (int(sample) - self.start_sample) // self.bin_width
        for bucket in tuple(self.bins):
            if bucket < first_bucket:
                del self.bins[bucket]

    def arrays(self):
        records = []
        for bucket in sorted(self.bins):
            low, high = self.bins[bucket]
            records.extend((low, high) if low[0] <= high[0] else (high, low))
        if not records:
            empty = np.empty(0, dtype=np.float64)
            return np.empty(0, dtype=np.int64), empty, empty, empty
        return (
            np.fromiter((row[0] for row in records), dtype=np.int64),
            np.fromiter((row[1] for row in records), dtype=np.float64),
            np.fromiter((row[2] for row in records), dtype=np.float64),
            np.fromiter((row[3] for row in records), dtype=np.float64),
        )


def app2_ui():
    return ui.div(
        ui.busy_indicators.options(fade_opacity=1),
        ui.h3("Piikkien tunnistus mittausdatasta"),
        ui.p(
            "Analysointi lukee dataa osissa, joten pitkää aineistoa ei ladata kokonaan muistiin. "
            "CSV muunnetaan tämän istunnon Parquet-tiedostoksi. Kohinanpoiston valinta vaikuttaa "
            "sekä kuvaajaan että piikkien tunnistukseen."
        ),
        ui.tags.div(
            ui.input_file(
                "app2_csv_upload",
                "Pudota CSV-tiedosto tähän tai selaa tiedostoja",
                accept=[".csv", "text/csv"],
                multiple=False,
                button_label="Valitse CSV",
                placeholder="CSV-tiedostoa ei ole valittu",
            ),
            id="app2-drop-zone",
            class_="app2-drop-zone",
        ),
        ui.tags.script("""
            (() => {
              const zone = document.getElementById('app2-drop-zone');
              if (!zone || zone.dataset.dropReady) return;
              zone.dataset.dropReady = 'true';
              zone.addEventListener('dragover', (event) => {
                event.preventDefault(); zone.classList.add('app2-drag-over');
              });
              zone.addEventListener('dragleave', () => zone.classList.remove('app2-drag-over'));
              zone.addEventListener('drop', (event) => {
                event.preventDefault(); zone.classList.remove('app2-drag-over');
                const input = document.getElementById('app2_csv_upload');
                if (!input || !event.dataTransfer.files.length) return;
                input.files = event.dataTransfer.files;
                input.dispatchEvent(new Event('change', { bubbles: true }));
              });
              document.addEventListener('keydown', (event) => {
                if (event.altKey || event.ctrlKey || event.metaKey) return;
                if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
                if (event.key === 'ArrowLeft') document.getElementById('app2_previous')?.click();
                if (event.key === 'ArrowRight') document.getElementById('app2_next')?.click();
              });
            })();
        """),
        ui.output_text("app2_upload_status"),
        ui.div(
            ui.input_action_button("app2_previous", "← Edellinen jakso"),
            ui.input_numeric(
                "app2_start_seconds", "Alkuaika (s)", DEFAULT_START_SECONDS, min=0, step=1
            ),
            ui.input_action_button("app2_next", "Seuraava jakso →"),
            ui.input_numeric(
                "app2_duration_seconds", "Jakson pituus (s)", DEFAULT_WINDOW_SECONDS,
                min=0.1, step=0.5,
            ),
            ui.input_numeric(
                "app2_min_amplitude", "Piikin vähimmäiskorkeus perustasosta",
                DEFAULT_MIN_PEAK_HEIGHT, min=0, step=1,
            ),
            style="display: flex; gap: 12px; flex-wrap: wrap; align-items: end;",
        ),
        ui.div(
            ui.input_checkbox("app2_show_overview", "Näytä koko signaali yleiskuvassa"),
        ),
        ui.div(
            ui.input_checkbox(
                "app2_denoise",
                "Kohinanpoisto: liukuva keskiarvo (20 näytettä)",
                value=True,
            ),
            style="margin-top: 8px;",
        ),
        ui.output_text("app2_dataset_info"),
        ui.div(
            ui.h4("Analyysin yhteenveto"),
            ui.output_ui("app2_summary"),
            class_="app2-summary-panel",
        ),
        ui.div(
            output_widget("app2_overview_plot"),
            class_="signal-plot-slot signal-plot-slot-overview",
        ),
        ui.div(
            output_widget("app2_peak_plot"),
            class_="signal-plot-slot signal-plot-slot-detail",
        ),
        ui.h4("Löydetyt piikit"),
        ui.output_data_frame("app2_peak_table"),
    )


def app2_server(input, output, session):
    session_dir = Path(tempfile.mkdtemp(prefix="industrial-app2-"))
    session.on_ended(lambda: shutil.rmtree(session_dir, ignore_errors=True))
    current_path = reactive.Value(PARQUET_PATH if PARQUET_PATH.is_file() else None)
    upload_message = reactive.Value(
        "Käytössä on projektin oletusaineisto." if PARQUET_PATH.is_file()
        else "Valitse CSV-tiedosto tai lisää data.parquet projektin juureen."
    )
    @reactive.effect
    @reactive.event(input.app2_csv_upload)
    def _convert_uploaded_csv():
        files = input.app2_csv_upload()
        if not files:
            return
        source = Path(files[0]["datapath"])
        destination = session_dir / "uploaded_signal.parquet"
        safe_destination = str(destination).replace("'", "''")
        try:
            destination.unlink(missing_ok=True)
            with duckdb.connect(database=":memory:") as con:
                con.execute("SET preserve_insertion_order = true")
                con.execute(
                    f"""
                    COPY (
                        SELECT ROW_NUMBER() OVER () - 1 AS row_id, *
                        FROM read_csv(?, auto_detect = true, ignore_errors = false)
                    ) TO '{safe_destination}' (FORMAT PARQUET, COMPRESSION ZSTD)
                    """,
                    [str(source)],
                )
            info = get_dataset_info(destination)
            current_path.set(destination)
            upload_message.set(
                f"CSV muunnettu Parquet-muotoon: {info['total_samples']:,} näytettä."
            )
            duration = selected_duration_seconds(input.app2_duration_seconds(), info["total_samples"])
            start = max(0.0, finite_float(input.app2_start_seconds(), DEFAULT_START_SECONDS))
            max_start = max(0.0, info["total_samples"] / SAMPLE_RATE_HZ - duration)
            ui.update_numeric("app2_start_seconds", value=min(start, max_start), session=session)
        except Exception as exc:
            upload_message.set(f"CSV:n muunnos epäonnistui: {exc}")

    def dataset():
        path = current_path.get()
        if path is None:
            return None
        return get_dataset_info(path)

    @reactive.effect
    @reactive.event(input.app2_previous)
    def _go_to_previous_period():
        length = finite_float(input.app2_duration_seconds(), DEFAULT_WINDOW_SECONDS)
        start = finite_float(input.app2_start_seconds(), 0.0)
        ui.update_numeric("app2_start_seconds", value=max(0.0, start - length), session=session)

    @reactive.effect
    @reactive.event(input.app2_next)
    def _go_to_next_period():
        length = finite_float(input.app2_duration_seconds(), DEFAULT_WINDOW_SECONDS)
        start = finite_float(input.app2_start_seconds(), 0.0)
        info = dataset()
        max_start = max(0.0, info["total_samples"] / SAMPLE_RATE_HZ - length) if info else start + length
        ui.update_numeric("app2_start_seconds", value=min(max_start, start + length), session=session)

    @render.text
    def app2_upload_status():
        return upload_message.get()

    @render.text
    def app2_dataset_info():
        info = dataset()
        if info is None:
            return "Aineistoa ei ole vielä ladattu."
        start = min(
            max(0.0, finite_float(input.app2_start_seconds(), DEFAULT_START_SECONDS)),
            info["total_samples"] / SAMPLE_RATE_HZ,
        )
        duration = selected_duration_seconds(input.app2_duration_seconds(), info["total_samples"])
        end = info["total_samples"] / SAMPLE_RATE_HZ
        return (
            f"Aineisto: {info['total_samples']:,} näytettä ({end:.2f} s) | "
            f"Näytteenottotaajuus: {SAMPLE_RATE_HZ:,} näytettä/s\n"
            f"Tarkasteltava jakso: {start:.2f}–{min(start + duration, end):.2f} s"
        )

    @render_widget
    def app2_overview_plot():
        if not input.app2_show_overview():
            return go.Figure()
        info = dataset()
        if info is None:
            fig = go.Figure()
            fig.add_annotation(text="Lataa aineisto nähdäksesi yleiskuvan.", showarrow=False)
            return fig
        started = time.perf_counter()
        bucket_size = max(1, math.ceil(info["total_samples"] / MAX_OVERVIEW_POINTS))
        column = quote_identifier(info["signal_column"])
        with duckdb.connect(database=":memory:") as con:
            frame = con.execute(
                f"""
                SELECT CAST(FLOOR(file_row_number::DOUBLE / ?) AS BIGINT) AS bucket,
                       ARG_MIN(file_row_number, {column}) AS min_sample,
                       MIN({column}) AS min_value,
                       ARG_MAX(file_row_number, {column}) AS max_sample,
                       MAX({column}) AS max_value
                FROM read_parquet(?, file_row_number = true)
                GROUP BY bucket ORDER BY bucket
                """,
                [bucket_size, str(info["path"])],
            ).fetch_df()
        overview_samples = np.column_stack((
            frame["min_sample"].to_numpy(), frame["max_sample"].to_numpy()
        )).reshape(-1)
        overview_values = np.column_stack((
            frame["min_value"].to_numpy(), frame["max_value"].to_numpy()
        )).reshape(-1)
        order = np.argsort(overview_samples)
        fig = go.Figure()
        fig.add_trace(go.Scattergl(
            x=overview_samples[order] / SAMPLE_RATE_HZ,
            y=overview_values[order], mode="lines", name="Koko signaali",
            line=dict(color="navy", width=1),
        ))
        start = min(
            max(0.0, finite_float(input.app2_start_seconds(), 0.0)),
            info["total_samples"] / SAMPLE_RATE_HZ,
        )
        duration = selected_duration_seconds(input.app2_duration_seconds(), info["total_samples"])
        fig.add_vrect(x0=start, x1=start + duration, fillcolor="orange", opacity=0.2, line_width=0)
        fig.update_layout(
            template="simple_white", height=320,
            title=f"Koko signaali ({len(overview_samples):,} minimi-/maksimipistettä, {time.perf_counter() - started:.2f} s)",
            xaxis_title="Aika (s)", yaxis_title="Mittausarvo", uirevision="app2-overview",
        )
        return fig

    @reactive.calc
    def app2_analysis():
        info = dataset()
        if info is None:
            return None
        total_samples = info["total_samples"]
        duration = selected_duration_seconds(input.app2_duration_seconds(), total_samples)
        duration_samples = max(1, int(duration * SAMPLE_RATE_HZ))
        min_amplitude = max(0.0, finite_float(input.app2_min_amplitude(), DEFAULT_MIN_PEAK_HEIGHT))
        use_smoothing = bool(input.app2_denoise())
        start_seconds = min(
            max(0.0, finite_float(input.app2_start_seconds(), DEFAULT_START_SECONDS)),
            total_samples / SAMPLE_RATE_HZ,
        )
        start_sample = int(start_seconds * SAMPLE_RATE_HZ)
        read_start = max(0, start_sample - WARMUP_SAMPLES)
        read_end = min(total_samples, start_sample + int(duration * SAMPLE_RATE_HZ) + LOOKAHEAD_SAMPLES)
        detector = StreamingPeakDetector()
        plot_buffer = SignalPlotBuffer(start_sample, duration_samples)
        selected_peaks = []
        started = time.perf_counter()
        for indices, values in read_blocks(info, read_start, read_end):
            block_start = int(indices[0])
            peaks, baseline, threshold, processed = detector.process(
                values, block_start, min_amplitude,
                detect_from=start_sample, smooth=use_smoothing,
            )
            visible = (indices >= start_sample) & (indices < min(total_samples, start_sample + int(duration * SAMPLE_RATE_HZ)))
            if np.any(visible):
                plot_buffer.add(
                    indices[visible], processed[visible], baseline[visible],
                    baseline[visible] + threshold,
                )
            selected_peaks.extend(
                peak for peak in peaks
                if start_sample <= peak.sample < start_sample + int(duration * SAMPLE_RATE_HZ)
            )
        elapsed = time.perf_counter() - started
        plot_samples, signal_values, baseline_values, threshold_values = plot_buffer.arrays()
        times = plot_samples / SAMPLE_RATE_HZ
        display_end_sample = min(total_samples, start_sample + int(duration * SAMPLE_RATE_HZ))
        return {
            "info": info,
            "start_sample": start_sample,
            "display_end_sample": display_end_sample,
            "duration": duration,
            "elapsed": elapsed,
            "min_amplitude": min_amplitude,
            "use_smoothing": use_smoothing,
            "selected_peaks": selected_peaks,
            "times": times,
            "signal_values": signal_values,
            "baseline_values": baseline_values,
            "threshold_values": threshold_values,
        }

    @render.ui
    def app2_summary():
        result = app2_analysis()
        if result is None:
            return ui.tags.p("Lataa aineisto nähdäksesi analyysin yhteenvedon.")
        peaks = result["selected_peaks"]
        reference_height = max(1.0, result["min_amplitude"])
        small_limit = 2.0 * reference_height
        large_limit = 5.0 * reference_height
        sizes = [peak.amplitude for peak in peaks]
        small_count = sum(height < small_limit for height in sizes)
        medium_count = sum(small_limit <= height < large_limit for height in sizes)
        large_count = sum(height >= large_limit for height in sizes)
        total_seconds = result["info"]["total_samples"] / SAMPLE_RATE_HZ
        start_seconds = result["start_sample"] / SAMPLE_RATE_HZ
        end_seconds = result["display_end_sample"] / SAMPLE_RATE_HZ
        return ui.tags.div(
            ui.tags.div(
                ui.tags.div("Aineiston kesto", class_="app2-summary-label"),
                ui.tags.strong(f"{total_seconds:.2f} s"),
                ui.tags.small(f"{result['info']['total_samples']:,} näytettä"),
                class_="app2-summary-card",
            ),
            ui.tags.div(
                ui.tags.div("Analysoitu jakso", class_="app2-summary-label"),
                ui.tags.strong(f"{start_seconds:.2f}–{end_seconds:.2f} s"),
                ui.tags.small(f"Käsittely {result['elapsed']:.2f} s"),
                class_="app2-summary-card",
            ),
            ui.tags.div(
                ui.tags.div("Löydettyjä piikkejä", class_="app2-summary-label"),
                ui.tags.strong(str(len(peaks))),
                ui.tags.small("Valitulla jaksolla"),
                class_="app2-summary-card",
            ),
            ui.tags.div(
                ui.tags.div("Pieniä", class_="app2-summary-label"),
                ui.tags.strong(str(small_count)),
                ui.tags.small(f"Korkeus alle {small_limit:g}"),
                class_="app2-summary-card app2-summary-small",
            ),
            ui.tags.div(
                ui.tags.div("Keskikokoisia", class_="app2-summary-label"),
                ui.tags.strong(str(medium_count)),
                ui.tags.small(f"Korkeus {small_limit:g}–alle {large_limit:g}"),
                class_="app2-summary-card app2-summary-medium",
            ),
            ui.tags.div(
                ui.tags.div("Suuria", class_="app2-summary-label"),
                ui.tags.strong(str(large_count)),
                ui.tags.small(f"Korkeus vähintään {large_limit:g}"),
                class_="app2-summary-card app2-summary-large",
            ),
            class_="app2-summary-grid",
        )

    @render.data_frame
    def app2_peak_table():
        result = app2_analysis()
        columns = ["Aika (s)", "Kokoluokka", "Korkeus perustasosta", "Mittausarvo", "Erottuvuus", "Leveys (ms)"]
        if result is None:
            return render.DataGrid(pd.DataFrame(columns=columns), filters=False, height="420px")
        reference_height = max(1.0, result["min_amplitude"])
        small_limit = 2.0 * reference_height
        large_limit = 5.0 * reference_height
        rows = []
        for peak in result["selected_peaks"]:
            if peak.amplitude < small_limit:
                size = "Pieni"
            elif peak.amplitude < large_limit:
                size = "Keskikokoinen"
            else:
                size = "Suuri"
            rows.append({
                "Aika (s)": peak.sample / SAMPLE_RATE_HZ,
                "Kokoluokka": size,
                "Korkeus perustasosta": peak.amplitude,
                "Mittausarvo": peak.raw_value,
                "Erottuvuus": peak.prominence,
                "Leveys (ms)": peak.width_samples / SAMPLE_RATE_HZ * 1_000,
            })
        return render.DataGrid(pd.DataFrame(rows, columns=columns), filters=False, height="420px")

    @render_widget
    def app2_peak_plot():
        result = app2_analysis()
        if result is None:
            fig = go.Figure()
            fig.add_annotation(text="Lataa Parquet- tai CSV-aineisto aloittaaksesi.", showarrow=False)
            return fig
        selected_peaks = result["selected_peaks"]
        times = result["times"]
        signal_values = result["signal_values"]
        baseline_values = result["baseline_values"]
        threshold_values = result["threshold_values"]
        start_sample = result["start_sample"]
        display_end_sample = result["display_end_sample"]
        use_smoothing = result["use_smoothing"]
        title_prefix = f"Käsittely {result['elapsed']:.2f} s"
        fig = go.Figure()
        if len(times):
            display_indices, display_signal = minmax_sample(times, signal_values, MAX_DETAIL_POINTS)
            # Reuse the preserved signal sample indices to draw matching baseline and threshold values.
            nearest = np.searchsorted(times, display_indices)
            fig.add_trace(go.Scattergl(
                x=display_indices, y=display_signal, mode="lines",
                name="Tasoitettu signaali" if use_smoothing else "Mittauskanava 1",
                line=dict(color="#8A00FF" if use_smoothing else "navy", width=0.9),
            ))
            fig.add_trace(go.Scattergl(
                x=display_indices, y=baseline_values[nearest], mode="lines", name="Seurattu perustaso",
                line=dict(color="green", width=1.2),
            ))
            fig.add_trace(go.Scattergl(
                x=display_indices, y=threshold_values[nearest], mode="lines", name="Tunnistuskynnys",
                line=dict(color="darkorange", width=1, dash="dash"),
            ))
        peak_times = [peak.sample / SAMPLE_RATE_HZ for peak in selected_peaks]
        peak_values = [peak.raw_value for peak in selected_peaks]
        peak_details = [
            (peak.amplitude, peak.prominence, peak.width_samples / SAMPLE_RATE_HZ * 1_000)
            for peak in selected_peaks
        ]
        if peak_times:
            fig.add_trace(go.Scattergl(
                x=peak_times, y=peak_values, mode="markers",
                name=f"Tunnistetut piikit ({len(peak_times)})", customdata=peak_details,
                hovertemplate=(
                    "Aika: %{x:.5f} s<br>Mittausarvo: %{y:.2f}<br>"
                    "Korkeus perustasosta: %{customdata[0]:.2f}<br>"
                    "Erottuvuus: %{customdata[1]:.2f}<br>"
                    "Leveys: %{customdata[2]:.2f} ms<extra></extra>"
                ),
                marker=dict(color="red", size=7),
            ))
        view_end = display_end_sample / SAMPLE_RATE_HZ
        view_start = start_sample / SAMPLE_RATE_HZ
        fig.update_layout(
            template="simple_white", height=600, hovermode="x unified",
            title=f"{title_prefix} | tunnistetut piikit: {len(peak_times)}",
            xaxis_title="Aika (s)", yaxis_title="Mittausarvo", legend_title="Selite",
            uirevision="app2-detail",
        )
        fig.update_xaxes(range=[view_start, max(view_start + 0.001, view_end)])
        return fig
