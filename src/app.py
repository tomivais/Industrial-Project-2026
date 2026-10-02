from shiny.express import input, render, ui

ui.page_opts(title='Industrial Project 2026')

ui.input_slider('n', 'Valitse arvo:', min=1, max=100, value=50)

@render.text
def txt():
    return f'Valittu arvo: {input.n()}'
