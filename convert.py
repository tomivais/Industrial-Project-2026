
from pathlib import Path
import duckdb

root_dir = Path(__file__).parent

csv_file = root_dir / "1. LONG_suction_HIGH_CONC_4ml_(and_3_times_resuction).csv"
parquet_file = root_dir / "data.parquet"

if not csv_file.is_file():
    print(f"CSV-tiedostoa ei löytynyt: {csv_file}")

else:
    try:
        with duckdb.connect() as con:
            print(f"Luetaan tiedostoa: {csv_file.name}")

            # Luetaan CSV
            csv_data = con.read_csv(
                str(csv_file),
                auto_detect=True,
                ignore_errors=False
            )

            csv_rows = csv_data.count("*").fetchone()[0]
            csv_columns = csv_data.columns

            print(f"CSV-rivejä: {csv_rows:,}")
            print(f"CSV-sarakkeita: {len(csv_columns)}")

            # Muunnetaan Parquet-muotoon
            csv_data.write_parquet(
                str(parquet_file),
            )

            # Tarkistetaan tallennettu Parquet
            parquet_data = con.read_parquet(str(parquet_file))

            parquet_rows = parquet_data.count("*").fetchone()[0]
            parquet_columns = parquet_data.columns

            print(f"\nParquet-rivejä: {parquet_rows:,}")
            print(f"Parquet-sarakkeita: {len(parquet_columns)}")

            # Verrataan tuloksia
            print("\n--- Tarkistuksen tulos ---")

            if csv_rows == parquet_rows:
                print("OK: Rivimäärät täsmäävät.")
            else:
                print("VIRHE: Rivimäärät eivät täsmää!")

            if csv_columns == parquet_columns:
                print("OK: Sarakkeet täsmäävät.")
            else:
                print("VIRHE: Sarakkeet eivät täsmää!")

            print(f"\nCSV:     {csv_file.stat().st_size / (1024**3):.2f} GiB")
            print(f"Parquet: {parquet_file.stat().st_size / (1024**3):.2f} GiB")

    except Exception as e:
        print(f"Muunnos tai tarkistus epäonnistui: {e}")
        raise
