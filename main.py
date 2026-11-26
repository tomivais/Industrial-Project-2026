from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parent

CSV_FILE = ROOT / "1. LONG_suction_HIGH_CONC_4ml_(and_3_times_resuction).csv"
PARQUET_FILE = ROOT / "data.parquet"

SRC = ROOT / "src"


# Muunnetaan vain, jos CSV löytyy ja:
# 1. Parquet puuttuu
# 2. CSV on uudempi kuin Parquet

if CSV_FILE.exists() and (
    not PARQUET_FILE.exists()
    or CSV_FILE.stat().st_mtime > PARQUET_FILE.stat().st_mtime
):
    print("Ajetaan convert.py...")

    subprocess.run(
        [sys.executable, str(ROOT / "convert.py")],
        check=True
    )

##Jos projekti juuressa ei ole a CSV-tiedostoa, niin ei yritetä ajaa convert.py:tä.
elif not CSV_FILE.exists():
    print("CSV-tiedostoa ei löydy. Ohitetaan muunnos.")

else:
    print("Parquet on ajan tasalla. Muunnosta ei tarvita.")


# Käynnistetään Shiny joka tapauksessa

print("Käynnistetään Shiny...")

try:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "shiny",
            "run",
            "--port", "8080",
            "--host", "localhost",
            str(SRC / "app.py"),
        ],
        check=True,
        cwd=SRC
    )

except KeyboardInterrupt:
    print("\nShiny pysäytetty.")