from shiny import ui


def home_ui():
    return ui.div(
        ui.h2("Etusivu"),
        ui.p("Tervetuloa signaalianalyysin sovellukseen."),
    )