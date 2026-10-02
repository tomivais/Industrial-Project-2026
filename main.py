from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parent

print("Ajetaan convert.py...")
subprocess.run(
    [sys.executable, str(ROOT / "convert.py")],
    check=True
)

print("Käynnistetään Shiny...")
try:

    subprocess.run(
        [
            sys.executable,
            "-m",
            "shiny",
            "run",
            "--reload",
            "--port",
            "8080",
            str(ROOT / "src" / "app.py"),
        ],
        check=True
    )

except KeyboardInterrupt:
    print("\nShiny pysäytetty.")