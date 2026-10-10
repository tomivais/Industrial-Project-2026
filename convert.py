from pathlib import Path
import duckdb

# Etsitään CSV
root_dir = Path(__file__).parent

csv_files = list(
    root_dir.glob(
        "IDP_signal_example_1.csv"
    )
)

if not csv_files:
    print("CSV-tiedostoa ei löytynyt juuresta!")

else:

    csv_file = csv_files[0]
    parquet_file = root_dir / "data.parquet"

    print(f"Aloitetaan muunnos tiedostolle: {csv_file.name}...")

    duckdb.sql(
        f"""
        COPY (
            SELECT *
            FROM read_csv(
                '{csv_file}',
                AUTO_DETECT = TRUE,
                IGNORE_ERRORS = TRUE
            )
        )
        TO '{parquet_file}'
        (FORMAT PARQUET)
        """
    )

    print("Muunnos valmis!")
    print(f"Tallennettu: {parquet_file}")