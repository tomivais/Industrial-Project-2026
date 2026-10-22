# Industrial-Project-2026

Tässä olisi ehdotelma pohja projektillemme. 
Repo pitää käytännössä sisällään:
- convert.py-Muuntaa CSV‑tiedoston Parquet‑muotoon ja tekee tarvittavat esikäsittelyt.
- main.py-Ajaa ensin convert.py:n ja käynnistää sen jälkeen Shiny‑sovelluksen.
-  src/app.py-Varsinainen Shiny‑sovellus, joka visualisoi Parquet‑tiedoston sisällön.
- requirements.txt-Lista Python‑kirjastoista, jotka tulee asentaa ennen projektin ajamista.

convert.py etsii CSV‑tiedostoa tietyllä nimellä, joten muokkaa sinne valitsemasi tiedoston nimi.
Eli kohtaan:
`csv_files = list(
    root_dir.glob(
        "1. LONG_suction_HIGH_CONC_4ml_(and_3_times_resuction).csv"
    )
)`

📦 Riippuvuuksien asennus
Asenna projektin kirjastot komennolla:
`pip install -r requirements.txt`


Kun kirjastot ja riippuvuuden on asennettu ja kyseiset muokkaukset on tehty, projektin voi ajaa antamalla terminaaliin komennon 
- py main.py. 

▶️ Projektin käynnistäminen
Kun: 
- CSV‑tiedosto on projektin juuressa

- convert.py:n tiedostonimi on päivitetty

- kirjastot on asennettu

Voit käynnistää projektin komennolla:
`py main.py`

Shiny‑sovellus käynnistyy porttiin 8080, ja sen voi avata selaimessa:
[http://localhost:8080](http://localhost:8080)
