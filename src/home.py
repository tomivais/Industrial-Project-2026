from shiny import ui

def home_ui():
    return ui.div(
        ui.h2("Etusivu"),

        ui.card(
            ui.card_header("Tervetuloa signaalianalyysin sovellukseen"),

            ui.p(
                "- Tässä projektissa saimme tehtäväksemme kehittää sovelluksen, "
                "jonka avulla signaalidataa voidaan analysoida signaalissa "
                "esiintyvien poikkeamien havaitsemiseksi."
            ),
            ui.p(
                "- Sovelluksen oletusaineistona on Marginumin laitteella "
                "mitattua CSV-muotoista signaalidataa."
            ),
            ui.p(
                "- Voit kuitenkin käyttäjänä ladata sovellukseen myös oman "
                "CSV-muotoisen datasi analysoitavaksi."
            ),
            class_="welcome-card"
        ),

        ui.card(
            ui.card_header("Kuinka aloitan analysoimisen?"),

            ui.markdown(
                "Siirry **Signaalin analyysi** -välilehdelle, niin voit aloittaa työskentelyn."
            ),
            class_="start-card"
        )
    )