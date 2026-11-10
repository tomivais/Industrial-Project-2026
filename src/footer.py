from shiny import ui


def footer():
    return ui.tags.footer(
        ui.div(
            ui.div(
                ui.strong("Industrial Project"),
                ui.span("Demo application"),
                class_="footer-top"
            ),

            ui.hr(),

            ui.p("Industrial Project app"),

            ui.p(
                "© 2026 Industrial Project-Group 5",
                class_="footer-copyright"
            ),

            class_="footer-content"
        ),
        class_="app-footer"
    )