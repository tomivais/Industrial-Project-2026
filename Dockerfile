FROM python:3.12-slim

# Estetään Pythonin stdout-bufferointi
ENV PYTHONUNBUFFERED=1

# Työkansio kontissa
WORKDIR /app

# Asenna järjestelmäriippuvuudet (tarvitaan pyarrowille)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Kopioi projektin tiedostot
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Portti Shiny-sovellukselle
EXPOSE 8080

# Komento konttia käynnistettäessä
CMD ["python", "main.py"]
