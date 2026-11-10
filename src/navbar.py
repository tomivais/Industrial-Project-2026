
from pathlib import Path
from shiny import ui


# CSS sijaitsee samassa kansiossa kuin navbar.py
SOURCE_DIR = Path(__file__).resolve().parent


def navbar(home, app2):
    return ui.page_navbar(
        ui.nav_panel("Etusivu", home),
        ui.nav_panel("Signaalin analyysi", app2),

        title="Industrial Project",
        id="section",

        header=ui.include_css(
            str(SOURCE_DIR / "styles.css")
        ),
    )
