
from pathlib import Path
import duckdb

root_dir = Path(__file__).parent

csv_file = root_dir / "1. LONG_suction_HIGH_CONC_4ml_(and_3_times_resuction).csv"
parquet_file = root_dir / "data.parquet"


if not csv_file.is_file():
    raise FileNotFoundError(
        f"CSV-tiedostoa ei löytynyt: {csv_file}"
    )


try:
    with duckdb.connect() as con:

        # Säilytetään CSV:n alkuperäinen rivijärjestys.
        con.execute(
            "SET preserve_insertion_order = true"
        )

        print(f"Luetaan tiedostoa: {csv_file.name}")

        # Luetaan CSV ja lisätään pysyvä havaintonumero.
        con.execute(
            """
            CREATE TEMP TABLE converted AS
            SELECT
                ROW_NUMBER() OVER () - 1 AS row_id,
                *
            FROM read_csv(
                ?,
                auto_detect = true,
                ignore_errors = false
            )
            """,
            [str(csv_file)]
        )

        # Tarkistetaan alkuperäinen data.
        csv_data = con.table("converted")

        csv_rows = csv_data.count("*").fetchone()[0]
        csv_columns = csv_data.columns

        print(f"CSV-rivejä: {csv_rows:,}")
        print(f"CSV-sarakkeita: {len(csv_columns) - 1}")

        print("\nLisätty sarake: row_id")
        print(f"Havaintoväli: 0 - {csv_rows - 1:,}")

        # Poistetaan vanha Parquet ennen uudelleenluontia.
        if parquet_file.exists():
            parquet_file.unlink()

        # Tallennetaan Parquet-muotoon.
        csv_data.write_parquet(
            str(parquet_file)
        )

        print("\nParquet-tiedosto tallennettu.")

        # Tarkistetaan tallennettu Parquet.
        parquet_data = con.read_parquet(
            str(parquet_file)
        )

        parquet_rows = parquet_data.count("*").fetchone()[0]
        parquet_columns = parquet_data.columns

        print(f"\nParquet-rivejä: {parquet_rows:,}")
        print(f"Parquet-sarakkeita: {len(parquet_columns)}")

        # Tarkistetaan row_id.
        row_id_check = con.execute(
            """
            SELECT
                MIN(row_id),
                MAX(row_id),
                COUNT(DISTINCT row_id)
            FROM read_parquet(?)
            """,
            [str(parquet_file)]
        ).fetchone()

        min_id, max_id, unique_ids = row_id_check

        print("\n--- Tarkistuksen tulos ---")

        if csv_rows == parquet_rows:
            print("OK: Rivimäärät täsmäävät.")
        else:
            print("VIRHE: Rivimäärät eivät täsmää!")

        if csv_columns == parquet_columns:
            print("OK: Sarakkeet täsmäävät.")
        else:
            print("VIRHE: Sarakkeet eivät täsmää!")

        if (
            min_id == 0
            and max_id == csv_rows - 1
            and unique_ids == csv_rows
        ):
            print("OK: row_id on yksilöllinen ja jatkuva.")
        else:
            print("VIRHE: row_id-tarkistus epäonnistui!")

        print(
            f"\nCSV:     {csv_file.stat().st_size / (1024**3):.2f} GiB"
        )
        print(
            f"Parquet: {parquet_file.stat().st_size / (1024**3):.2f} GiB"
        )

except Exception as e:
    print(f"Muunnos tai tarkistus epäonnistui: {e}")
    raise
