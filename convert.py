
from pathlib import Path
import duckdb

# Etsitään CSV-tiedosto
root_dir = Path(__file__).parent

csv_file = root_dir / "1. LONG_suction_HIGH_CONC_4ml_(and_3_times_resuction).csv"
parquet_file = root_dir / "data.parquet"

if not csv_file.is_file():
    print(f"CSV-tiedostoa ei löytynyt: {csv_file}")

else:
    print(f"Aloitetaan muunnos: {csv_file.name}...")

    with duckdb.connect() as con:
        # Luetaan CSV ja tunnistetaan sarakkeet automaattisesti
        relation = con.read_csv(
            str(csv_file),
            auto_detect=True,
            ignore_errors=False
        )

        # Tulostetaan tunnistetut sarakkeet ja tietotyypit
        print("Sarakkeet ja tietotyypit:")
        print(relation.types)

        # Tallennetaan Parquet-muotoon
        relation.write_parquet(
            str(parquet_file)
            )

    print("Muunnos valmis!")
    print(f"Tallennettu: {parquet_file}")
