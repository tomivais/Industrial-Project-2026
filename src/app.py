
from shiny import App, ui

from navbar import navbar
from footer import footer

from home import home_ui
from app2 import app2_ui, app2_server


app_ui = ui.div(
    navbar(
        home=home_ui(),
        app2=app2_ui(),
    ),

    footer(),

    class_="app-shell"
)

def server(input, output, session):
    app2_server(input, output, session)


app = App(app_ui, server)
