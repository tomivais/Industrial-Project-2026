# Industrial Project 2026

Shiny-sovellus mittaussignaalien tarkasteluun ja piikkien tunnistamiseen.

## Asennus

Asenna projektin juurihakemistossa tarvittavat kirjastot:

```powershell
py -m pip install -r requirements.txt
```

## Käynnistys

Käynnistä sovellus projektin juurihakemistosta:

```powershell
py -m shiny run --port 8080 --host localhost src/app.py
```

Avaa selaimessa [http://localhost:8080](http://localhost:8080). Sammuta sovellus painamalla `Ctrl+C`.

## Käyttö

1. Avaa **Signaalin analyysi** ja lataa CSV tiedostonvalitsimella tai pudottamalla tiedosto latausalueelle.
2. Valitse mittauskanava (`adc1` tai `adc2`) sekä analysoitavan jakson alkuaika ja pituus. Nuolinäppäimillä voi siirtyä jaksojen välillä.
3. Säädä piikin vähimmäiskorkeutta ja kokeile kohinanpoistoa päälle tai pois.
4. Avaa **Yhteenveto ja koko signaali** nähdäksesi koko aineiston yleiskuvan ja analyysin yhteenvedon. Valitun jakson tarkempi kuvaaja ja löydettyjen piikkien taulukko näkyvät sen alla.

Piikin vähimmäiskorkeuden oletusarvo on 10. Kohinanpoiston voi kytkeä päälle tai pois.

Sovellus olettaa näytteenottotaajuudeksi 50 000 näytettä sekunnissa. CSV muunnetaan analyysin ajaksi Parquet-muotoon. Jos projektin juuressa on `data.parquet`, sovellus käyttää sitä oletusaineistona.
