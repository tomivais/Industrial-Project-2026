from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parent

CSV_FILE = ROOT / "1. LONG_suction_HIGH_CONC_4ml_(and_3_times_resuction).csv"
PARQUET_FILE = ROOT / "data.parquet"

SRC = ROOT / "src"


# Muunnetaan vain, jos Parquet puuttuu
# tai CSV on uudempi kuin Parquet.

if (
    not PARQUET_FILE.exists()
    or CSV_FILE.stat().st_mtime > PARQUET_FILE.stat().st_mtime
):
    print("Ajetaan convert.py...")

    subprocess.run(
        [sys.executable, str(ROOT / "convert.py")],
        check=True
    )
else:
    print("Parquet on ajan tasalla. Muunnosta ei tarvita.")


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